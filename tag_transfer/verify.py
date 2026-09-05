"""
Tag verification: does the target carry the same tags as the source?

Public API:
  verify_all(pairs, auto_detect_tags=True) -> list[TagIssue]
      pairs = [{"id": ..., "source": ..., "target": ...}, ...]
  verify_segment(seg_id, source, target)   -> list[TagIssue]
  verify_redaction_runs(pairs)              -> list[TagIssue]
  set_custom_tags(names), detect_paired_bbcode(texts)
  print_report(pairs, issues)

What it checks, segment by segment. Every rule is here because the bug it
catches reached a real file first:

  1. Tag count must match                                     CRITICAL
  2. Tag content must match after normalisation. Duplicates
     are counted exactly (Counter, not set)                   CRITICAL
  3. Paired tags must nest legally: XLIFF bpt/ept/g, HTML
     pairs and BBCode pairs, checked with a stack             CRITICAL
  4. Position-anchored tags (bpt/ept/g) must keep their order CRITICAL
  5. Word-wrapping tags (BBCode/HTML pairs) may change order
     when the word order of the sentence changes              WARNING
  6. Placeholders and variables may change order              WARNING
  7. ICU-style conditional variables like {N:plural:a|b}: the
     option text may be translated; dropping the whole thing
     in a Chinese target is a WARNING, not a CRITICAL         WARNING
  8. Line-break tags must not gain spaces around them         WARNING
  9. Redaction blocks (■) must match run by run               CRITICAL

One more rule that is not code: read tags from the original file, never
from an intermediate parsed copy.
"""

import html as _html
import re
from collections import Counter
from dataclasses import dataclass

# ============================================================
# Tag patterns
# ============================================================

PAIRED_HTML_TAGS = r'b|i|u|em|strong|span|a|font|sub|sup'

# BBCode (Godot RichTextLabel, Unity TextMeshPro rich text).
# Lesson behind it: the pattern list once had no BBCode at all, so a
# BBCode-heavy file reported zero issues. Zero meant "never checked", not
# "clean".
# This is a whitelist on purpose. A wildcard like \[/?[a-zA-Z]+\] would turn
# [TODO], [1], [s01] and [0-9] into tags, and false alarms make people stop
# reading the report faster than missed tags do.
BBCODE_TAGS = (
    "b|i|u|s|code|p|center|right|left|fill|indent|url|img|font_size|font|"
    "color|bgcolor|fgcolor|outline_size|outline_color|table|cell|ul|ol|br|"
    "wave|tornado|shake|fade|rainbow|pulse|size|align|alpha|link|sup|sub|"
    "mark|nobr|noparse|style|gradient|rotate|cspace|mspace|voffset|width"
)

STRUCTURAL_PATTERNS = [
    r'<bpt[^>]*>.*?</bpt>',
    r'<ept[^>]*>.*?</ept>',
    r'<g\b[^>]*>',
    r'</g>',
    rf'<(?:{PAIRED_HTML_TAGS})\b[^>]*>',
    rf'</(?:{PAIRED_HTML_TAGS})>',
    rf'\[/?(?:{BBCODE_TAGS})\b(?:=[^\]]*)?\]',
]

PLACEHOLDER_PATTERNS = [
    r'<ph[^>]*>.*?</ph>',
    r'<ph[^>]*/?>',
    r'<x[^>]*/?>',
    r'\{[^}]+\}',
    r'%[sd]|%\d+\$[sd]',
    r'\\n',
]

GENERIC_PATTERNS = [
    r'<[^>]+>',
]

# Project-specific BBCode.
#
# The whitelist above has a blind spot: tags a project invented itself, such
# as [gold] or [jitter], are simply not checked. On one real game file only
# 15-20% of lost custom tags were caught, and order changes almost never,
# while the report still said "count / content / order / nesting verified".
#
# Two ways in. Automatic first, explicit as a fallback:
#   1. Auto-detect pairs: if the whole batch contains both [x...] and [/x],
#      then x is a tag. [TODO] has no [/TODO], so it is left alone.
#   2. --tags gold,blue: for self-closing custom tags, which have no closing
#      mate and therefore cannot be auto-detected.
_CUSTOM_BBCODE: set = set()
_BBCODE_PAIR_RE = re.compile(r'\[/?([A-Za-z_][\w-]{0,29})\b(?:=[^\]]*)?\]')


