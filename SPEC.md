# mailjson — specification

Turns a Postfix `mail.log` into JSON records: **one message, one record**.

Only the essentials about each message: addresses, times, outcome. Nothing else is our job.

---

## 1. Input

Three logs from three different mail servers (`logs/`), in two different syslog formats:

```
2026-09-06T12:29:38.885813+02:00 projectmayhem postfix/pickup[61794]: D8381A3220: uid=0 from=<newsletter@example.com>
Sep  7 09:30:28 mx postfix-ases/smtpd[15338]: 183DA60568: client=mx[127.0.0.1]
```

Only the **syslog header** differs, not Postfix. Postfix always writes the same thing: `queue_id: key=value, ...`. The prefix is added by the logging daemon according to its template:

| | ISO | BSD |
|---|---|---|
| rsyslog template | `RSYSLOG_FileFormat` (RFC3339) | `RSYSLOG_TraditionalFileFormat` (RFC3164) |
| example | `2026-09-06T12:29:38.885813+02:00` | `Sep  6 00:00:51` |
| resolution | microseconds | seconds |
| year | present | **absent** |
| timezone | offset present | **absent** |

The ISO format is the default on recent Debian/Ubuntu and what journald emits; BSD is the historical rsyslog default. Host `mx` is simply configured the old way.

**Consequence: one header regex is enough, not two sets of rules.** Verified against all three logs — 191,460 lines out of 191,460 parsed, no misses.

### Program name

The third header field comes in three shapes:

| in the log | instance | daemon | what it is |
|---|---|---|---|
| `postfix/qmgr` | `postfix` | `qmgr` | ordinary instance |
| `postfix-ases/smtp` | `postfix-ases` | `smtp` | second postfix instance (multi-instance) |
| `postfix/submission/smtpd` | `postfix` | `smtpd` | service name from `master.cf` |

Rule: **instance is the first component, daemon is the last**. Anything in between is discarded.

---

## 2. Time

- **ISO line** — the offset is taken from the line itself, as is.
- **BSD line** — no year, no zone. The year comes from the file's mtime; if the month jumps from December to January while reading, the year is bumped. For stdin, the current year. The zone is the **system zone of the machine running mailjson** — the assumption is that the log comes from that machine or from the same zone. The offset is resolved against the date of the record itself, so September lines get `+02:00` and January lines `+01:00`. During the ambiguous hour of a DST fall-back, the first (summer) reading is used.
- Output is always ISO 8601 with an offset; microseconds are preserved when the source had them.

---

## 3. Output record

```json
{
  "queue_id": "D8381A3220",
  "host": "projectmayhem",
  "instance": "postfix",
  "from": "newsletter@example.com",
  "fdomain": "example.com",
  "recipients": ["walter@example.org"],
  "rdomains": ["example.org"],
  "delivery": { "walter@example.org": "sent" },
  "status": "sent",
  "first_seen": "2026-09-06T12:29:38.885813+02:00",
  "first_seen_int": 20260906,
  "last_seen": "2026-09-06T12:29:40.979259+02:00",
  "closed": true
}
```

| field | meaning |
|---|---|
| `queue_id` | Postfix queue identifier |
| `host` | host from the syslog header |
| `instance` | postfix instance — `mx` runs two, each with its own queue ids |
| `from` | envelope sender; empty (`<>`) for bounces |
| `fdomain` | the sender's domain, lowercased. A string, not a list: there is only ever one sender |
| `recipients` | **a plain list of strings** — greppable and countable without unpacking structures |
| `rdomains` | the recipients' domains, lowercased and deduplicated: two addresses at gmail.com leave one entry. A local recipient with no domain (`to=<root>`) adds nothing |
| `delivery` | map of address to outcome. The value is always a string. The last outcome wins: `deferred` then `sent` leaves `sent` |
| `status` | worst-of summary: `bounced` > `deferred` > `sent`. `sent` only if every recipient got it. `unknown` when no outcome is visible |
| `first_seen` | time of the first line carrying this queue id |
| `first_seen_int` | the same day as an integer, `YYYYMMDD` — a number to group or filter by without parsing a timestamp |
| `last_seen` | time of the last line |
| `closed` | whether `qmgr: removed` was reached |
| `message_id` | the Message-ID header, as logged by `cleanup`; absent for messages that never passed through it |
| `queued_as` | present only for messages handed to another queue (see §5) |

---

## 4. Engine

Streaming, single pass, never holds the log in memory.

