"""
Tag semantic-position report — catches "tags are all present but wrapped
around the wrong words".

verify.py checks tag count, content and order. It cannot catch this class of
error: the source has {accent-gn}50%{/style} wrapping a number, while the
target has the same tag pair wrapping something else entirely. Counts match,
order matches, and the file passes every mechanical check while being wrong.

This module lists what each tag pair wraps in source vs target, side by side,
so a reviewer can scan for mismatches.

Public API:
  build_report(pairs) -> str     markdown table; pairs = [{"id","source","target"}, ...]
  extract_spans(text) -> list    tag pairs and their wrapped content
  GLOSSARY                       optional term list; wrapped content that hits a
                                 term must contain the approved translation

Pairing logic:
  - memoQ <ph> elements are resolved back to their original tag via displaytext,
    then open/close tags are matched with a stack
  - same-name tag pairs align by order of appearance (source's k-th accent-gn
    pair maps to target's k-th), which tolerates ph reordering and ID reassignment
  - automatic warning when wrapped content has mismatched numbers; everything
    else is left to human judgement
"""

import html
import re
from pathlib import Path

# ============================================================
# 配置区
# ============================================================

# 可选：术语表联动。源文 tag 包裹内容命中术语条目时，
# 译文同对 tag 必须包含对应定译，否则自动 ⚠️术语不符。
# 格式同 check_glossary：[{"source": "寒冰射线", "target": "Frostbeam", "status": "approved"}]
# 多个合法变体用 "|" 分隔。
GLOSSARY = []

# 不参与配对的独立 tag（无包裹内容）
STANDALONE = {"br", "hr", "img"}


# ============================================================
# 解析逻辑
# ============================================================

# Was `[\d.]+%?`, which treated English periods and ellipses as numbers:
# "Wait... what?" yielded ['...'], "He said. Then left." yielded ['.', '.'].
# On a real 525-tag file that produced 116 false "number mismatch" reports.
# Now requires at least one digit; decimals/versions/percentages still match.
NUMBER_RE = re.compile(r"\d+(?:\.\d+)*%?")


def _resolve_ph(text: str) -> str:
    """把 memoQ <ph> 元素还原为 displaytext 里的原始 tag；非 ph 文本原样保留。"""
    def repl(m):
        d = re.search(r'displaytext="([^"]*)"', m.group(0))
        return html.unescape(d.group(1)) if d else m.group(0)
    return re.sub(r"<ph\b[^>]*>.*?</ph>|<ph\b[^>]*/>", repl, text, flags=re.S)


def _tag_name(tag: str) -> str:
    """<style="accent-gn"> → style:accent-gn；</style> → style；<linktext=1509> → linktext=1509"""
    inner = tag.strip("<>/").strip()
    m = re.match(r'([A-Za-z]+)\s*=\s*"?([^">]*)"?', inner)
    if m:
        return f"{m.group(1)}={m.group(2)}" if "=" in inner.split()[0] or "=" in inner else f"{m.group(1)}:{m.group(2)}"
    return inner.split()[0].split("=")[0]


def extract_spans(text: str) -> list:
    """
    返回 [(签名, 包裹内容)]。签名 = 开标签原文。
    用栈配对：闭标签弹出最近的同类开标签。
    """
    resolved = _resolve_ph(text)
    tokens = re.split(r"(<[^>]+>)", resolved)
    stack = []   # [(签名, 内容缓冲索引)]
    spans = []
    buffers = []
    for tok in tokens:
        if not tok:
            continue
        if tok.startswith("<"):
            name = tok.strip("<>/").split()[0].split("=")[0].rstrip('"')
            if tok.startswith("</"):
                # 闭标签：弹出最近一个同名开标签
                for i in range(len(stack) - 1, -1, -1):
                    open_sig, buf_i = stack[i]
                    open_name = open_sig.strip("<>/").split()[0].split("=")[0].rstrip('"')
                    if open_name == name:
                        spans.append((open_sig, buffers[buf_i].strip()))
                        stack.pop(i)
                        break
            elif name.lower() in STANDALONE or tok.endswith("/>"):
                spans.append((tok, "（独立标签，无包裹内容）"))
            else:
                stack.append((tok, len(buffers)))
                buffers.append("")
        else:
            for j in range(len(buffers)):
                if any(b == j for _, b in stack):
                    buffers[j] += tok
    # 未闭合的开标签也报出来
    for sig, buf_i in stack:
        spans.append((sig, f"⚠️未闭合：{buffers[buf_i].strip()}"))
    return spans


def _term_contains(text: str, term: str) -> bool:
    """英文按词边界不区分大小写；含 CJK 的术语按子串。"""
    if re.search(r"[一-鿿]", term):
        return term in text
    return re.search(rf"(?<![A-Za-z]){re.escape(term)}(?![A-Za-z])", text, re.I) is not None


def _glossary_flag(s_content: str, t_content: str, glossary: list) -> str:
    """源包裹内容命中术语条目 → 译包裹内容必须含定译之一。"""
    for entry in glossary:
        s_term = entry.get("source", "")
        t_term = entry.get("target", "")
        if not s_term or not t_term:
            continue
        if _term_contains(s_content, s_term):
            variants = [v.strip() for v in t_term.split("|") if v.strip()]
            if not any(_term_contains(t_content, v) for v in variants):
                return f"⚠️术语不符（应含 {t_term}）"
    return ""


def align_spans(src_spans: list, tgt_spans: list, glossary: list = None) -> list:
    """同签名的 tag 对按出现次序对齐（第 k 个对第 k 个）。"""
    glossary = glossary if glossary is not None else GLOSSARY
    rows = []
    tgt_pool = list(tgt_spans)
    for sig, s_content in src_spans:
        match = None
        for i, (t_sig, t_content) in enumerate(tgt_pool):
            if t_sig == sig:
                match = tgt_pool.pop(i)
                break
        t_content = match[1] if match else "❌ 译文中未找到对应 tag 对"
        flag = ""
        s_nums = NUMBER_RE.findall(s_content)
        t_nums = NUMBER_RE.findall(t_content) if match else []
        if match and s_nums != t_nums:
            flag = "⚠️数字不一致"
        elif match and glossary:
            flag = _glossary_flag(s_content, t_content, glossary)
        rows.append((sig, s_content, t_content, flag))
    for t_sig, t_content in tgt_pool:
        rows.append((t_sig, "❌ 源文中无此 tag 对", t_content, ""))
    return rows


def build_report(pairs: list) -> str:
    lines = ["# Tag 语义位置对照表", "",
             "逐行核对：每对 tag 在译文中包裹的内容，语义上是否对应源文包裹的内容。",
             "⚠️ 行优先处理；其余行靠人工判断（如源文包数值、译文包了术语 → 贴错词）。", ""]
    for p in pairs:
        rows = align_spans(extract_spans(p["source"]), extract_spans(p["target"]))
        if not rows:
            continue
        lines.append(f"## 段 {p['id']}")
        lines.append("| tag | 源文包裹 | 译文包裹 | 预警 |")
        lines.append("|---|---|---|---|")
        for sig, s, t, flag in rows:
            esc = lambda x: x.replace("|", "\\|")
            lines.append(f"| `{esc(sig)}` | {esc(s)} | {esc(t)} | {flag} |")
        lines.append("")
    return "\n".join(lines)


def write_report(pairs: list, output_path: str = "") -> str:
    """Build the report; write it to output_path when given. Returns the markdown."""
    report = build_report(pairs)
    if output_path:
        Path(output_path).write_text(report, encoding="utf-8")
    return report
