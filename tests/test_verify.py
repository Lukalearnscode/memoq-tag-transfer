#!/usr/bin/env python3
"""Regression tests for verify.py — BBCode coverage and false-positive guards.

Why these exist: the tag pattern library originally had no BBCode support
(common in Godot/Unity rich text). A BBCode-heavy file reported zero issues,
which read as "clean" but actually meant "never checked" — the most dangerous
kind of false green light.

Run: python3 tests/test_verify.py
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from tag_transfer import verify as vt  # noqa: E402


def _tags(text):
    return vt.STRUCTURAL_RE.findall(text) if hasattr(vt, "STRUCTURAL_RE") else []


def test_bbcode_is_extracted():
    """BBCode 标签必须被识别为结构 tag，否则丢了也不会报。"""
    text = "[color=#ff0000]警告[/color]：请[b]立即[/b]撤离"
    found = vt.STRUCTURAL_RE.findall(text)
    for expect in ("[color=#ff0000]", "[/color]", "[b]", "[/b]"):
        assert expect in "".join(found) or any(expect in f for f in found), \
            f"BBCode {expect} 未被识别，found={found}"


def test_bbcode_loss_is_detected():
    """真实场景：译文丢了 BBCode 闭合标签，必须报出来。"""
    src = "[color=#ff0000]Warning[/color]: evacuate [b]now[/b]"
    tgt = "[color=#ff0000]警告：请立即撤离"   # 丢了 [/color] 和 [b][/b]
    s_tags = vt.STRUCTURAL_RE.findall(src)
    t_tags = vt.STRUCTURAL_RE.findall(tgt)
    assert len(s_tags) > len(t_tags), \
        f"BBCode 丢失未被检出: src={s_tags} tgt={t_tags}"


def test_plain_brackets_are_not_bbcode():
    """反向：普通方括号不能误判成 BBCode，否则满屏假阳性。"""
    for text in ("见 [1] 注释", "[TODO] 待确认", "数值 [0-9] 区间", "[s01]"):
        found = vt.STRUCTURAL_RE.findall(text)
        joined = "".join(found)
        assert not joined, f"普通方括号被误判为 BBCode: {text!r} -> {found}"


def test_number_regex_requires_a_digit():
    """回归：`[\\d.]+%?` 会把英文句点/省略号当成数字。

    在一个 525-tag 的真实 MQXLZ 上制造过 116 条「数字不一致」假阳性——源译
    两侧句点数不同就报警。数字提取必须至少含一个 digit。
    """
    from tag_transfer import semantic_report as tsr
    pattern = tsr.NUMBER_RE
    for text, expected in [
        ("Wait... what?", []),
        ("He said. Then left.", []),
        ("a.b.c", []),
        ("Level 3.5 boss", ["3.5"]),
        ("50% off", ["50%"]),
        ("v1.2.3 released", ["1.2.3"]),
        ("第 3 章，共 12 章", ["3", "12"]),
    ]:
        got = pattern.findall(text)
        assert got == expected, f"{text!r} -> {got}，应为 {expected}"


def test_redaction_runs_splits_by_break():
    """遮蔽块要切成长度序列，不能只数总数。"""
    assert vt.redaction_runs("■■■a■■") == [3, 2]
    assert vt.redaction_runs("没有遮蔽块") == []


def test_redaction_total_same_but_breaks_moved_is_caught():
    """总数相同、断点挪了 —— 只比总数会漏，逐串比才抓得到。

    这是删节位置被改动的形态：信息量看似没变，实际遮蔽的内容边界变了。
    """
    pairs = [{"id": "s1", "source": "A■■■B■■C", "target": "甲■■乙■■■丙"}]
    issues = vt.verify_redaction_runs(pairs)
    assert len(issues) == 1 and issues[0].issue_type == "REDACTION_RUN_MISMATCH", issues
    assert issues[0].severity == "CRITICAL"


def test_redaction_identical_runs_pass():
    pairs = [{"id": "s1", "source": "A■■■B■■C", "target": "甲■■■乙■■丙"}]
    assert not vt.verify_redaction_runs(pairs)


def test_redaction_b21_segment_962_reconciles():
    """B21 [962] 的真实串长序列 —— DeepSeek 当初在这段数错，报了 2 条假 CRITICAL。

    脚本必须给出 0 差异，这样才能把结论当既定事实写进 prompt，而不是指望模型
    自己克制别去数。
    """
    runs = [16, 13, 15, 18, 7, 18, 12, 11, 21, 17, 5]
    src = "x".join("■" * n for n in runs)
    tgt = "。".join("■" * n for n in runs)
    assert vt.redaction_runs(src) == runs
    assert not vt.verify_redaction_runs([{"id": "962", "source": src, "target": tgt}])


def test_redaction_note_states_fact_when_clean():
    """对账干净时，产出的 prompt 片段必须是一句『已核对、别数』的既定事实。"""
    pairs = [{"id": "s1", "source": "A■■■B", "target": "甲■■■乙"}]
    note = vt.redaction_reconciliation_note(pairs)
    assert "既定事实" in note and "完全一致" in note
    assert "不要清点" in note


def test_redaction_note_refuses_when_mismatched():
    """对账不过时**不得**产出「已核对」的说法，否则等于把假事实喂给模型。"""
    pairs = [{"id": "s1", "source": "A■■■B", "target": "甲■乙"}]
    note = vt.redaction_reconciliation_note(pairs)
    assert "未通过" in note and "既定事实" not in note


if __name__ == "__main__":
    # 自动发现而非硬编码清单，理由同 test_init_project.py（0726）。
    ran = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            ran += 1
    print(f"PASS ({ran} 项): verify_tags BBCode 覆盖 + 普通方括号不误判 + 数字正则需含 digit")
