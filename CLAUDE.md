# mailjson

Turns a Postfix `mail.log` into JSON records, one per message. Read [SPEC.md](SPEC.md)
first — it carries the design decisions and the reasoning behind them.

## Layout

| file | holds |
|---|---|
| `src/mailjson/config.py` | everything Postfix-specific: queue id pattern, rules, empty record |
| `src/mailjson/engine.py` | syslog headers, timestamps, the open/close state machine |
| `src/mailjson/cli.py` | argument parsing and output |

## The one rule that matters

**Postfix knowledge belongs in `config.py`, mechanics in `engine.py`.** Adding a field to
the output must mean adding a `Rule`, never touching the engine. If a change seems to
require both, that is the signal to stop and reconsider the shape of `Rule`.

## Releasing

`__version__` in `src/mailjson/__init__.py` is the single source of truth — `pyproject.toml`
reads it through `[tool.hatch.version]`. **Bump it in every commit that gets pushed**, or
`pipx upgrade mailjson` sees the same version and does nothing. Patch level for a fix, minor
for a new field or flag.

## Working here

- Simplicity is the point. Prefer the boring version; a feature that needs a flag to stay
  out of the way probably should not exist. Deliberate omissions are listed in SPEC.md §5
  — `NOQUEUE`, cross-instance stitching, attempt history — do not add them back without
  being asked.
- No runtime dependencies. Standard library only.
- `logs/` holds real logs from three servers, one of them 21 MB. Read them with `head`,
  `grep -c`, `sort | uniq -c` — never load one into context whole.
- Tests: `PYTHONPATH=src pytest`. They must not depend on the machine's timezone; compare
  against `datetime(...).astimezone()` rather than a hardcoded offset.

## Checking against the real logs

```console
PYTHONPATH=src python3 -m mailjson.cli logs/mail.log.2 | wc -l   # records
grep -c ': removed' logs/mail.log.2                              # closed messages
```

The first number equals the second plus the records flushed with `"closed": false`.