def _compile_patterns() -> None:
    """Rebuild every compiled pattern from the current custom-tag set.

    Must be called after the set changes.
    """
    global STRUCTURAL_PATTERNS, STRUCTURAL_RE, PLACEHOLDER_RE
    global ALL_PATTERNS, COMBINED_PATTERN, EXPLICIT_PATTERN
    names = BBCODE_TAGS
    if _CUSTOM_BBCODE:
        names += "|" + "|".join(sorted(re.escape(n) for n in _CUSTOM_BBCODE))
    STRUCTURAL_PATTERNS = _BASE_STRUCTURAL + [
        rf'\[/?(?:{names})\b(?:=[^\]]*)?\]',
    ]
    STRUCTURAL_RE = re.compile('|'.join(STRUCTURAL_PATTERNS))
    PLACEHOLDER_RE = re.compile('|'.join(PLACEHOLDER_PATTERNS))
    ALL_PATTERNS = STRUCTURAL_PATTERNS + PLACEHOLDER_PATTERNS + GENERIC_PATTERNS
    COMBINED_PATTERN = re.compile('|'.join(ALL_PATTERNS))
    EXPLICIT_PATTERN = re.compile('|'.join(STRUCTURAL_PATTERNS + PLACEHOLDER_PATTERNS))


def set_custom_tags(names) -> set:
    """Set (replace, not add to) the custom BBCode tag names. Returns the set."""
    global _CUSTOM_BBCODE
    _CUSTOM_BBCODE = {n.strip().lstrip("/") for n in names if n and n.strip()}
    _compile_patterns()
    return set(_CUSTOM_BBCODE)


def custom_tags() -> set:
    """The custom tag names currently in effect."""
    return set(_CUSTOM_BBCODE)


def detect_paired_bbcode(texts) -> set:
    """Bracket names that appear both as [x] and [/x] somewhere in the batch."""
    opens, closes = set(), set()
    for text in texts:
        for m in _BBCODE_PAIR_RE.finditer(text or ""):
            (closes if m.group(0).startswith("[/") else opens).add(m.group(1))
    known = set(BBCODE_TAGS.split("|"))
    return (opens & closes) - known


_BASE_STRUCTURAL = list(STRUCTURAL_PATTERNS[:-1])
_compile_patterns()


def _is_lone_angle_direction(text: str, tag: str) -> bool:
    """Is this unknown angle-bracket token plain dialogue text, not a tag?

    memoQ/XLIFF stores player-facing stage directions such as <grunts> as
    escaped character data (&lt;grunts&gt;). After XML parsing that looks
    exactly like an unknown opening tag. So an unknown token is treated as a
    tag only if it carries tag-shaped evidence: a closing mate somewhere in
    the text, an attribute (key=value), or a closing / self-closing marker.
    Known inline formats are still matched by the explicit patterns above.
    """
    if EXPLICIT_PATTERN.fullmatch(tag):
        return False
    if tag.startswith(("</", "<!", "<?")) or tag.endswith("/>"):
        return False
    inner = tag[1:-1].strip()
    name_match = re.fullmatch(r'([^\W\d][\w:.-]*)', inner)
    if name_match and re.search(rf'</{re.escape(name_match.group(1))}\s*>', text):
        return False
    # Prose does not write key=value. Anything with "=" stays a tag.
    return "=" not in inner


def _balanced_brace_spans(text: str) -> list:
    r"""Top-level {...} spans, with nesting respected.

    The regex \{[^}]+\} stops at the first "}". Given
    {InBattle:\n(Repeats {RepeatCount:diff()} times)|} it returns the
    fragment {InBattle:\n(Repeats {RepeatCount:diff()}, which can never
    match the other side, so every report about it is noise. On one real
    file that was 84 meaningless CRITICALs.
    """
    spans, depth, start = [], 0, -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0:
                spans.append((start, i + 1))
    return spans


def extract_tags(text: str) -> list:
    brace_spans = _balanced_brace_spans(text)
    items = [(a, b, text[a:b]) for a, b in brace_spans]
    for match in COMBINED_PATTERN.finditer(text):
        if _is_lone_angle_direction(text, match.group(0)):
            continue
        # Anything inside a brace span is option text of a conditional
        # variable. The whole span is one tag; its insides are not.
        if any(a <= match.start() < b for a, b in brace_spans):
            continue
        items.append((match.start(), match.end(), match.group(0)))
    return [t for _, _, t in sorted(items)]


def classify_tag(tag: str) -> str:
    """Return 'structural' or 'placeholder'."""
    if STRUCTURAL_RE.fullmatch(tag):
        return "structural"
    return "placeholder"


# Normalisation: keep the tag name, the id/i attribute and the inner text;
# drop display attributes such as ctype or assoc. Then a tag read from the
# raw file and a tag rebuilt from a parsed copy compare the same way.
_ATTR_ID_RE = re.compile(r'\b(id|i)="([^"]*)"')
_TAG_PARSE_RE = re.compile(r'<(\w+)\b([^>]*?)(/?)>(?:(.*)</\1>)?$', re.DOTALL)


