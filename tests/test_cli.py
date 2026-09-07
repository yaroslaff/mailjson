import gzip
import io
import json
import os
import time

import pytest

from mailjson.cli import main

LOG = """\
Sep  6 00:00:52 mx postfix/smtpd[10356]: 47C1C6054A: client=sender.example.com[198.51.100.5]
Sep  6 00:00:52 mx postfix/smtpd[9760]: connect from unknown[198.51.100.9]
Sep  6 00:00:56 mx postfix/qmgr[2666]: 47C1C6054A: removed
Sep  6 00:01:03 mx postfix/smtpd[10356]: 08BBF60567: client=mx[127.0.0.1]
"""


@pytest.fixture
def log(tmp_path):
    path = tmp_path / "mail.log"
    path.write_text(LOG)
    os.utime(path, (time.time(), time.mktime((2026, 9, 6, 12, 0, 0, 0, 0, -1))))
    return str(path)


def test_writes_jsonl(log, capsys):
    assert main([log]) == 0
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [(r["queue_id"], r["closed"]) for r in records] == [
        ("47C1C6054A", True),
        ("08BBF60567", False),  # never removed: flushed at the end of input
    ]


def test_writes_one_list(log, capsys):
    assert main(["--list", log]) == 0
    records = json.loads(capsys.readouterr().out)
    assert [r["queue_id"] for r in records] == ["47C1C6054A", "08BBF60567"]


def test_reads_gzip(tmp_path, capsys):
    path = tmp_path / "mail.log.1.gz"
    with gzip.open(path, "wt") as handle:
        handle.write(LOG)
    assert main([str(path)]) == 0
    assert len(capsys.readouterr().out.splitlines()) == 2


def test_reads_stdin(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(LOG))
    assert main(["-"]) == 0
    assert len(capsys.readouterr().out.splitlines()) == 2


def test_several_files_are_one_stream(tmp_path, capsys):
    older = tmp_path / "mail.log.1"
    newer = tmp_path / "mail.log"
    older.write_text("Sep  6 00:00:52 mx postfix/smtpd[1]: 47C1C6054A: client=mx[127.0.0.1]\n")
    newer.write_text("Sep  6 00:00:56 mx postfix/qmgr[2]: 47C1C6054A: removed\n")
    assert main([str(older), str(newer)]) == 0
    (record,) = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    # torn in half by log rotation, stitched back into one message
    assert record["closed"] is True
    assert record["first_seen"] < record["last_seen"]
