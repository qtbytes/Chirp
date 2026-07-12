"""
Extract ``#hashtag`` and ``@mention`` entities from post text.

Pure functions, so the write paths can persist the results and the tests can
check the parsing rules in isolation. Both return order-preserving, de-duplicated
lists.
"""

import re

# A hashtag body is Unicode word characters (so non-ASCII tags work); a mention
# body matches the username charset registration allows (alphanumeric +
# underscore, up to the 50-char username cap). Both must be preceded by a
# non-word character (or the start) so "email@host" or "c#" mid-word do not count.
_HASHTAG_RE = re.compile(r"(?<!\w)#(\w{1,140})", re.UNICODE)
_MENTION_RE = re.compile(r"(?<!\w)@([A-Za-z0-9_]{1,50})")


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def extract_hashtags(text: str) -> list[str]:
    """Return the lowercased hashtag bodies (without ``#``), de-duplicated."""
    return _dedupe(match.lower() for match in _HASHTAG_RE.findall(text or ""))


def extract_mention_usernames(text: str) -> list[str]:
    """
    Return the mentioned usernames (without ``@``), de-duplicated.

    Case is preserved as typed; resolution to a user is case-sensitive against
    the stored username, matching how the rest of the app looks users up.
    """
    return _dedupe(_MENTION_RE.findall(text or ""))