# ── ICU-style conditional and plural variables ─────────────────────────
#
# {Gems:plural:Gem|Gems}, {IfUpgraded:show:A|B}, {X:select:a|b}: the part
# after the colon is option text, and option text gets translated. It is not
# part of the tag's identity. Before this fix the whole string took part in
# the comparison, so "the options were translated" came out as
# TAG_CONTENT_MISMATCH. On one real game's official translation that made
# 817 issues per direction; a sample found none of them real.
#
# Two changes:
#   1. Identity is only {Name:type}. Option text is ignored.
#   2. Dropping the variable entirely in a Chinese target is a WARNING, not a
#      CRITICAL. English has plural forms, Chinese does not. When the target
#      is English, losing the plural branch is still CRITICAL.
#
# Both spellings count: with a type keyword ({N:plural:...}) and without one,
# listing options directly ({IsMultiplayer:yours|your own}). The test is
# "the part after the colon has options separated by |" or "the type is a
# known keyword". A value function such as {Damage:diff()} is not
# conditional: nothing inside it is translated, so the whole string is its
# identity.
#
# "cond" is the type name this module writes back after normalising
# ({N:optionA|optionB} -> {N:cond}). It has to be recognised, otherwise
# normalising twice gives a different answer than normalising once.
_ICU_KEYWORDS = ("plural", "select", "show", "choice", "cond")
_VAR_HEAD_RE = re.compile(r'^\{([A-Za-z_][\w.]*)\s*:\s*(.*)\}$', re.S)
_CJK_RE = re.compile(r'[一-鿿぀-ヿ]')


def _icu_parts(tag: str):
    """{Name:type:optionA|optionB} -> (Name, type); None if not conditional.

    The "|" must sit at the top level of this token. In
    {InBattle:\n(Repeats {X:diff()} times)|} the option text contains
    another variable, and a plain regex stops at its "{". On one real file
    71 segments were stuck on exactly that.
    """
    head = _VAR_HEAD_RE.match(tag)
    if not head:
        return None
    name, rest = head.group(1), head.group(2)
    for keyword in _ICU_KEYWORDS:
        if re.match(rf'{keyword}\b', rest):
            return name, keyword
    depth = 0
    for ch in rest:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif ch == "|" and depth == 0:
            return name, "cond"
    return None


def is_icu_conditional(tag: str) -> bool:
    return _icu_parts(tag) is not None


_VAR_NAME_RE = re.compile(r'^\{([A-Za-z_][\w.]*)\s*[:}]')


def icu_name(tag: str) -> str:
    """Variable name of a conditional ({Gems:plural:...} -> Gems)."""
    parts = _icu_parts(tag)
    return parts[0] if parts else ""


def normalize_tag(tag: str) -> str:
    icu = _icu_parts(tag)
    if icu:
        return f"{{{icu[0]}:{icu[1]}}}"
    m = _TAG_PARSE_RE.match(tag)
    if not m:
        return tag  # {N}, %s, \n, </g> and other non-opening tags stay as they are
    name, attrs, selfclose, inner = m.group(1), m.group(2), m.group(3), m.group(4)
    id_m = _ATTR_ID_RE.search(attrs)
    id_part = f' {id_m.group(1)}="{id_m.group(2)}"' if id_m else ""
    if inner is not None:
        return f'<{name}{id_part}>{inner}</{name}>'
    if selfclose:
        return f'<{name}{id_part}/>'
    return f'<{name}{id_part}>'


def _carries_value(cond: str) -> bool:
    r"""Does this conditional variable print the number itself?

    {Amount:plural:turn|turns} prints only a word. {Amount:diff()} and
    {Amount:plural:an item|[blue]{}[/blue] items} (a branch contains {})
    print the value. The word-only kind must not cancel out a missing bare
    {Amount}: if it did, the number would vanish from the target while every
    check stayed green.
    """
    body = cond[1:-1] if cond.startswith('{') and cond.endswith('}') else cond
    kind = body.split(':', 2)[1] if body.count(':') >= 1 else ''
    if kind.endswith('()'):          # diff() / value() and other value functions
        return True
    return '{}' in body


_INLINE_MARKUP_RE = re.compile(r'^\[/?[^\]]*\]$|^</?(?:%s)\b[^>]*>$' % PAIRED_HTML_TAGS)


