"""What a Postfix log line means.

Everything specific to Postfix lives here — the queue id pattern, the rules, and
the shape of an empty record. Adding a field to the output is a matter of adding
a rule; the engine is not supposed to change for it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from .engine import Groups, Rule

# Postfix prefixes every message-related line with its queue id. Hex only, which
# also keeps NOQUEUE out: those lines are rejections that never reached a queue.
QUEUE_ID_RE = re.compile(r"^(?P<queue_id>[0-9A-F]{6,18}): (?P<body>.*)$")

# Worst outcome wins when a message has several recipients. Anything Postfix
# might invent that we have not seen ranks worst of all, so that a surprise is
# never quietly reported as success.
STATUS_RANK = {"sent": 0, "deferred": 1, "bounced": 2}
UNKNOWN_STATUS_RANK = 3


def worst(statuses: Iterable[str]) -> str:
    return max(statuses, key=lambda status: STATUS_RANK.get(status, UNKNOWN_STATUS_RANK))


def add_recipient(record: dict, groups: Groups) -> None:
    """Record one delivery attempt.

    The address is listed once however many attempts it takes, and the latest
    outcome wins: a recipient that was deferred and then delivered reads `sent`.
    """
    address = groups["to"]
    if address not in record["delivery"]:
        record["recipients"].append(address)
    record["delivery"][address] = groups["status"]
    record["status"] = worst(record["delivery"].values())


def new_record() -> dict:
    """The fields a record starts with, before any rule has fired."""
    return {"status": "unknown", "recipients": [], "delivery": {}}


RULES = [
    # A message appears: handed over locally by sendmail(1), or accepted over SMTP.
    Rule(name="pickup", daemon="pickup", pattern=r"^uid=\d+ from=<(?P<from>[^>]*)>"),
    Rule(name="accepted", daemon="smtpd", pattern=r"^client="),
    Rule(name="message_id", daemon="cleanup", pattern=r"^message-id=<(?P<message_id>[^>]*)>"),
    # The envelope sender, repeated on every queue run; empty for bounces.
    Rule(name="sender", daemon="qmgr", pattern=r"^from=<(?P<from>[^>]*)>, size="),
    # One delivery attempt. Any of smtp, local, virtual, pipe and discard makes them.
    Rule(
        name="delivery",
        pattern=r"^to=<(?P<to>[^>]*)>.*?, status=(?P<status>\w+)",
        apply=add_recipient,
    ),
    # Handed to another queue — a content filter, or a second postfix instance.
    # Kept as a plain field: the two halves stay two records, joinable by hand.
    Rule(name="queued_as", pattern=r"queued as (?P<queued_as>[0-9A-F]{6,18})\b"),
    # A message is gone: delivered, bounced or given up on — the queue file is unlinked.
    Rule(name="removed", daemon="qmgr", pattern=r"^removed$", closes=True),
]
