"""
Semantic-position report: catches "every tag is present, but wrapped around
the wrong words".

verify.py checks count, content, nesting and order. It cannot catch this:
the source has {accent-gn}50%{/style} around a number, the target has the
same pair around something else. Counts match, order matches, every
mechanical check passes, and the file is wrong.

This module lists what each tag pair wraps in source and in target, side by
side, so a reviewer can scan for mismatches.

Public API:
  build_report(pairs, glossary=None) -> str        markdown table
  write_report(pairs, output_path, glossary=None) -> str
  extract_spans(text) -> [(signature, wrapped_text)]
  align_spans(src_spans, tgt_spans, glossary=None) -> rows

Pairing logic:
  - memoQ <ph> elements are resolved back to the original tag through their
    displaytext, then opening and closing tags are matched with a stack
  - pairs with the same signature align by order of appearance (the
    source's k-th accent-gn pair maps to the target's k-th), which
    tolerates ph reordering and ID reassignment

Five automatic flags, in priority order, at most one per row:
  1. number mismatch    the numbers inside the wrapped text differ
                        (the strongest signal that a span shifted)
  2. empty target span  the source wraps text, the target pair is empty;
                        the text got pushed outside the tags
  3. term mismatch      the source span hits a glossary term and the target
                        span lacks the approved translation. Longest match
                        wins, so a short entry cannot hijack a longer one
  4. span expanded      the target span contains an approved translation
                        whose source term is not inside the source span
  5. span density       Chinese characters : English content words falls
                        outside [0.35, 1.3]

Flags 3 and 4 need a glossary. Flag 4 also requires flag 5 to fire: only
when both length and terminology point at over-wrapping is it called
"expanded".

No flag does not mean pass. These five cover only what a machine can
decide; the remaining rows are for a human reading the table.
"""

import html
import re
from pathlib import Path

# Standalone tags that wrap nothing.
STANDALONE = {"br", "hr", "img"}


# ============================================================
# Parsing
# ============================================================

# Was `[\d.]+%?`, which treated English periods and ellipses as numbers:
# "Wait... what?" yielded ['...'], "He said. Then left." yielded ['.', '.'].
# On a real 525-tag file that produced 116 false "number mismatch" reports.
# Now requires at least one digit; decimals, versions and percentages still
# match.
NUMBER_RE = re.compile(r"\d+(?:\.\d+)*%?")

# Span-boundary checks. Two real segments that slipped through every gate
# motivated flags 4 and 5:
#   source ⟦c⟧当前队长⟦/c⟧ -> target current ⟦c⟧leader⟦/c⟧
#       (span shrank: "current" fell outside the tag)
#   source ⟦c⟧增加{*val*}⟦/c⟧技能点 -> target ⟦c⟧grants {*val*} Skill Points⟦/c⟧
#       (span expanded: "Skill Points" got pulled inside)
# Count, order and nesting were all legal, so verify.py cannot see them. The
# wrapped text had no numbers, and the term was not in the glossary, so the
# two older flags stayed silent too. The root cause is the same in both: the
# tag was placed by anchoring on one word (队长 / 增加), not on the two edges
# of the span. When English word order shifts, the edges drift with the
# neighbouring words.
#
# Variable placeholders must be stripped before counting words: in
# {*element*}系 -> {*element*} Element, "element" would be counted as an
# English content word and raise a false alarm.
VAR_RE = re.compile(r"\{\*[^}]*\*\}|\{[^}]*\}|%[sd]|\$\{[^}]*\}")

# Function words do not count as content words. Without this, auxiliary
# expansions such as 必定 -> "is guaranteed to" would be flagged in bulk
# (10 in one batch, every one a legitimate translation).
FUNCTION_WORDS = {
    "is", "are", "be", "was", "were", "been", "to", "the", "a", "an", "of",
    "and", "or", "for", "by", "will", "would", "has", "have", "had", "that",
    "it", "its", "in", "on", "at", "as", "with", "from", "this", "these",
}