def _is_inline_markup(tag: str) -> bool:
    """BBCode / HTML pairs wrap words. XLIFF bpt/ept/g are anchored to a position."""
    return bool(_INLINE_MARKUP_RE.match(tag))


def split_by_type(tags: list) -> tuple:
    """Split into (structural, placeholder)."""
    structural, placeholder = [], []
    for t in tags:
        if classify_tag(t) == "structural":
            structural.append(t)
        else:
            placeholder.append(t)
    return structural, placeholder


# ============================================================
# Nesting check (stack)
# ============================================================

# bpt/ept pair by rid in memoQ (id/i is a running number). Use rid when
# present, fall back to i/id.
_RID_RE = re.compile(r'\brid="([^"]*)"')
_IID_RE = re.compile(r'\b(?:i|id)="([^"]*)"')


def _pair_key(token: str) -> str:
    m = _RID_RE.search(token)
    if m:
        return m.group(1)
    m = _IID_RE.search(token)
    return m.group(1) if m else ""


G_OPEN_RE = re.compile(r'<g\b[^>]*>')
G_CLOSE_RE = re.compile(r'</g>')

HTML_OPEN_RE = re.compile(rf'<({PAIRED_HTML_TAGS})\b[^>]*>')
HTML_CLOSE_RE = re.compile(rf'</({PAIRED_HTML_TAGS})>')

NESTING_TOKEN_RE = re.compile(
    r'(<bpt\s[^>]*>.*?</bpt>)'
    r'|(<ept\s[^>]*>.*?</ept>)'
    r'|(<g\b[^>]*>)'
    r'|(</g>)'
    rf'|(<(?:{PAIRED_HTML_TAGS})\b[^>]*>)'
    rf'|(</(?:{PAIRED_HTML_TAGS})>)'
)

# ph/x are standalone placeholders whose insides often contain HTML text
# (for example <ph id="1"><b></ph>). They take no part in nesting, so they
# are blanked out first; otherwise their insides look like unclosed tags.
_PH_SPAN_RE = re.compile(
    r'<ph\b[^>]*>.*?</ph>|<ph\b[^>]*/\s*>|<x\b[^>]*/\s*>', re.DOTALL)

# BBCode nesting. The original check_nesting only knew XLIFF bpt/ept/g and
# angle-bracket HTML; square brackets were never checked. So [blue]word[/green],
# a crossed pair, only reached the order check, where it shared a WARNING
# code with "Chinese word order changed" and could not be told apart. A
# crossed, orphaned or unclosed pair is a pure structural error, the renderer
# breaks on it, so it is CRITICAL.
_BBCODE_NEST_RE = re.compile(r'\[(/?)([A-Za-z_][\w-]{0,29})\b(?:[ =][^\]]*)?\]')
_MAX_BRANCH_VARIANTS = 12


def _icu_branch_variants(text: str) -> list:
    r"""Expand an ICU choice structure into one string per branch.

    One real segment was written as: a [gold] opened outside the braces, and
    each of the two branches carried its own [/gold]. Flattened, that is one
    open and two closes. Per branch, each is balanced. Without expanding the
    branches it is a false alarm.
    """
    variants = [text]
    for _ in range(3):                       # at most three levels, no blow-up
        grown, changed = [], False
        for cur in variants:
            spans = [(a, b) for a, b in _balanced_brace_spans(cur)
                     if '|' in cur[a:b]]
            if not spans:
                grown.append(cur)
                continue
            changed = True
            a, b = spans[0]
            body, depth, piece, pieces = cur[a + 1:b - 1], 0, [], []
            for ch in body:                  # split only on this level's "|"
                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                if ch == '|' and depth == 0:
                    pieces.append(''.join(piece))
                    piece = []
                    continue
                piece.append(ch)
            pieces.append(''.join(piece))
            grown += [cur[:a] + x + cur[b:] for x in pieces]
        variants = grown[:_MAX_BRANCH_VARIANTS]
        if not changed:
            break
    return variants


def _bbcode_nesting_errors(text: str) -> list:
    """Stack check for square-bracket pairs. Every branch must balance."""
    last = ''
    for variant in _icu_branch_variants(text):
        toks = [(m.group(1) == '/', m.group(2), m.group(0))
                for m in _BBCODE_NEST_RE.finditer(variant)]
        # A name that never appears in closing form in this segment is
        # treated as self-closing and stays off the stack ([TODO] is fine).
        closers = {name for is_close, name, _ in toks if is_close}
        stack, err = [], ''
        for is_close, name, raw in toks:
            if name not in closers:
                continue
            if not is_close:
                stack.append(name)
            elif not stack:
                err = f'{raw} has no opening tag'
            elif stack[-1] != name:
                err = f'crossed nesting: [{stack[-1]}] was closed by {raw}'
            else:
                stack.pop()
            if err:
                break
        if not err and stack:
            err = f'[{stack[-1]}] is never closed'
        if not err:
            return []                        # one balanced branch is enough
        last = err
    return [last] if text.strip() and last else []


