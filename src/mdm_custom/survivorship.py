"""Custom survivorship functions.

Signature: fn(pairs: list[tuple[source_name, value]]) -> (source_name, value) | None
`pairs` arrive in the Property Rule's Source Priority order; return the winning pair.
"""

from __future__ import annotations

import re

GENERIC_MAILBOXES = {"info", "office", "contact", "sales", "support", "hello", "admin", "mail", "noreply", "no-reply"}
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.I)


def valid_email_prefer_personal(pairs):
    """First syntactically valid email that is not a generic mailbox; else the first valid one."""
    valid = [p for p in pairs if p[1] and EMAIL.match(str(p[1]).strip())]
    for p in valid:
        if str(p[1]).split("@", 1)[0].lower() not in GENERIC_MAILBOXES:
            return p
    return valid[0] if valid else None


def longest_value(pairs):
    """The most detailed value (e.g. full street address)."""
    return max(pairs, key=lambda p: len(str(p[1] or "")), default=None)
