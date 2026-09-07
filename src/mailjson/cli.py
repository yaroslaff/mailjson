"""Command line entry point."""

from __future__ import annotations

import argparse
import json
import sys

from .config import QUEUE_ID_RE, RULES, new_record
from .engine import read_records


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mailjson",
        description="Turn a Postfix mail.log into JSON records, one per message.",
    )
    parser.add_argument(
        "files",
        nargs="+",
        metavar="FILE",
        help="log files, oldest first; plain or .gz; '-' reads stdin",
    )
    parser.add_argument(
        "-l",
        "--list",
        action="store_true",
        help="emit one JSON list instead of JSONL (holds every record in memory)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    records = read_records(args.files, RULES, new_record, QUEUE_ID_RE, stdin=sys.stdin)
    try:
        if args.list:
            json.dump(list(records), sys.stdout, indent=2)
            sys.stdout.write("\n")
        else:
            for record in records:
                sys.stdout.write(json.dumps(record) + "\n")
    except BrokenPipeError:  # piped into head, less, ...
        sys.stdout = None  # type: ignore[assignment]
        return 0
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
