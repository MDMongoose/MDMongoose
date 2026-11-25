"""UIDs: "MDM-" + base36 of a number. 0..100 000 are reserved for seeded nodes."""

ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
RESERVED_MAX = 100_000
PREFIX = "MDM-"


def to_uid(num: int) -> str:
    if num < 0:
        raise ValueError("uid number must be >= 0")
    if num == 0:
        return PREFIX + "0"
    out = []
    while num:
        num, rem = divmod(num, 36)
        out.append(ALPHABET[rem])
    return PREFIX + "".join(reversed(out))


def from_uid(uid: str) -> int:
    if not isinstance(uid, str) or not uid.upper().startswith(PREFIX):
        raise ValueError(f"not an MDM uid: {uid!r}")
    return int(uid[len(PREFIX):], 36)


def is_uid(text: str) -> bool:
    try:
        from_uid(text.strip())
        return True
    except (ValueError, AttributeError):
        return False


def is_reserved(num: int) -> bool:
    return num <= RESERVED_MAX
