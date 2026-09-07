# mailjson

Turns a Postfix `mail.log` into JSON records: **one message, one record**.

```console
$ mailjson /var/log/mail.log | head -1
{"queue_id": "D8381A3220", "host": "projectmayhem", "instance": "postfix",
 "first_seen": "2026-09-06T12:29:38.885813+02:00",
 "last_seen": "2026-09-06T12:29:40.979259+02:00", "closed": true}
```

Both syslog timestamp formats are understood — RFC3339 (`2026-09-06T12:29:38.885813+02:00`)
and traditional BSD (`Sep  6 00:00:51`) — so logs from differently configured servers
can be fed to the same command.

## Install

```console
pipx install .
```

## Usage

```console
mailjson logs/mail.log.1 logs/mail.log > mail.jsonl   # JSONL, one record per line
mailjson -l logs/mail.log > mail.json                 # a single JSON list
cat mail.log | mailjson -                             # stdin
```

- Files are read **as one stream** in the order given, so pass the oldest first: a
  message split across a log rotation is then stitched back together.
- `.gz` files are recognised by their extension.
- Output order follows the moment a message left the queue, not the moment it arrived.
  Messages that never closed — still queued, or cut off at the end of the log — come
  last, marked `"closed": false`.
- BSD-format lines carry no year and no timezone: the year comes from the file's
  mtime, and the time is read as local to the machine running `mailjson`.

## Development

```console
pip install -e '.[dev]'
pytest
```

The parsing rules live in [`src/mailjson/config.py`](src/mailjson/config.py) — a list of
regexes and what to do with the fields they capture. The engine in
[`src/mailjson/engine.py`](src/mailjson/engine.py) knows nothing about Postfix, so adding
a field to the output means adding a rule, not changing the engine.

See [SPEC.md](SPEC.md) for the full specification and the reasoning behind it.
