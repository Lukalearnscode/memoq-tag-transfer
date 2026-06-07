"""Verify tag consistency between source and target segments."""

import re
from collections import Counter


STRUCTURAL_PATTERNS = [
    r'<bpt[^>]*>.*?</bpt>',
    r'<ept[^>]*>.*?</ept>',
    r'<g[^>]*>',
    r'</g>',
]

PLACEHOLDER_PATTERNS = [
    r'<ph[^>]*>.*?</ph>',
    r'<ph[^>]*/?>',
    r'<x[^>]*/?>',
    r'\{\d+\}',
]

ALL_PATTERNS = STRUCTURAL_PATTERNS + PLACEHOLDER_PATTERNS + [r'<[^>]+>']
STRUCTURAL_RE = re.compile('|'.join(STRUCTURAL_PATTERNS))
COMBINED_RE = re.compile('|'.join(ALL_PATTERNS))

BPT_RE = re.compile(r'<bpt\s[^>]*?\b(?:i|id)="(\d+)"[^>]*>')
EPT_RE = re.compile(r'<ept\s[^>]*?\b(?:i|id)="(\d+)"[^>]*>')
G_OPEN_RE = re.compile(r'<g\b[^>]*>')
G_CLOSE_RE = re.compile(r'</g>')

NESTING_RE = re.compile(
    r'(<bpt\s[^>]*>.*?</bpt>)'
    r'|(<ept\s[^>]*>.*?</ept>)'
    r'|(<g\b[^>]*>)'
    r'|(</g>)'
)


def extract_tags(text):
    return COMBINED_RE.findall(text)


def is_structural(tag):
    return bool(STRUCTURAL_RE.fullmatch(tag))


def check_nesting(text):
    """Check paired tag nesting with a stack. Returns list of error strings."""
    errors = []
    stack = []

    for m in NESTING_RE.finditer(text):
        token = m.group(0)

        bpt = BPT_RE.search(token)
        if bpt:
            stack.append(("bpt", bpt.group(1)))
            continue

        ept = EPT_RE.search(token)
        if ept:
            ept_id = ept.group(1)
            if not stack:
                errors.append(f"<ept i=\"{ept_id}\"> has no matching <bpt>")
            elif stack[-1][0] == "bpt" and stack[-1][1] == ept_id:
                stack.pop()
            else:
                errors.append(f"<ept i=\"{ept_id}\"> nesting error")
            continue

        if G_OPEN_RE.match(token):
            stack.append(("g", ""))
            continue

        if G_CLOSE_RE.match(token):
            if not stack:
                errors.append("</g> has no matching <g>")
            elif stack[-1][0] == "g":
                stack.pop()
            else:
                errors.append("</g> nesting error")
            continue

    for item in stack:
        errors.append(f"Unclosed <{item[0]}> (i=\"{item[1]}\")")

    return errors


def verify_segment(seg_id, source, target):
    """Verify tag consistency for one segment.

    Returns list of (severity, issue_type, detail) tuples.
    severity: "CRITICAL" or "WARNING"
    """
    issues = []

    src_tags = extract_tags(source)
    tgt_tags = extract_tags(target)
    src_counter = Counter(src_tags)
    tgt_counter = Counter(tgt_tags)

    if len(src_tags) != len(tgt_tags):
        issues.append((
            "CRITICAL", "TAG_COUNT_MISMATCH",
            f"Source has {len(src_tags)} tags, target has {len(tgt_tags)}"
        ))

    if src_counter != tgt_counter:
        missing = src_counter - tgt_counter
        extra = tgt_counter - src_counter
        parts = []
        if missing:
            parts.append(f"Missing in target: {dict(missing)}")
        if extra:
            parts.append(f"Extra in target: {dict(extra)}")
        issues.append(("CRITICAL", "TAG_CONTENT_MISMATCH", "; ".join(parts)))

    for err in check_nesting(target):
        issues.append(("CRITICAL", "NESTING_ERROR", err))

    if src_counter == tgt_counter and src_tags != tgt_tags:
        src_struct = [t for t in src_tags if is_structural(t)]
        tgt_struct = [t for t in tgt_tags if is_structural(t)]

        if src_struct != tgt_struct:
            issues.append((
                "CRITICAL", "STRUCTURAL_ORDER_CHANGED",
                "Structural tags must keep the same order as source"
            ))

        src_ph = [t for t in src_tags if not is_structural(t)]
        tgt_ph = [t for t in tgt_tags if not is_structural(t)]
        if src_ph != tgt_ph:
            issues.append((
                "WARNING", "PLACEHOLDER_ORDER_CHANGED",
                "Placeholder tag order differs from source — check if semantically correct"
            ))

    return issues


def verify_all(pairs):
    """Verify all segments. Returns (total, critical_count, warning_count, issues_by_seg)."""
    total = len(pairs)
    all_issues = {}
    critical = 0
    warning = 0

    for pair in pairs:
        seg_id = pair.get("id", "unknown")
        issues = verify_segment(seg_id, pair["source"], pair["target"])
        if issues:
            all_issues[seg_id] = issues
            critical += sum(1 for s, _, _ in issues if s == "CRITICAL")
            warning += sum(1 for s, _, _ in issues if s == "WARNING")

    return total, critical, warning, all_issues


def print_report(total, critical, warning, issues_by_seg):
    clean = total - len(issues_by_seg)
    print(f"\nTag verification: {total} segments, {clean} passed, "
          f"{critical} critical, {warning} warnings")

    if not issues_by_seg:
        print("All segments passed.")
        return

    for seg_id, issues in issues_by_seg.items():
        for severity, issue_type, detail in issues:
            marker = "!!" if severity == "CRITICAL" else "?"
            print(f"  [{marker}] Seg {seg_id}: {issue_type} — {detail}")

    if critical > 0:
        print(f"\n{critical} critical issues found. Fix before delivery.")
    else:
        print(f"\n{warning} warnings. Review recommended.")
