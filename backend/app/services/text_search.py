"""
CJK-aware text preparation for the post search index.

SQLite's FTS5 ``unicode61`` tokenizer breaks on non-letters, and every Han
character is a letter to it -- so a run of Chinese text is one single token:
``中文测试`` is indexed as the term ``中文测试`` and nothing else. Prefix search
then finds that post from ``中文`` but never from ``测试``, which makes every
script written without spaces between words (Chinese, Japanese) effectively
searchable only from its first character.

The fix is to make each CJK character its own token, on both sides of the index:

- :func:`segment_for_index` spaces the characters out before the text is
  indexed. It feeds ``Post.search_text``, which is the column ``posts_fts``
  indexes (see ``app/db/fts.py``).
- :func:`build_match` segments the query the same way and wraps each run in an
  FTS5 *phrase*, so its characters must appear adjacent and in order.

That pairing is the whole contract, and it is why the two functions live
together: a phrase over per-character tokens is exactly substring search. ``测试``
matches ``中文测试``; ``试中`` does not; ``中文测试`` still matches itself. Latin
text is untouched -- whole words, matched as prefixes, exactly as before.
"""

import re

# Characters written without spaces between words: Han (unified ideographs, the
# extension planes, radicals and compatibility forms) plus Japanese kana. Korean
# is deliberately left out -- Hangul is space-delimited, so whole-word tokens and
# prefix matching already suit it, and splitting it per syllable would only blur
# its ranking.
_CJK = (
    "⺀-⻿"  # CJK radicals supplement
    "々〇"  # 々 iteration mark, 〇 ideographic number zero
    "぀-ヿ"  # hiragana + katakana
    "ㇰ-ㇿ"  # katakana phonetic extensions
    "㐀-䶿"  # CJK unified ideographs extension A
    "一-鿿"  # CJK unified ideographs
    "豈-﫿"  # CJK compatibility ideographs
    "ｦ-ﾟ"  # halfwidth katakana
    "\U00020000-\U0003ffff"  # CJK unified ideographs extensions B and later
)

_CJK_CHAR = re.compile(f"[{_CJK}]")

# Split the query into word chunks, dropping every FTS5 operator character in the
# process (quotes, parentheses, ``*``, ``:``, ``-`` ...). This is what keeps a raw
# user string from being interpreted -- or misparsed -- as an FTS match
# expression, and it is bound as a parameter besides. ``\w`` covers CJK too.
_CHUNK_RE = re.compile(r"\w+", re.UNICODE)

# Within a chunk: one CJK character is one term, and each run of everything else
# (a Latin word, a number) stays whole.
_TERM_RE = re.compile(f"[{_CJK}]|[^{_CJK}]+")


def segment_for_index(text: str) -> str:
    """
    Return ``text`` with every CJK character surrounded by spaces, so FTS5
    indexes it as its own term.

    ``"中文测试"`` -> ``" 中  文  测  试 "``. Nothing else is touched: the
    tokenizer still handles Latin words, punctuation and case folding, and the
    added whitespace only ever creates token boundaries.
    """
    return _CJK_CHAR.sub(lambda match: f" {match.group(0)} ", text or "")


def build_match(query: str) -> str | None:
    """
    Turn a user query into a safe FTS5 MATCH string, or ``None`` if it has no
    usable token (in which case the caller returns an empty page).

    Chunks are ANDed (space), so "fast api" matches posts containing both. A
    Latin chunk becomes a prefix term (``"api"*``), which is what makes search
    type-ahead friendly; a chunk holding CJK becomes a phrase over its
    per-character terms (``"中 文"``), which is substring search.
    """
    chunks = _CHUNK_RE.findall(query or "")
    if not chunks:
        return None
    return " ".join(_chunk_to_phrase(chunk) for chunk in chunks)


def _chunk_to_phrase(chunk: str) -> str:
    """One word chunk as an FTS5 phrase, prefixed when it ends in Latin text."""
    terms = _TERM_RE.findall(chunk)
    # Double-quoting is what makes the phrase a phrase; a literal quote inside is
    # escaped by doubling. ``_CHUNK_RE`` cannot produce one, but the escaping
    # keeps this correct if the chunking is ever loosened.
    phrase = " ".join(term.replace('"', '""') for term in terms)
    # A trailing Latin term is the word the user is still typing, so match it as
    # a prefix. A trailing CJK character is already a whole token -- a prefix
    # there would mean nothing.
    star = "" if _CJK_CHAR.match(terms[-1]) else "*"
    return f'"{phrase}"{star}'