def check_nesting(text: str) -> list:
    """Return a list of nesting errors (empty when legal)."""
    text = _PH_SPAN_RE.sub(' ', text)
    errors = []
    stack = []

    for m in NESTING_TOKEN_RE.finditer(text):
        token = m.group(0)

        if token.startswith('<bpt'):
            stack.append(("bpt", _pair_key(token), m.start()))
            continue

        if token.startswith('<ept'):
            ept_key = _pair_key(token)
            if not stack:
                errors.append(f"<ept {ept_key}> has no matching <bpt>")
            elif stack[-1][0] == "bpt" and stack[-1][1] == ept_key:
                stack.pop()
            else:
                errors.append(
                    f"<ept {ept_key}> nesting error, "
                    f"expected to close {stack[-1][0]} \"{stack[-1][1]}\"")
            continue

        if G_OPEN_RE.match(token):
            stack.append(("g", "", m.start()))
            continue

        if G_CLOSE_RE.match(token):
            if not stack:
                errors.append("</g> has no matching <g>")
            elif stack[-1][0] == "g":
                stack.pop()
            else:
                errors.append(
                    f"</g> nesting error, expected to close {stack[-1][0]} i=\"{stack[-1][1]}\"")
            continue

        html_open = HTML_OPEN_RE.match(token)
        if html_open:
            stack.append((html_open.group(1), "", m.start()))
            continue

        html_close = HTML_CLOSE_RE.match(token)
        if html_close:
            tag_name = html_close.group(1)
            if not stack:
                errors.append(f"</{tag_name}> has no matching <{tag_name}>")
            elif stack[-1][0] == tag_name:
                stack.pop()
            else:
                errors.append(
                    f"</{tag_name}> nesting error, expected to close <{stack[-1][0]}>")
            continue

    for item in stack:
        errors.append(f"unclosed <{item[0]}> (i=\"{item[1]}\")")

    errors += _bbcode_nesting_errors(text)
    return errors


# ============================================================
# Verification
# ============================================================

@dataclass
class TagIssue:
    seg_id: str
    severity: str  # CRITICAL / WARNING
    issue_type: str
    detail: str


