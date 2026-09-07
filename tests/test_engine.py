from datetime import datetime

import pytest

from mailjson.config import QUEUE_ID_RE, RULES, new_record
from mailjson.engine import Engine, Rule, YearTracker, parse_line, parse_time, split_program

ISO_LINE = (
    "2026-09-06T12:29:38.885813+02:00 projectmayhem postfix/pickup[61794]: "
    "D8381A3220: uid=0 from=<newsletter@example.com>\n"
)
BSD_LINE = "Sep  7 09:30:28 mx postfix-ases/smtpd[15338]: 183DA60568: client=mx[127.0.0.1]\n"


@pytest.fixture
def years():
    return YearTracker(datetime(2026, 9, 7, 12, 0, 0))


def parse(line, years):
    return parse_line(line, QUEUE_ID_RE, years)


def test_parses_iso_header(years):
    event = parse(ISO_LINE, years)
    assert (event.host, event.instance, event.daemon) == ("projectmayhem", "postfix", "pickup")
    assert event.queue_id == "D8381A3220"
    assert event.body == "uid=0 from=<newsletter@example.com>"
    assert event.time.isoformat() == "2026-09-06T12:29:38.885813+02:00"


def test_parses_bsd_header(years):
    event = parse(BSD_LINE, years)
    assert (event.host, event.instance, event.daemon) == ("mx", "postfix-ases", "smtpd")
    assert event.queue_id == "183DA60568"
    # no zone in the log: read as local time, offset resolved for that date
    assert event.time == datetime(2026, 9, 7, 9, 30, 28).astimezone()


@pytest.mark.parametrize(
    ("program", "expected"),
    [
        ("postfix/qmgr", ("postfix", "qmgr")),
        ("postfix-ases/smtp", ("postfix-ases", "smtp")),
        ("postfix/submission/smtpd", ("postfix", "smtpd")),
    ],
)
def test_splits_program(program, expected):
    assert split_program(program) == expected


def test_skips_lines_without_a_queue_id(years):
    noqueue = (
        "Sep  6 00:02:41 mx postfix/smtpd[10356]: NOQUEUE: reject: RCPT from x[1.2.3.4]: "
        "554 5.7.1 <spammer@example.net>: Relay access denied; from=<spammer@example.net> to=<spammer@example.net>\n"
    )
    assert parse(noqueue, years) is None
    assert parse("Sep  6 00:00:51 mx postfix/smtpd[9760]: connect from unknown[1.2.3.4]\n", years) is None
    assert parse("not a syslog line at all\n", years) is None


class TestYearTracker:
    def test_takes_the_year_from_the_mtime(self):
        assert YearTracker(datetime(2026, 9, 7)).year_for(9) == 2026

    def test_bumps_the_year_when_the_month_jumps_back(self):
        years = YearTracker(datetime(2027, 1, 3))
        assert years.year_for(12) == 2026  # log started before New Year
        assert years.year_for(1) == 2027

    def test_backdates_a_log_that_ends_early_in_the_year(self):
        # mtime says January, but the log opens in December: it began last year
        years = YearTracker(datetime(2027, 1, 3))
        assert years.year_for(12) == 2026


def test_bsd_time_gets_the_offset_of_its_own_date(years):
    winter = parse_time("Jan  6 00:00:51", YearTracker(datetime(2026, 1, 31)))
    summer = parse_time("Jul  6 00:00:51", YearTracker(datetime(2026, 7, 31)))
    assert winter.utcoffset() == datetime(2026, 1, 6).astimezone().utcoffset()
    assert summer.utcoffset() == datetime(2026, 7, 6).astimezone().utcoffset()


class TestEngine:
    def feed_all(self, engine, lines, years):
        return [record for line in lines if (record := engine.feed(parse(line, years))) is not None]

    def test_emits_a_record_when_the_message_is_removed(self, years):
        engine = Engine(RULES, new_record)
        lines = [
            ISO_LINE,
            "2026-09-06T12:29:40.979259+02:00 projectmayhem postfix/qmgr[48588]: D8381A3220: removed\n",
        ]
        (record,) = self.feed_all(engine, lines, years)
        assert record == {
            "queue_id": "D8381A3220",
            "host": "projectmayhem",
            "instance": "postfix",
            "first_seen": "2026-09-06T12:29:38.885813+02:00",
            "last_seen": "2026-09-06T12:29:40.979259+02:00",
            "closed": True,
        }
        assert list(engine.flush()) == []

    def test_flushes_what_never_closed(self, years):
        engine = Engine(RULES, new_record)
        assert self.feed_all(engine, [ISO_LINE], years) == []
        (record,) = list(engine.flush())
        assert record["queue_id"] == "D8381A3220"
        assert record["closed"] is False

    def test_reused_queue_id_starts_a_new_message(self, years):
        engine = Engine(RULES, new_record)
        removed = "2026-09-06T12:29:40.979259+02:00 projectmayhem postfix/qmgr[48588]: D8381A3220: removed\n"
        later = "2026-09-07T08:00:00.000000+02:00 projectmayhem postfix/pickup[1]: D8381A3220: uid=0 from=<sender@example.com>\n"
        first, = self.feed_all(engine, [ISO_LINE, removed], years)
        assert self.feed_all(engine, [later], years) == []
        (second,) = list(engine.flush())
        assert first["first_seen"] != second["first_seen"]

    def test_the_same_id_on_two_instances_stays_apart(self, years):
        engine = Engine(RULES, new_record)
        one = "Sep  7 09:30:28 mx postfix/smtpd[1]: 183DA60568: client=mx[127.0.0.1]\n"
        two = "Sep  7 09:30:28 mx postfix-ases/smtpd[2]: 183DA60568: client=mx[127.0.0.1]\n"
        assert self.feed_all(engine, [one, two], years) == []
        assert {r["instance"] for r in engine.flush()} == {"postfix", "postfix-ases"}

    def test_named_groups_land_in_the_record(self, years):
        rules = [
            Rule(name="from", daemon="pickup", pattern=r"uid=(?P<uid>\d+) from=<(?P<from>[^>]*)>"),
            Rule(name="removed", daemon="qmgr", pattern=r"^removed$", closes=True),
        ]
        engine = Engine(rules, new_record)
        engine.feed(parse(ISO_LINE, years))
        (record,) = list(engine.flush())
        assert record["uid"] == "0"
        assert record["from"] == "newsletter@example.com"

    def test_groups_that_did_not_match_are_left_out(self, years):
        rules = [Rule(name="from", daemon="pickup", pattern=r"uid=(?P<uid>\d+)( x=(?P<extra>\w+))?")]
        engine = Engine(rules, new_record)
        engine.feed(parse(ISO_LINE, years))
        (record,) = list(engine.flush())
        assert "extra" not in record

    def test_apply_replaces_the_default_assignment(self, years):
        def collect(record, groups):
            record.setdefault("uids", []).append(groups["uid"])

        rules = [Rule(name="from", daemon="pickup", pattern=r"uid=(?P<uid>\d+)", apply=collect)]
        engine = Engine(rules, new_record)
        engine.feed(parse(ISO_LINE, years))
        (record,) = list(engine.flush())
        assert record["uids"] == ["0"]
        assert "uid" not in record
