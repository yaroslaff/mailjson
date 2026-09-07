"""Turn a Postfix mail.log into JSON records, one per message."""

from .engine import Engine, Event, Rule, read_records

__all__ = ["Engine", "Event", "Rule", "read_records"]
__version__ = "0.2.0"
