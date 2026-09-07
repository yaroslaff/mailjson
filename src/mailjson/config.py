"""What a Postfix log line means.

Everything specific to Postfix lives here — the queue id pattern, the rules, and
the shape of an empty record. Adding a field to the output is a matter of adding
a rule; the engine is not supposed to change for it.
"""

from __future__ import annotations

import re

from .engine import Rule

# Postfix prefixes every message-related line with its queue id. Hex only, which
# also keeps NOQUEUE out: those lines are rejections that never reached a queue.
QUEUE_ID_RE = re.compile(r"^(?P<queue_id>[0-9A-F]{6,18}): (?P<body>.*)$")


def new_record() -> dict:
    """The fields a record starts with, before any rule has fired."""
    return {}


RULES = [
    # A message appears: handed over locally by sendmail(1), or accepted over SMTP.
    Rule(name="pickup", daemon="pickup", pattern=r"^uid="),
    Rule(name="accepted", daemon="smtpd", pattern=r"^client="),
    # A message is gone: delivered, bounced or given up on — the queue file is unlinked.
    Rule(name="removed", daemon="qmgr", pattern=r"^removed$", closes=True),
]