def verify_segment(seg_id: str, source: str, target: str) -> list:
    issues = []

    src_tags = [normalize_tag(t) for t in extract_tags(source)]
    tgt_tags = [normalize_tag(t) for t in extract_tags(target)]
    src_counter = Counter(src_tags)
    tgt_counter = Counter(tgt_tags)

    # When the only difference is "a conditional variable was resolved away
    # in the Chinese target", report one WARNING instead of two CRITICALs.
    missing_now = src_counter - tgt_counter
    extra_now = tgt_counter - src_counter
    # Normalising erases the branch text ({A:plural:a|{} items} -> {A:plural}),
    # so "does it print the value" must be decided on the raw text, before
    # normalising.
    value_carrying = {
        normalize_tag(t)
        for t in extract_tags(source) + extract_tags(target)
        if _carries_value(t)
    }

    # A Chinese target resolves a conditional in one of two shapes. Both count:
    #   1. removed entirely: {Gems:plural:Gem|Gems} -> nothing in the target
    #   2. replaced by the bare variable: {Amount:plural:an item|{} items}
    #      -> {Amount}. On one real file 193 segments did this and every
    #      one was a CRITICAL before this fix.
    def _cancel(cond_side, bare_side):
        """Cancel a conditional against a non-conditional form of the same name.

        The other form is not always the bare {N}: one real file wrote
        {Power:plural:time|times} as {Power:diff()}, the value-function form
        of the same variable. The rule is "same name, and the other one is not
        conditional".
        """
        out = []
        for tag in list(cond_side):
            if not is_icu_conditional(tag) or not cond_side[tag]:
                continue
            name = icu_name(tag)
            for other in list(bare_side):
                if not bare_side[other] or is_icu_conditional(other):
                    continue
                if tag not in value_carrying:
                    continue
                if _VAR_NAME_RE.match(other) and _VAR_NAME_RE.match(other).group(1) == name:
                    n = min(cond_side[tag], bare_side[other])
                    cond_side[tag] -= n
                    bare_side[other] -= n
                    out.extend([tag] * n)
                    if not cond_side[tag]:
                        break
        return out

    resolved = _cancel(missing_now, extra_now)    # Chinese wrote {N:plural:...} as {N}
    restored = _cancel(extra_now, missing_now)    # English wrote {N} back as {N:plural:...}
    missing_now = +missing_now      # drop keys whose count reached zero
    extra_now = +extra_now

    icu_only_drop = (
        bool(missing_now or resolved)
        and not (extra_now or restored)
        and all(is_icu_conditional(t) for t in missing_now)
        and bool(_CJK_RE.search(target))
    )
    icu_only_add = (
        bool(extra_now or restored)
        and not (missing_now or resolved)
        and all(is_icu_conditional(t) for t in extra_now)
        and bool(_CJK_RE.search(source))
    )
    if icu_only_drop:
        dropped = sorted(missing_now) + sorted(set(resolved))
        issues.append(TagIssue(
            seg_id=seg_id,
            severity="WARNING",
            issue_type="ICU_CONDITIONAL_DROPPED",
            detail=f"Target resolved the conditional variable(s) {dropped}. Chinese has no "
                   f"plural forms, so this is usually correct. If the variable carries real "
                   f"branching meaning (different text per option), it is a real defect. "
                   f"Needs a human look.",
        ))
    elif icu_only_add:
        added = sorted(extra_now) + sorted(set(restored))
        issues.append(TagIssue(
            seg_id=seg_id,
            severity="WARNING",
            issue_type="ICU_CONDITIONAL_ADDED",
            detail=f"Target turned a bare variable into the conditional {added}. English needs "
                   f"plurals, so this is usually correct. But confirm the game engine supports "
                   f"this syntax: the source did not use it, the translator invented it, and if "
                   f"the engine does not know it, that is a runtime error.",
        ))
    icu_only_drop = icu_only_drop or icu_only_add

    # 1. Count. No early return: the later checks still run.
    if not icu_only_drop and len(src_tags) != len(tgt_tags):
        issues.append(TagIssue(
            seg_id=seg_id,
            severity="CRITICAL",
            issue_type="TAG_COUNT_MISMATCH",
            detail=f"Source has {len(src_tags)} tag(s), target has {len(tgt_tags)}."
                  f"\n  source: {src_tags}\n  target: {tgt_tags}",
        ))

    # 2. Content. Counter, so duplicate tags are counted exactly.
    if not icu_only_drop and src_counter != tgt_counter:
        missing = src_counter - tgt_counter
        extra = tgt_counter - src_counter
        detail_parts = []
        if missing:
            detail_parts.append(f"missing in target: {dict(missing)}")
        if extra:
            detail_parts.append(f"extra in target: {dict(extra)}")
        issues.append(TagIssue(
            seg_id=seg_id,
            severity="CRITICAL",
            issue_type="TAG_CONTENT_MISMATCH",
            detail="\n".join(detail_parts),
        ))

    # 3. Nesting, on both sides. A broken source means an intermediate file
    #    polluted it, and that is worth knowing too.
    for err in check_nesting(source):
        issues.append(TagIssue(
            seg_id=seg_id,
            severity="CRITICAL",
            issue_type="SOURCE_NESTING_ERROR",
            detail=f"[source] {err}",
        ))

    for err in check_nesting(target):
        issues.append(TagIssue(
            seg_id=seg_id,
            severity="CRITICAL",
            issue_type="TAG_NESTING_ERROR",
            detail=err,
        ))

    # 4. Order. Only when the inventories match exactly.
    if src_counter == tgt_counter and src_tags != tgt_tags:
        src_struct, src_ph = split_by_type(src_tags)
        tgt_struct, tgt_ph = split_by_type(tgt_tags)

        # Two kinds of structural tag, two different verdicts.
        # XLIFF bpt/ept/g are anchored to a position in the source: if their
        # order changes, something moved that should not have. BBCode and
        # HTML pairs wrap words: when the sentence's word order changes, the
        # wrapped words move with it, and that is a correct translation:
        #   [blue]25%[/blue] more [gold]Gold[/gold]
        #   -> [gold]金币[/gold]增加[blue]25%[/blue]
        # On one real file 97 segments looked like this. So an order change
        # among word-wrapping tags is a WARNING. Whether each pair still wraps
        # the right words is the semantic report's job, and a human's.
        if src_struct != tgt_struct:
            inline_only = all(_is_inline_markup(t) for t in src_struct + tgt_struct)
            issues.append(TagIssue(
                seg_id=seg_id,
                severity="WARNING" if inline_only else "CRITICAL",
                issue_type=("INLINE_TAG_ORDER_CHANGED" if inline_only
                            else "STRUCTURAL_TAG_ORDER_CHANGED"),
                detail=(("Word-wrapping tags changed order. Normal when the word order of the "
                         "sentence changed; check that each pair still wraps the right words."
                         if inline_only else
                         "Position-anchored tags changed order. They must keep the source order.")
                        + f"\n  source: {src_struct}\n  target: {tgt_struct}"),
            ))

        if src_ph != tgt_ph:
            issues.append(TagIssue(
                seg_id=seg_id,
                severity="WARNING",
                issue_type="PLACEHOLDER_ORDER_CHANGED",
                detail=f"Placeholders / variables changed order. Check that each one still "
                      f"sits where it makes sense."
                      f"\n  source: {src_ph}\n  target: {tgt_ph}",
            ))

    return issues


