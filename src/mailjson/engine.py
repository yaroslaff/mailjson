"""Log mechanics: syslog headers, timestamps, and the open/close state machine.

Nothing here knows anything about Postfix. What a log line means is decided
entirely by the rules in `config.py`.
"""

from __future__ import annotations

import gzip
import re
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import IO, TextIO

# Both syslog templates in one expression: RFC3339 (rsyslog's RSYSLOG_FileFormat,
# the default on recent Debian/Ubuntu) and traditional BSD (RFC3164).
HEADER_RE = re.compile(
    r"^(?P<time>\d{4}-\d\d-\d\dT[\d:.]+(?:[+-]\d\d:\d\d|Z)|[A-Z][a-z]{2}\s+\d+ \d\d:\d\d:\d\d) "
    r"(?P<host>\S+) "
    r"(?P<program>[^\s\[:]+)(?:\[\d+\])?: "
    r"(?P<body>.*)$"
)

# A leap year stands in while parsing, so that "Feb 29" survives until the
# real year is known; strptime warns about a missing year otherwise.
BSD_TIME_FMT = "%Y %b %d %H:%M:%S"
BSD_PLACEHOLDER_YEAR = 1904

Groups = dict[str, str | None]


@dataclass
class Rule:
    """One log line worth reacting to.

    `pattern` is matched against the message body, i.e. what follows the
    ``queue_id: `` prefix. `daemon` of None matches any daemon.

    By default every named group is copied into the record under its own name;
    groups that did not participate in the match are skipped. A rule that has to
    do more than assign — accumulate a list, fill a map — supplies `apply`.
    """

    name: str
    pattern: str
    daemon: str | None = None
    closes: bool = False
    apply: Callable[[dict, Groups], None] | None = None
    regex: re.Pattern[str] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.regex = re.compile(self.pattern)


@dataclass(frozen=True)
class Event:
    """A parsed log line that carries a queue id."""

    time: datetime
    host: str
    instance: str
    daemon: str
    queue_id: str
    body: str


class YearTracker:
    """Supplies the year that BSD syslog lines leave out.

    The log ends at the file's mtime, so that is where the year comes from. Two
    corrections keep a file that spans New Year honest: one at the start, when
    the first record is later in the year than the mtime, and one mid-file, when
    the month jumps backwards.
    """

    def __init__(self, mtime: datetime) -> None:
        self._year = mtime.year
        self._mtime_month = mtime.month
        self._prev_month: int | None = None

    def year_for(self, month: int) -> int:
        if self._prev_month is None:
            if month > self._mtime_month:
                self._year -= 1
        elif month < self._prev_month:
            self._year += 1
        self._prev_month = month
        return self._year


def parse_time(raw: str, years: YearTracker) -> datetime:
    """Turn a syslog timestamp into an aware datetime.

    An RFC3339 stamp carries its own offset. A BSD stamp carries neither year
    nor zone: the year comes from `years`, and the time is read as local to this
    machine — `astimezone` resolves the offset against the date of the record
    itself, so DST is handled without us thinking about it.
    """
    if raw[4] == "-":  # 2026-09-06T...
        return datetime.fromisoformat(raw)
    naive = datetime.strptime(f"{BSD_PLACEHOLDER_YEAR} {raw}", BSD_TIME_FMT)
    return naive.replace(year=years.year_for(naive.month)).astimezone()


def split_program(program: str) -> tuple[str, str]:
    """`postfix/submission/smtpd` -> instance `postfix`, daemon `smtpd`."""
    parts = program.split("/")
    return parts[0], parts[-1]


def parse_line(line: str, queue_id_re: re.Pattern[str], years: YearTracker) -> Event | None:
    """Parse one log line, or return None if it is of no interest to us."""
    header = HEADER_RE.match(line)
    if header is None:
        return None
    tagged = queue_id_re.match(header["body"])
    if tagged is None:  # no queue id: NOQUEUE, statistics, connect/disconnect...
        return None
    instance, daemon = split_program(header["program"])
    return Event(
        time=parse_time(header["time"], years),
        host=header["host"],
        instance=instance,
        daemon=daemon,
        queue_id=tagged["queue_id"],
        body=tagged["body"],
    )


