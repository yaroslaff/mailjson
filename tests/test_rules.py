"""The Postfix rules in config.py, fed with real lines from logs/."""

from datetime import datetime

import pytest

from mailjson.config import QUEUE_ID_RE, RULES, new_record, worst
from mailjson.engine import Engine, YearTracker, parse_line

DELIVERED = """\
2026-09-06T12:29:38.885813+02:00 projectmayhem postfix/pickup[61794]: D8381A3220: uid=0 from=<newsletter@example.com>
2026-09-06T12:29:38.886182+02:00 projectmayhem postfix/cleanup[62319]: D8381A3220: message-id=<A1B2C3D4E5@example.com>
2026-09-06T12:29:38.890048+02:00 projectmayhem postfix/qmgr[48588]: D8381A3220: from=<newsletter@example.com>, size=5550, nrcpt=1 (queue active)
2026-09-06T12:29:40.978481+02:00 projectmayhem postfix/smtp[62331]: D8381A3220: to=<walter@example.org>, relay=mx.example.org[192.0.2.10]:25, delay=2.1, delays=0.01/0.03/0.19/1.9, dsn=2.6.0, status=sent (250 2.6.0 <A1B2C3D4E5@example.com> [InternalId=1234567890] 17373 bytes in 0.251, 67.338 KB/sec Queued mail for delivery)
2026-09-06T12:29:40.979259+02:00 projectmayhem postfix/qmgr[48588]: D8381A3220: removed
"""

BOUNCED = """\
2026-09-06T05:59:31.592392+02:00 projectmayhem postfix/pickup[56583]: 9082BA31EB: uid=1001 from=<postmaster@example.com>
2026-09-06T05:59:31.602444+02:00 projectmayhem postfix/qmgr[48588]: 9082BA31EB: from=<postmaster@example.com>, size=1728, nrcpt=1 (queue active)
2026-09-06T05:59:31.616471+02:00 projectmayhem postfix/local[57167]: 9082BA31EB: to=<info@example.com>, relay=local, delay=0.03, delays=0.02/0.01/0/0.01, dsn=5.1.1, status=bounced (unknown user: "info")
2026-09-06T05:59:31.621563+02:00 projectmayhem postfix/qmgr[48588]: 9082BA31EB: removed
"""

TWO_RECIPIENTS = """\
Sep  6 01:04:31 mx postfix/smtpd[13497]: A53E66054A: client=mx[127.0.0.1]
Sep  6 01:04:31 mx postfix/qmgr[2666]: A53E66054A: from=<sender@gmail.com>, size=312799, nrcpt=2 (queue active)
Sep  6 01:04:31 mx opendkim[911]: A53E66054A: s=20251104 d=gmail.com SSL
Sep  6 01:04:32 mx postfix/pipe[13704]: A53E66054A: to=<info@example.net>, relay=dovecot, delay=0.86, delays=0.09/0/0/0.76, dsn=2.0.0, status=sent (delivered via dovecot service)
Sep  6 01:04:33 mx postfix/pipe[13950]: A53E66054A: to=<vacation@autoreply.example.net>, orig_to=<info@example.net>, relay=vacation, delay=2.2, delays=0.09/0.01/0/2.1, dsn=4.3.0, status=deferred (temporary failure)
Sep  6 01:04:34 mx postfix/qmgr[2666]: A53E66054A: removed
"""

HANDED_OVER = """\
Sep  6 01:04:31 mx postfix/smtpd[13497]: DEB3360567: client=mx[127.0.0.1]
Sep  6 01:04:31 mx postfix/smtp[13489]: DEB3360567: to=<info@example.net>, relay=127.0.0.1[127.0.0.1]:10024, delay=4, delays=1.4/0/0/2.7, dsn=2.0.0, status=sent (250 2.0.0 from MTA(smtp:[127.0.0.1]:10025): 250 2.0.0 Ok: queued as A53E66054A)
Sep  6 01:04:31 mx postfix/qmgr[2666]: DEB3360567: removed
"""

RETRIED = """\
Sep  6 00:21:43 mx postfix/smtp[11480]: 515A560CB0: to=<retry@example.net>, relay=none, delay=227253, delays=227163/0.16/90/0, dsn=4.4.1, status=deferred (connect to mail.example.net[203.0.113.7]:25: Connection timed out)
Sep  6 04:21:43 mx postfix/smtp[11480]: 515A560CB0: to=<retry@example.net>, relay=mail.example.net[203.0.113.7]:25, delay=241, delays=240/0.1/0.4/0.5, dsn=2.0.0, status=sent (250 OK)
Sep  6 04:21:44 mx postfix/qmgr[2666]: 515A560CB0: removed
"""


def run(text):
    """Feed a message's worth of log lines through the real rules."""
    engine = Engine(RULES, new_record)
    years = YearTracker(datetime(2026, 9, 7))
    records = []
    for line in text.splitlines(keepends=True):
        event = parse_line(line, QUEUE_ID_RE, years)
        if event is not None and (record := engine.feed(event)) is not None:
            records.append(record)
    records.extend(engine.flush())
    return records


def test_a_delivered_message():
    (record,) = run(DELIVERED)
    assert record == {
        "queue_id": "D8381A3220",
        "host": "projectmayhem",
        "instance": "postfix",
        "first_seen": "2026-09-06T12:29:38.885813+02:00",
        "last_seen": "2026-09-06T12:29:40.979259+02:00",
        "status": "sent",
        "recipients": ["walter@example.org"],
        "delivery": {"walter@example.org": "sent"},
        "from": "newsletter@example.com",
        "message_id": "A1B2C3D4E5@example.com",
        "closed": True,
    }


def test_a_bounced_message():
    (record,) = run(BOUNCED)
    assert record["status"] == "bounced"
    assert record["delivery"] == {"info@example.com": "bounced"}
    assert "message_id" not in record  # this one never passed through cleanup


def test_two_recipients_take_the_worst_outcome():
    (record,) = run(TWO_RECIPIENTS)
    assert record["recipients"] == ["info@example.net", "vacation@autoreply.example.net"]
    assert record["delivery"] == {"info@example.net": "sent", "vacation@autoreply.example.net": "deferred"}
    assert record["status"] == "deferred"


def test_a_retried_recipient_is_listed_once_with_its_final_outcome():
    (record,) = run(RETRIED)
    assert record["recipients"] == ["retry@example.net"]
    assert record["status"] == "sent"


def test_a_handover_keeps_the_new_queue_id():
    (record,) = run(HANDED_OVER)
    assert record["queued_as"] == "A53E66054A"
    assert record["status"] == "sent"


def test_an_ordinary_message_has_no_queued_as():
    (record,) = run(DELIVERED)
    assert "queued_as" not in record


def test_an_empty_sender_stays_empty():
    lines = "Sep  6 00:20:13 mx postfix-ases/qmgr[2670]: 515A560CB0: from=<>, size=33133, nrcpt=1 (queue active)\n"
    (record,) = run(lines)
    assert record["from"] == ""


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (["sent"], "sent"),
        (["sent", "sent"], "sent"),
        (["sent", "deferred"], "deferred"),
        (["sent", "bounced", "deferred"], "bounced"),
        (["sent", "expired"], "expired"),  # never seen before: assume the worst
    ],
)
def test_worst_outcome(statuses, expected):
    assert worst(statuses) == expected