# Normal range for (English content words) / (Chinese characters). Measured
# median: 0.50 across 75 paired spans of one real batch. Below the range the
# target lost words or the span shrank; above it the span swallowed something
# from outside the source tags. This is a heuristic; treat its flags as
# "look here", not as verdicts.
DENSITY_LO, DENSITY_HI = 0.35, 1.3


def _resolve_ph(text: str) -> str:
    """Replace each memoQ <ph> element with the original tag from its displaytext."""
    def repl(m):
        d = re.search(r'displaytext="([^"]*)"', m.group(0))
        return html.unescape(d.group(1)) if d else m.group(0)
    return re.sub(r"<ph\b[^>]*>.*?</ph>|<ph\b[^>]*/>", repl, text, flags=re.S)


def extract_spans(text: str) -> list:
    """Return [(signature, wrapped_text)]. Signature = the opening tag as written.

    Stack pairing: a closing tag pops the nearest opening tag of the same name.
    """
    resolved = _resolve_ph(text)
    tokens = re.split(r"(<[^>]+>)", resolved)
    stack = []   # [(signature, buffer index)]
    spans = []
    buffers = []
    for tok in tokens:
        if not tok:
            continue
        if tok.startswith("<"):
            name = tok.strip("<>/").split()[0].split("=")[0].rstrip('"')
            if tok.startswith("</"):
                for i in range(len(stack) - 1, -1, -1):
                    open_sig, buf_i = stack[i]
                    open_name = open_sig.strip("<>/").split()[0].split("=")[0].rstrip('"')
                    if open_name == name:
                        spans.append((open_sig, buffers[buf_i].strip()))
                        stack.pop(i)
                        break
            elif name.lower() in STANDALONE or tok.endswith("/>"):
                spans.append((tok, "(standalone tag, wraps nothing)"))
            else:
                stack.append((tok, len(buffers)))
                buffers.append("")
        else:
            for j in range(len(buffers)):
                if any(b == j for _, b in stack):
                    buffers[j] += tok
    # Unclosed opening tags are reported too.
    for sig, buf_i in stack:
        spans.append((sig, f"⚠️ unclosed: {buffers[buf_i].strip()}"))
    return spans


_HYPHEN_SPACE = re.compile(r"[-\s]+")


def _term_contains(text: str, term: str) -> bool:
    """English: word boundaries, case-insensitive. Terms with CJK: substring.

    English side first folds hyphens and whitespace to a single space: the
    glossary says "All Stat Bonus", the target says "All-Stat Bonus", same
    term. Then a trailing s/es is tolerated in both directions: "Gale Arrow"
    and "Gale Arrows" are the same term (4 false alarms across three real
    batches came from exactly this). Only words longer than 3 letters are
    stemmed, so ATK / DMG / HP are left alone.
    """
    if re.search(r"[一-鿿]", term):
        return term in text
    t, s = _HYPHEN_SPACE.sub(" ", term), _HYPHEN_SPACE.sub(" ", text)
    stem = re.sub(r"e?s$", "", t) if len(t) > 3 else t
    return re.search(rf"(?<![A-Za-z]){re.escape(stem)}(?:e?s)?(?![A-Za-z])",
                     s, re.I) is not None


def _glossary_flag(s_content: str, t_content: str, glossary: list) -> str:
    """Source span hits a glossary term -> target span must contain one of its translations.

    Longest match wins. With both 伤害 -> Attack DMG and 物理伤害 -> Physical
    DMG in the table, a target saying "Physical DMG" was flagged 14 times as
    "should contain Attack DMG", because the short entry matched as a
    substring. A short entry hijacking a longer one is the classic glossary
    check bug.
    """
    entry = _longest_term_match(s_content, glossary)
    if not entry:
        return ""
    t_term = entry["target"]
    variants = [v.strip() for v in t_term.split("|") if v.strip()]
    if not any(_term_contains(t_content, v) for v in variants):
        return f"⚠️ term mismatch (source span contains 「{entry['source']}」, target should contain {t_term})"
    return ""