class Engine:
    """Assembles messages from log lines, one record per queue id.

    A record is keyed by (host, instance, queue_id): a queue id is unique only
    within one Postfix instance, and is recycled over time. Since a closed
    record leaves memory immediately, a later reuse of the same id simply starts
    a new message.
    """

    def __init__(self, rules: Iterable[Rule], new_record: Callable[[], dict]) -> None:
        self._new_record = new_record
        self._by_daemon: dict[str | None, list[Rule]] = {}
        for rule in rules:
            self._by_daemon.setdefault(rule.daemon, []).append(rule)
        self._any_daemon = self._by_daemon.get(None, [])
        self._cache: dict[str, list[Rule]] = {}
        self._open: dict[tuple[str, str, str], dict] = {}

    def _rules_for(self, daemon: str) -> list[Rule]:
        rules = self._cache.get(daemon)
        if rules is None:
            rules = self._cache[daemon] = self._by_daemon.get(daemon, []) + self._any_daemon
        return rules

    def feed(self, event: Event) -> dict | None:
        """Apply one event; returns the record if this event closed a message.

        Every rule that matches is applied, not just the first one, so a line
        carrying two independent facts is covered by two independent rules.
        """
        matches = [
            (rule, match)
            for rule in self._rules_for(event.daemon)
            if (match := rule.regex.search(event.body)) is not None
        ]
        if not matches:
            return None

        key = (event.host, event.instance, event.queue_id)
        record = self._open.get(key)
        if record is None:
            record = {
                "queue_id": event.queue_id,
                "host": event.host,
                "instance": event.instance,
                "first_seen": event.time.isoformat(),
                **self._new_record(),
            }
            self._open[key] = record
        record["last_seen"] = event.time.isoformat()

        for rule, match in matches:
            groups = match.groupdict()
            if rule.apply is not None:
                rule.apply(record, groups)
            else:
                record.update({k: v for k, v in groups.items() if v is not None})

        if any(rule.closes for rule, _ in matches):
            del self._open[key]
            record["closed"] = True
            return record
        return None

    def flush(self) -> Iterator[dict]:
        """Messages we never saw the end of: cut off by rotation, or still queued."""
        for record in self._open.values():
            record["closed"] = False
            yield record
        self._open.clear()


def open_log(path: str) -> tuple[IO[str], datetime]:
    """Open a log file (plain or gzipped) and report the mtime its years come from."""
    stat = Path(path).stat()
    mtime = datetime.fromtimestamp(stat.st_mtime)
    if path.endswith(".gz"):
        return gzip.open(path, "rt", errors="replace"), mtime
    return open(path, errors="replace"), mtime


def read_records(
    paths: Iterable[str],
    rules: Iterable[Rule],
    new_record: Callable[[], dict],
    queue_id_re: re.Pattern[str],
    stdin: TextIO | None = None,
) -> Iterator[dict]:
    """Run the whole pipeline over a list of files, yielding finished records.

    Several files are read as a single stream, so a message torn in half by log
    rotation is stitched back together — as long as the older file comes first.
    """
    engine = Engine(rules, new_record)
    for path in paths:
        if path == "-":
            if stdin is None:
                raise ValueError("no stdin provided")
            stream, mtime = stdin, datetime.now()
        else:
            stream, mtime = open_log(path)
        years = YearTracker(mtime)
        try:
            for line in stream:
                event = parse_line(line, queue_id_re, years)
                if event is None:
                    continue
                record = engine.feed(event)
                if record is not None:
                    yield record
        finally:
            if stream is not stdin:
                stream.close()
    yield from engine.flush()
