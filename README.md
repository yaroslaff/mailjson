# mailjson

Turns a Postfix `mail.log` into JSON records: **one message, one record**.

```console
$ mailjson /var/log/mail.log | head -1
{"queue_id": "D8381A3220", "host": "projectmayhem", "instance": "postfix",
 "first_seen": "2026-09-06T12:29:38.885813+02:00", "first_seen_int": 20260906,
 "last_seen": "2026-09-06T12:29:40.979259+02:00",
 "from": "newsletter@example.com", "fdomain": "example.com",
 "message_id": "A1B2C3D4E5@example.com",
 "recipients": ["walter@example.org"], "rdomains": ["example.org"],
 "delivery": {"walter@example.org": "sent"},
 "status": "sent", "closed": true}
```

Both syslog timestamp formats are understood — RFC3339 (`2026-09-06T12:29:38.885813+02:00`)
and traditional BSD (`Sep  6 00:00:51`) — so logs from differently configured servers
can be fed to the same command.

## Install

```console
pipx install git+https://github.com/yaroslaff/mailjson.git
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
- `first_seen_int` is the day a message appeared as a plain integer (`20260906`), so
  records can be bucketed or filtered by date without parsing anything.
- `fdomain` and `rdomains` are the domains of the sender and of the recipients, lowercased;
  `rdomains` is deduplicated, so counting mail by destination domain needs no parsing.
- `status` is the worst outcome among the recipients: `sent` only when every one of
  them got it, otherwise `deferred` or `bounced`. `unknown` means no delivery attempt
  was logged — normally a message still sitting in the queue.
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