1. A line is parsed by the header regex into time, host, instance, daemon, body.
2. The queue id is taken from the body: `^(?P<qid>[0-9A-F]{6,18}): (?P<rest>.*)$`. No match, line silently skipped.
3. The body is matched against the rules (§6). **Every** rule that matches is applied, not just the first — one line often carries two unrelated facts, and each gets its own small rule. Nothing matches, line skipped.
4. A rule matched: the record for `(host, instance, queue_id)` is created if absent, then updated. `last_seen` always advances.
5. One of the matched rules is marked `closes`: the record is emitted and dropped from memory.
6. At end of input, every remaining record is emitted with `closed: false`.

**The record key is the triple `(host, instance, queue_id)`.** A queue id is unique only within an instance, and gets reused over time; since a closed record leaves memory, a later reappearance of the same id starts a new message.

**Output order follows the time a message closes**, not the time it appeared. Anything else would mean holding the whole log in memory. For messages that live for seconds the difference is invisible; a message that sat in the queue for a day surfaces far from its `first_seen`. Unclosed records come last.

Only unclosed messages stay in memory: deferred ones plus those cut off by rotation. For `logs/mail.log.2` that is a few thousand records — single-digit megabytes.

---

## 5. Deliberate simplifications

**`NOQUEUE` is ignored.** `mx` has 7,778 such lines — messages rejected during the SMTP dialogue, before ever entering the queue. They have no queue id, so there is nothing to assemble a message from. On top of that, 475 of them are temporary greylisting rejections: the sender almost always comes back, and the very same message would be counted twice.

**A message crossing two instances yields two records.** `logs/mail.log.2` contains 2,228 such handoffs:

```
postfix/smtp:      04929605AC: to=<x@gmail.com>, status=sent (250 2.0.0 Ok: queued as 183DA60568)
postfix-ases/smtp: 183DA60568: to=<x@gmail.com>, relay=mx.example.net...
```

Physically it is one message, but it passed through a queue twice. We do not stitch them: "one queue id, one record" stays free of exceptions, and the engine knows nothing about content filters. To find the other half by hand, the first record carries `queued_as: "183DA60568"` — an ordinary capture from the same line, with no linking logic behind it.

**No attempt history.** Only the last outcome per recipient.

---

## 6. Configuration

Regexes and rules live in one place — `config.py`. The engine knows nothing about Postfix.

```python
Rule(
    name="delivered",
    daemon="smtp",              # None = any daemon
    pattern=r"to=<(?P<to>[^>]*)>.*status=(?P<status>\w+)",
    closes=False,
    apply=None,                 # None = copy every named group into the record as is
)
```

- `pattern` is matched against the body **after** `queue_id: `.
- Every matching rule fires. Two rules may therefore read one line — `status=sent (... queued as A53E66054A)` is both a delivery attempt and a handover — and neither has to know about the other. When two rules do write the same field, the later one in the list wins.
- `apply(record, groups)` is an optional function for fields that accumulate (`recipients`, `delivery`). It is written in the same `config.py`.
- A new field means a new rule. The engine does not change — which is exactly the test of whether configuration is separated from mechanics.

---

## 7. CLI

```bash
mailjson logs/mail.log.2 logs/mail.log > mail.jsonl   # JSONL by default
mailjson -l logs/mail.log > mail.json                 # a single list (--list)
cat mail.log | mailjson -                             # stdin
```

- Several files are read **as one stream**, in argument order (oldest first) — messages torn apart by rotation stitch themselves back together.
- `.gz` is recognised by extension.
- Install: `pipx install git+https://github.com/yaroslaff/mailjson.git`

---

## 8. Stages

**Stage 1 — the engine (done).** `pyproject.toml`, the package, the CLI, tests. Three rules in the config: message appeared (`pickup`, `smtpd client=`) and message gone (`qmgr: removed`). Output carried `queue_id`, `host`, `instance`, `first_seen`, `last_seen`, `closed`.

**Stage 2 — the fields (done).** Four more rules bring `from`/`fdomain`, `message_id`, `recipients`/`rdomains`/`delivery`/`status` and `queued_as`.

Stage 2 did change the engine once, in the one way stage 1 had not settled: a line is now offered to every rule instead of only the first one that matches. Under first-match-wins, `to=<...> status=sent (... queued as ...)` would have forced delivery and handover into a single regex, and every later field on a shared line would have meant editing an existing rule instead of adding one. The rest of the engine was untouched.