def verify_all(pairs: list, auto_detect_tags: bool = True) -> list:
    """Verify a whole batch.

    By default the whole batch is scanned first, and any square-bracket name
    that appears both as [x] and [/x] is added to the custom tag set.
    """
    if auto_detect_tags:
        found = detect_paired_bbcode(
            [t for p in pairs for t in (p.get("source", ""), p.get("target", ""))])
        if found:
            set_custom_tags(_CUSTOM_BBCODE | found)
    all_issues = []
    for pair in pairs:
        issues = verify_segment(
            pair.get("id", "unknown"),
            pair["source"],
            pair["target"],
        )
        issues += check_break_padding(
            pair.get("id", "unknown"), pair["source"], pair["target"])
        all_issues.extend(issues)
    return all_issues


# ============================================================
# Extra spaces around line-break tags
# ============================================================
#
# A line break is whitespace already; spaces next to it are never needed.
# They come from the placement step: the model sees a token like {3}, English
# tokenising habits put spaces around it, and after the tags are put back that
# becomes a trailing space or a leading indent on the next line.
#
# On one real file (709 story segments) the source had 386 tags and zero
# adjacent spaces (Chinese has no word spaces). The target had 40 spaces
# before <br> and 11 after. Four review passes let all of them through: a
# trailing space is invisible in-game, a leading one only shifts a line by one
# character, and every gate (count, pairing, order, terms, punctuation) was
# green.
#
# The rule is anchored to the source: a space that already exists in the
# source is not reported (an English source may really say "text <br>"). That
# gives zero false alarms on any language or engine, with no need to guess
# whether a space was intentional. It is a WARNING: it is dirt, not a meaning
# change, and it is mechanically fixable. But it has to show in the report,
# or it is one more defect nobody sees and nobody fixes.

_BREAK_CORE_RE = re.compile(r"</?(?:br|hr)\s*/?>|\[/?br\]", re.I)
_PH_BLOCK_RE = re.compile(r'<ph\b[^>]*?(?:/>|>.*?</ph>)', re.S)
_DISPLAYTEXT_RE = re.compile(r'displaytext="([^"]*)"')


def _break_spans(text: str):
    """[(start, end)] of every line-break tag. memoQ <ph> envelopes are opened first."""
    spans = []
    for m in _PH_BLOCK_RE.finditer(text):
        d = _DISPLAYTEXT_RE.search(_html.unescape(m.group(0)))
        core = _html.unescape(d.group(1)) if d else ""
        if _BREAK_CORE_RE.fullmatch(core.strip()):
            spans.append(m.span())
    covered = list(spans)
    for m in _BREAK_CORE_RE.finditer(text):
        if not any(a <= m.start() < b for a, b in covered):
            spans.append(m.span())
    return sorted(spans)


def _padded_break_count(text: str) -> int:
    """How many line-break tags have a space or tab right next to them."""
    n = 0
    for a, b in _break_spans(text):
        if a > 0 and text[a - 1] in " \t":
            n += 1
        if b < len(text) and text[b] in " \t":
            n += 1
    return n


def check_break_padding(seg_id: str, source: str, target: str) -> list:
    src_n, tgt_n = _padded_break_count(source), _padded_break_count(target)
    if tgt_n <= src_n:
        return []
    return [TagIssue(
        seg_id=seg_id,
        severity="WARNING",
        issue_type="BREAK_TAG_PADDING",
        detail=f"Spaces next to line-break tags: source {src_n}, target {tgt_n}, "
               f"{tgt_n - src_n} extra (trailing space or leading indent)",
    )]


# ============================================================
# Redaction blocks (■) reconciled run by run
# ============================================================
#
# Why this exists: in one story-heavy file, ■ marked censored text inside the
# story, 5,174 of them across 38 segments. The bilingual review prompt said in
# plain words "do not report anything about these symbols". The reviewing
# model still miscounted one 153-block segment and produced two false
# CRITICALs (it said 15 vs 18).
#
# The lesson is not "write a sterner prompt". It is that counting should not
# be given to a language model at all. A ban does not help, because the model
# is not disobeying, it is genuinely miscounting. The fix: let a script settle
# the count first, then state in the prompt, as an established fact, that
# source and target match. Then there is nothing left to count.