def _cjk_units(text: str) -> int:
    """Number of Chinese characters, after stripping variable placeholders."""
    return len(re.findall(r"[一-鿿]", VAR_RE.sub(" ", text)))


def _en_units(text: str) -> int:
    """Number of English content words, after stripping placeholders and function words."""
    words = re.findall(r"[A-Za-z][A-Za-z'\-]*", VAR_RE.sub(" ", text))
    return len([w for w in words if w.lower() not in FUNCTION_WORDS])


def _first_token(text: str) -> str:
    """First indexable unit: an English word or a single Chinese character, lowercased."""
    m = re.search(r"[A-Za-z][A-Za-z'\-]*|[一-鿿]", text)
    return m.group(0).lower() if m else ""


def _all_tokens(text: str) -> set:
    return {t.lower() for t in re.findall(r"[A-Za-z][A-Za-z'\-]*|[一-鿿]", text)}


# A 15,000-entry glossary, checked entry by entry with a regex on the English
# side, did not finish one batch in two minutes. So: an inverted index on the
# first token, and each span only checks a few dozen candidates. Cached by
# object identity; a new glossary object rebuilds the index.
_TERM_INDEX_CACHE = {}


def _term_index(glossary: list) -> tuple:
    """Two lookups: source first-token -> entries, target first-token -> {variant: {sources}}."""
    key = id(glossary)
    cached = _TERM_INDEX_CACHE.get(key)
    if cached is not None and cached[0] is glossary:
        return cached[1]
    src_idx, tgt_idx = {}, {}
    for e in glossary:
        s, t = e.get("source", ""), e.get("target", "")
        if not s or not t:
            continue
        ft = _first_token(s)
        if ft:
            src_idx.setdefault(ft, []).append(e)
        for v in (x.strip() for x in t.split("|")):
            fv = _first_token(v)
            if fv:
                # One translation can serve several source terms (攻击力 and
                # 物理攻击 both -> ATK). They must be grouped: if any of the
                # source terms sits inside the source span, it is not overflow.
                tgt_idx.setdefault(fv, {}).setdefault(v, set()).add(s)
    _TERM_INDEX_CACHE[key] = (glossary, (src_idx, tgt_idx))
    return src_idx, tgt_idx


def _longest_term_match(text: str, glossary: list) -> dict:
    """The longest glossary entry (by source length) found in text, or None."""
    src_idx, _ = _term_index(glossary)
    hits = [e for tok in _all_tokens(text) for e in src_idx.get(tok, ())
            if _term_contains(text, e["source"])]
    return max(hits, key=lambda e: len(e["source"])) if hits else None


def _term_overflow_flag(s_content: str, t_content: str, glossary: list) -> str:
    """Terminology evidence for an expanded span.

    The source span does not contain a term, but the target span contains its
    approved translation: the target tag swallowed something that sits
    outside the source tag. This is the mirror image of _glossary_flag: that
    one looks for what is missing, this one for what is extra.
    """
    # Length evidence is required as well. When the spans are the same
    # length, a translation appearing in the target only means the source
    # used a synonym that is not in the table. Example: {*element*}系 ->
    # {*element*} Element, where 系 is not in the glossary but 属性 is; the
    # lengths are 1:1, and calling that "expanded" would be wrong.
    if _density_flag(s_content, t_content, glossary) == "":
        return ""
    _, tgt_idx = _term_index(glossary)
    for tok in _all_tokens(t_content):
        for v, sources in tgt_idx.get(tok, {}).items():
            if not _term_contains(t_content, v):
                continue
            if any(_term_contains(s_content, s) for s in sources):
                continue
            hint = " / ".join(sorted(sources)[:3])
            return f"⚠️ span expanded (target also wraps {v}, source span has no 「{hint}」)"
    return ""


