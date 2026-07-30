"""
Tag verification — segment-by-segment consistency check between source and target.

Public API:
  verify_all(pairs)      -> list[TagIssue]   pairs = [{"id", "source", "target"}, ...]
  verify_segment(id, src, tgt) -> list[TagIssue]
  print_report(pairs, issues)                human-readable summary

Rules enforced (each one exists because it caught a real production bug):
  1. 数量必须完全一致（CRITICAL）
  2. Tag 内容在规范化后必须一致（normalize_tag：保留 tag 名+id/i+内部文本），Counter 精确统计重复 tag
  3. 结构性 tag（bpt/ept/g）顺序和嵌套必须严格一致（CRITICAL）
  4. 占位符/变量 tag 顺序允许因语序调整（WARNING）
  5. Paired tags 嵌套必须合法（栈检查）
  6. Tag 内容必须从原始归档读取，不从中间解析文件读取
"""

import re
from collections import Counter
from dataclasses import dataclass, field

# ============================================================
# Tag 分类与提取
# ============================================================

PAIRED_HTML_TAGS = r'b|i|u|em|strong|span|a|font|sub|sup'

# BBCode（Godot RichTextLabel / Unity TextMeshPro 富文本）。0725 补。
# 0716 教训：pattern 库不含 BBCode，这类段落的"零报告"是「没查」不是「没错」。
# 用白名单而不是 \[/?[a-zA-Z]+\] 通配——通配会把 [TODO]、[1]、[s01]、[0-9]
# 这些普通方括号全判成 tag，假阳性比漏报更快让人不再看报告。
BBCODE_TAGS = (
    "b|i|u|s|code|p|center|right|left|fill|indent|url|img|font_size|font|"
    "color|bgcolor|fgcolor|outline_size|outline_color|table|cell|ul|ol|br|"
    "wave|tornado|shake|fade|rainbow|pulse|size|align|alpha|link|sup|sub|"
    "mark|nobr|noparse|style|gradient|rotate|cspace|mspace|voffset|width"
)