REDACTION_CHARS = "■□▪▫"


def redaction_runs(text: str, chars: str = REDACTION_CHARS) -> list:
    """Lengths of consecutive redaction runs: '■■■a■■' -> [3, 2].

    Compared run by run, not by total. Same total with different break points
    ([16, 13] vs [13, 16]) means the censored spot moved, and that is a real
    problem a total would hide.
    """
    runs, current = [], 0
    for ch in text or "":
        if ch in chars:
            current += 1
        elif current:
            runs.append(current)
            current = 0
    if current:
        runs.append(current)
    return runs


def verify_redaction_runs(pairs: list, chars: str = REDACTION_CHARS) -> list:
    """Compare source and target redaction runs per segment."""
    issues = []
    for pair in pairs:
        src = redaction_runs(pair.get("source", ""), chars)
        tgt = redaction_runs(pair.get("target", ""), chars)
        if src == tgt:
            continue
        issues.append(TagIssue(
            seg_id=str(pair.get("id", "unknown")),
            severity="CRITICAL",
            issue_type="REDACTION_RUN_MISMATCH",
            detail=(f"Redaction runs differ (source {sum(src)} blocks in {len(src)} runs / "
                    f"target {sum(tgt)} blocks in {len(tgt)} runs)"
                    f"\n  source: {src}\n  target: {tgt}"),
        ))
    return issues


def redaction_reconciliation_note(pairs: list, chars: str = REDACTION_CHARS) -> str:
    """A paragraph you can paste into a review prompt, stating the settled count.

    This is the main output of the redaction check, not a by-product: put it
    in the prompt and the model has nothing left to count. The caller must
    check that the text says the runs match; if they do not, fix the data
    before sending anything for review.
    """
    bearing = [p for p in pairs
               if redaction_runs(p.get("source", ""), chars)
               or redaction_runs(p.get("target", ""), chars)]
    if not bearing:
        return "This batch contains no redaction characters. Nothing to reconcile."
    issues = verify_redaction_runs(bearing, chars)
    total = sum(sum(redaction_runs(p.get("source", ""), chars)) for p in bearing)
    if issues:
        return (f"Redaction reconciliation FAILED: {len(bearing)} segment(s) contain redaction "
                f"blocks, {len(issues)} of them differ between source and target (ids "
                f"{[i.seg_id for i in issues]}). Fix the data first. Do not send this draft "
                f"for review.")
    return (
        f"## Established fact: redaction blocks have been reconciled by script\n"
        f"{len(bearing)} segment(s) in this batch contain runs of redaction characters "
        f"({chars[0]} and similar), {total} in total.\n"
        f"**A script compared source and target run by run: they match exactly** "
        f"(count, break points and order are all identical).\n"
        f"These mark censored text inside the story. They are not garbage and not "
        f"placeholders.\n"
        f"Therefore: do not count these symbols, and do not report anything about their "
        f"number. That question is settled.\n"
        f"When judging meaning, read each run as one unknown noun or adjective.\n"
    )


# ============================================================
# Report
# ============================================================

def print_report(pairs: list, issues: list):
    total = len(pairs)
    critical = sum(1 for i in issues if i.severity == "CRITICAL")
    warning = sum(1 for i in issues if i.severity == "WARNING")
    clean = total - len({i.seg_id for i in issues})

    print("=" * 60)
    print("TAG VERIFICATION REPORT")
    print("=" * 60)
    print(f"Segments: {total}")
    print(f"Passed: {clean}  |  CRITICAL: {critical}  |  WARNING: {warning}")
    if _CUSTOM_BBCODE:
        print(f"Custom tags in effect: {', '.join(sorted(_CUSTOM_BBCODE))}")
    print("-" * 60)

    if not issues:
        print("All segments passed tag verification.")
        return

    for issue in issues:
        marker = "[!]" if issue.severity == "CRITICAL" else "[?]"
        print(f"\n{marker} [{issue.severity}] segment {issue.seg_id}")
        print(f"    type: {issue.issue_type}")
        for line in issue.detail.split('\n'):
            print(f"    {line}")

    print("\n" + "=" * 60)
    if critical > 0:
        print(f"{critical} CRITICAL issue(s). Fix before delivery.")
    else:
        print(f"{warning} WARNING(s). A human should look at them.")