def _density_flag(s_content: str, t_content: str, glossary: list) -> str:
    """Content-word density. Only meaningful with Chinese on one side and English on the other.

    Same language on both sides, or an empty side, is skipped (an empty
    target span is flagged separately in align_spans). A source span that is
    itself a glossary term is exempt: 攻击力 -> ATK is an abbreviation and its
    ratio is off by nature.
    """
    s_cjk, t_cjk = _cjk_units(s_content), _cjk_units(t_content)
    s_en, t_en = _en_units(s_content), _en_units(t_content)
    if s_cjk and t_en and not t_cjk:        # zh -> en
        zh, en = s_cjk, t_en
    elif s_en and t_cjk and not s_cjk:      # en -> zh
        zh, en = t_cjk, s_en
    else:
        return ""
    if glossary:
        term = _longest_term_match(s_content, glossary)
        if term:
            variants = [v.strip() for v in term["target"].split("|") if v.strip()]
            if any(_term_contains(t_content, v) for v in variants):
                return ""
    ratio = en / zh
    if ratio < DENSITY_LO:
        return f"⚠️ span may have shrunk (content-word density {ratio:.2f}, below {DENSITY_LO})"
    if ratio > DENSITY_HI:
        return f"⚠️ span may have expanded (content-word density {ratio:.2f}, above {DENSITY_HI})"
    return ""


def align_spans(src_spans: list, tgt_spans: list, glossary: list = None) -> list:
    """Align pairs with the same signature by order of appearance (k-th to k-th)."""
    glossary = glossary or []
    rows = []
    tgt_pool = list(tgt_spans)
    for sig, s_content in src_spans:
        match = None
        for i, (t_sig, t_content) in enumerate(tgt_pool):
            if t_sig == sig:
                match = tgt_pool.pop(i)
                break
        t_content = match[1] if match else "❌ no matching tag pair in target"
        flag = ""
        s_nums = NUMBER_RE.findall(s_content)
        t_nums = NUMBER_RE.findall(t_content) if match else []
        if not match:
            pass
        elif s_nums != t_nums:
            flag = "⚠️ number mismatch"
        elif s_content.strip() and not t_content.strip():
            # One real segment: the source wrapped one word, the target's
            # pair sat empty with the word right after it. The older version
            # said nothing.
            flag = "❌ empty target span (source span is not empty)"
        else:
            if glossary:
                flag = (_glossary_flag(s_content, t_content, glossary)
                        or _term_overflow_flag(s_content, t_content, glossary))
            if not flag:
                flag = _density_flag(s_content, t_content, glossary)
        rows.append((sig, s_content, t_content, flag))
    for t_sig, t_content in tgt_pool:
        rows.append((t_sig, "❌ no such tag pair in source", t_content, ""))
    return rows


def build_report(pairs: list, glossary: list = None) -> str:
    lines = ["# Tag semantic-position report", "",
             "For each tag pair: does the text it wraps in the target correspond to the "
             "text it wraps in the source?",
             "Rows with ⚠️ or ❌ first. The rest need a human eye (for example: source wraps "
             "a number, target wraps a term, so the tag landed on the wrong word).", ""]
    for p in pairs:
        rows = align_spans(extract_spans(p["source"]), extract_spans(p["target"]), glossary)
        if not rows:
            continue
        lines.append(f"## Segment {p['id']}")
        lines.append("| tag | source wraps | target wraps | flag |")
        lines.append("|---|---|---|---|")
        for sig, s, t, flag in rows:
            esc = lambda x: x.replace("|", "\\|")
            lines.append(f"| `{esc(sig)}` | {esc(s)} | {esc(t)} | {flag} |")
        lines.append("")
    return "\n".join(lines)


def write_report(pairs: list, output_path: str = "", glossary: list = None) -> str:
    """Build the report; write it to output_path when given. Returns the markdown."""
    report = build_report(pairs, glossary)
    if output_path:
        Path(output_path).write_text(report, encoding="utf-8")
    return report