STRUCTURAL_PATTERNS = [
    r'<bpt[^>]*>.*?</bpt>',
    r'<ept[^>]*>.*?</ept>',
    r'<g[^>]*>',
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

STRUCTURAL_RE = re.compile('|'.join(STRUCTURAL_PATTERNS))
PLACEHOLDER_RE = re.compile('|'.join(PLACEHOLDER_PATTERNS))

ALL_PATTERNS = STRUCTURAL_PATTERNS + PLACEHOLDER_PATTERNS + GENERIC_PATTERNS
COMBINED_PATTERN = re.compile('|'.join(ALL_PATTERNS))


def extract_tags(text: str) -> list[str]:
    return COMBINED_PATTERN.findall(text)


def classify_tag(tag: str) -> str:
    """返回 'structural' 或 'placeholder'。"""
    if STRUCTURAL_RE.fullmatch(tag):
        return "structural"
    return "placeholder"


# 统一规范化：保留 tag 名 + id/i 属性 + 内部文本，忽略 ctype/assoc 等显示属性。
# 与 SKILL.md Phase 5 规则、parse_mqxlz 的重建逻辑保持一致——
# 这样无论输入来自 parse_mqxlz 还是原始文本，比较口径相同。
_ATTR_ID_RE = re.compile(r'\b(id|i)="([^"]*)"')
_TAG_PARSE_RE = re.compile(r'<(\w+)\b([^>]*?)(/?)>(?:(.*)</\1>)?$', re.DOTALL)


def normalize_tag(tag: str) -> str:
    m = _TAG_PARSE_RE.match(tag)
    if not m:
        return tag  # {N}、%s、\n、</g> 等非开始 tag 原样返回
    name, attrs, selfclose, inner = m.group(1), m.group(2), m.group(3), m.group(4)
    id_m = _ATTR_ID_RE.search(attrs)
    id_part = f' {id_m.group(1)}="{id_m.group(2)}"' if id_m else ""
    if inner is not None:
        return f'<{name}{id_part}>{inner}</{name}>'
    if selfclose:
        return f'<{name}{id_part}/>'
    return f'<{name}{id_part}>'


def split_by_type(tags: list[str]) -> tuple[list[str], list[str]]:
    """拆分为结构性 tag 和占位符 tag。"""
    structural, placeholder = [], []
    for t in tags:
        if classify_tag(t) == "structural":
            structural.append(t)
        else:
            placeholder.append(t)
    return structural, placeholder


# ============================================================
# Paired tag 嵌套检查（栈验证）
# ============================================================

# bpt/ept 配对键：memoQ 用 rid 配对（id/i 是全局流水号），有 rid 用 rid，否则退回 i/id
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


# ph/x 是独立占位 tag，内部常包含 <b> 等 HTML 文本（如 <ph id="1"><b></ph>），
# 不参与嵌套检查——先整体遮蔽，否则内部内容会被误判为未关闭 tag
_PH_SPAN_RE = re.compile(
    r'<ph\b[^>]*>.*?</ph>|<ph\b[^>]*/\s*>|<x\b[^>]*/\s*>', re.DOTALL)


def check_nesting(text: str) -> list[str]:
    """检查 paired tag 嵌套是否合法，返回错误描述列表。"""
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
                errors.append(f"<ept {ept_key}> 无匹配的 <bpt>")
            elif stack[-1][0] == "bpt" and stack[-1][1] == ept_key:
                stack.pop()
            else:
                errors.append(
                    f"<ept {ept_key}> 嵌套错误，"
                    f"期望关闭 {stack[-1][0]} \"{stack[-1][1]}\"")
            continue

        if G_OPEN_RE.match(token):
            stack.append(("g", "", m.start()))
            continue

        if G_CLOSE_RE.match(token):
            if not stack:
                errors.append("</g> 无匹配的 <g>")
            elif stack[-1][0] == "g":
                stack.pop()
            else:
                errors.append(
                    f"</g> 嵌套错误，期望关闭 {stack[-1][0]} i=\"{stack[-1][1]}\"")
            continue

        html_open = HTML_OPEN_RE.match(token)
        if html_open:
            stack.append((html_open.group(1), "", m.start()))
            continue

        html_close = HTML_CLOSE_RE.match(token)
        if html_close:
            tag_name = html_close.group(1)
            if not stack:
                errors.append(f"</{tag_name}> 无匹配的 <{tag_name}>")
            elif stack[-1][0] == tag_name:
                stack.pop()
            else:
                errors.append(
                    f"</{tag_name}> 嵌套错误，"
                    f"期望关闭 <{stack[-1][0]}>")
            continue

    for item in stack:
        errors.append(f"未关闭的 <{item[0]}> (i=\"{item[1]}\")")

    return errors


# ============================================================
# 验证逻辑
# ============================================================

@dataclass
class TagIssue:
    seg_id: str
    severity: str  # CRITICAL / WARNING
    issue_type: str
    detail: str


def verify_segment(seg_id: str, source: str, target: str) -> list[TagIssue]:
    issues = []

    src_tags = [normalize_tag(t) for t in extract_tags(source)]
    tgt_tags = [normalize_tag(t) for t in extract_tags(target)]
    src_counter = Counter(src_tags)
    tgt_counter = Counter(tgt_tags)

    # 1. 数量检查（不再 early return，继续后续分析）
    if len(src_tags) != len(tgt_tags):
        issues.append(TagIssue(
            seg_id=seg_id,
            severity="CRITICAL",
            issue_type="TAG_COUNT_MISMATCH",
            detail=f"源文 {len(src_tags)} 个 tag，译文 {len(tgt_tags)} 个 tag。"
                  f"\n  源: {src_tags}\n  译: {tgt_tags}",
        ))

    # 2. 内容检查（Counter 精确统计，正确处理重复 tag）
    if src_counter != tgt_counter:
        missing = src_counter - tgt_counter
        extra = tgt_counter - src_counter
        detail_parts = []
        if missing:
            detail_parts.append(f"译文缺失: {dict(missing)}")
        if extra:
            detail_parts.append(f"译文多出: {dict(extra)}")
        issues.append(TagIssue(
            seg_id=seg_id,
            severity="CRITICAL",
            issue_type="TAG_CONTENT_MISMATCH",
            detail="\n".join(detail_parts),
        ))

    # 3. 嵌套检查（源端 + 译端，防止中间文件污染源端）
    src_nesting_errors = check_nesting(source)
    for err in src_nesting_errors:
        issues.append(TagIssue(
            seg_id=seg_id,
            severity="CRITICAL",
            issue_type="SOURCE_NESTING_ERROR",
            detail=f"[源文] {err}",
        ))

    tgt_nesting_errors = check_nesting(target)
    for err in tgt_nesting_errors:
        issues.append(TagIssue(
            seg_id=seg_id,
            severity="CRITICAL",
            issue_type="TAG_NESTING_ERROR",
            detail=err,
        ))

    # 4. 顺序检查（仅在 inventory 完全一致时执行）
    if src_counter == tgt_counter and src_tags != tgt_tags:
        src_struct, src_ph = split_by_type(src_tags)
        tgt_struct, tgt_ph = split_by_type(tgt_tags)

        # 结构性 tag 顺序必须严格一致
        if src_struct != tgt_struct:
            issues.append(TagIssue(
                seg_id=seg_id,
                severity="CRITICAL",
                issue_type="STRUCTURAL_TAG_ORDER_CHANGED",
                detail=f"结构性 tag 顺序不一致，必须与源文保持相同。"
                      f"\n  源: {src_struct}\n  译: {tgt_struct}",
            ))

        # 占位符 tag 顺序变化仅 WARNING
        if src_ph != tgt_ph:
            issues.append(TagIssue(
                seg_id=seg_id,
                severity="WARNING",
                issue_type="PLACEHOLDER_ORDER_CHANGED",
                detail=f"占位符/变量 tag 顺序有变化，请确认语义位置是否合理。"
                      f"\n  源: {src_ph}\n  译: {tgt_ph}",
            ))

    return issues


def verify_all(pairs: list) -> list[TagIssue]:
    all_issues = []
    for pair in pairs:
        issues = verify_segment(
            pair.get("id", "unknown"),
            pair["source"],
            pair["target"],
        )
        all_issues.extend(issues)
    return all_issues


# ============================================================
# 遮蔽块（涂黑/删节符）对账 —— 0726 建立
# ============================================================
#
# 存在的理由：B21 里「■」是剧情内的涂黑删节标记，5174 个分布在 38 段里。给
# DeepSeek 做双语审校时，prompt 里已经明写了「不要报告这些符号相关的问题」，
# 它仍然在一个含 153 个■的段落里数错，产出 2 条假 CRITICAL（说 15 vs 18）。
#
# 教训不是「prompt 写得不够狠」，而是**计数这件事根本不该交给语言模型**。禁令
# 挡不住它，因为它不是故意报，是真数错了。正确做法：脚本先把账算完，把「已核对，
# 源译完全一致」当作既定事实写进 prompt，模型就没有可数的余地。

REDACTION_CHARS = "■□▪▫"


def redaction_runs(text: str, chars: str = REDACTION_CHARS) -> list[int]:
    """把连续遮蔽块切成长度序列：'■■■a■■' → [3, 2]。

    逐串比而不是只比总数——总数相同但断点不同（比如 [16,13] vs [13,16]）意味着
    删节位置挪了，那是真问题，只对总数会漏掉。
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


def verify_redaction_runs(pairs: list, chars: str = REDACTION_CHARS) -> list[TagIssue]:
    """逐段比对源/译的遮蔽块序列。返回 TagIssue 列表，与 tag 检查同构。"""
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
            detail=(f"遮蔽块序列不一致（源 {sum(src)} 个分 {len(src)} 串 / "
                    f"译 {sum(tgt)} 个分 {len(tgt)} 串）"
                    f"\n  源: {src}\n  译: {tgt}"),
        ))
    return issues


def redaction_reconciliation_note(pairs: list, chars: str = REDACTION_CHARS) -> str:
    """生成一段可直接粘进审校 prompt 的既定事实陈述。

    这是本模块的重点产出，不是附赠品：把它塞进 prompt，模型才不会去数。
    调用方必须先确认返回文本里写的是「完全一致」，不一致就该先修数据而不是发 prompt。
    """
    bearing = [p for p in pairs
               if redaction_runs(p.get("source", ""), chars)
               or redaction_runs(p.get("target", ""), chars)]
    if not bearing:
        return "本批次不含遮蔽块字符，无需对账。"
    issues = verify_redaction_runs(bearing, chars)
    total = sum(sum(redaction_runs(p.get("source", ""), chars)) for p in bearing)
    if issues:
        return (f"⚠️ 遮蔽块对账**未通过**：{len(bearing)} 段含遮蔽块，其中 "
                f"{len(issues)} 段源/译不一致（段号 "
                f"{[i.seg_id for i in issues]}）。先修数据，不要拿这份稿去审校。")
    return (
        f"## 既定事实：遮蔽块已由脚本对账完毕\n"
        f"本批 {len(bearing)} 段含连续遮蔽块字符（{chars[0]} 等），共 {total} 个。\n"
        f"**已用脚本逐段逐串比对，源文与译文完全一致**（数量、断点、顺序三项全同）。\n"
        f"这是剧情内的「机密内容被涂黑」标记，不是乱码也不是占位符。\n"
        f"因此：不要清点这些符号，不要报告与它们数量相关的任何问题——该账已经结了。\n"
        f"判断句意时把每一串当作一个未知名词/形容词来读即可。\n"
    )


# ============================================================
# 报告输出
# ============================================================

def print_report(pairs: list, issues: list[TagIssue]):
    total = len(pairs)
    critical = sum(1 for i in issues if i.severity == "CRITICAL")
    warning = sum(1 for i in issues if i.severity == "WARNING")
    clean = total - len({i.seg_id for i in issues})

    print("=" * 60)
    print("TAG VERIFICATION REPORT")
    print("=" * 60)
    print(f"Segments: {total}")
    print(f"Passed: {clean}  |  CRITICAL: {critical}  |  WARNING: {warning}")
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
        print(f"{critical} CRITICAL issue(s) — must be fixed before delivery.")
    else:
        print(f"{warning} WARNING(s) — manual review recommended.")
