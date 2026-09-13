#!/usr/bin/env python3
"""Regression tests for the whole package: verify.py, semantic_report.py,
pairs_io.py and the extract -> place -> output pipeline.

Every test here exists because the bug it guards against reached a real
file first. None was written for coverage. Where a test quotes an example
segment, the segment is made up; it keeps the shape of the real one, not
its words.

Run: python3 tests/test_verify.py   (no pytest needed)
"""
import json
import re
import subprocess
import sys
import tempfile
import types
import zipfile
from pathlib import Path

from lxml import etree

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from tag_transfer import verify as vt  # noqa: E402
from tag_transfer import semantic_report as tsr  # noqa: E402
from tag_transfer import pairs_io  # noqa: E402
from tag_transfer import extract, output  # noqa: E402

# place.py imports the openai SDK at module level. The package is only needed
# for `transfer`, and these tests must run without it, so a stub stands in.
sys.modules.setdefault("openai", types.ModuleType("openai")).OpenAI = lambda **kw: object()
from tag_transfer import place  # noqa: E402

XLIFF_NS = "urn:oasis:names:tc:xliff:document:1.2"


def _source_el(inner: str):
    """A <source> element inside a document that declares the mq namespace,
    the shape lxml actually hands to output.py when reading an mqxliff."""
    doc = etree.fromstring(
        f'<xliff xmlns="{XLIFF_NS}" xmlns:mq="MQXliff"><source>{inner}</source></xliff>'
        .encode("utf-8"))
    return doc[0]


# ── BBCode coverage ──────────────────────────────────────────────────────
# The pattern list once had no BBCode. A BBCode-heavy file reported zero
# issues, which read as "clean" and meant "never checked".

def test_bbcode_is_extracted():
    """BBCode must be recognised as structural tags, or losing one is invisible."""
    text = "[color=#ff0000]警告[/color]：请[b]立即[/b]撤离"
    found = vt.STRUCTURAL_RE.findall(text)
    for expect in ("[color=#ff0000]", "[/color]", "[b]", "[/b]"):
        assert any(expect in f for f in found), f"BBCode {expect} not recognised, found={found}"


def test_bbcode_loss_is_detected():
    """Target lost a closing BBCode tag; this must be reported."""
    src = "[color=#ff0000]Warning[/color]: evacuate [b]now[/b]"
    tgt = "[color=#ff0000]警告：请立即撤离"   # lost [/color] and [b]...[/b]
    s_tags = vt.STRUCTURAL_RE.findall(src)
    t_tags = vt.STRUCTURAL_RE.findall(tgt)
    assert len(s_tags) > len(t_tags), f"BBCode loss not caught: src={s_tags} tgt={t_tags}"


def test_plain_brackets_are_not_bbcode():
    """The other direction: ordinary square brackets must not become tags."""
    for text in ("见 [1] 注释", "[TODO] 待确认", "数值 [0-9] 区间", "[s01]"):
        found = vt.STRUCTURAL_RE.findall(text)
        assert not "".join(found), f"plain brackets taken as BBCode: {text!r} -> {found}"


# ── Number regex ─────────────────────────────────────────────────────────

def test_number_regex_requires_a_digit():
    """`[\\d.]+%?` treated English periods and ellipses as numbers.

    On a real 525-tag file that made 116 false "number mismatch" flags: any
    difference in the number of periods between source and target fired it.
    A number must contain at least one digit.
    """
    for text, expected in [
        ("Wait... what?", []),
        ("He said. Then left.", []),
        ("a.b.c", []),
        ("Level 3.5 boss", ["3.5"]),
        ("50% off", ["50%"]),
        ("v1.2.3 released", ["1.2.3"]),
        ("第 3 章，共 12 章", ["3", "12"]),
    ]:
        got = tsr.NUMBER_RE.findall(text)
        assert got == expected, f"{text!r} -> {got}, expected {expected}"


# ── Redaction blocks ─────────────────────────────────────────────────────

def test_redaction_runs_splits_by_break():
    """Runs must be reported as a length sequence, not a total."""
    assert vt.redaction_runs("■■■a■■") == [3, 2]
    assert vt.redaction_runs("没有遮蔽块") == []


def test_redaction_total_same_but_breaks_moved_is_caught():
    """Same total, different break points. A total would miss it; run by run catches it."""
    pairs = [{"id": "s1", "source": "A■■■B■■C", "target": "甲■■乙■■■丙"}]
    issues = vt.verify_redaction_runs(pairs)
    assert len(issues) == 1 and issues[0].issue_type == "REDACTION_RUN_MISMATCH", issues
    assert issues[0].severity == "CRITICAL"


def test_redaction_identical_runs_pass():
    pairs = [{"id": "s1", "source": "A■■■B■■C", "target": "甲■■■乙■■丙"}]
    assert not vt.verify_redaction_runs(pairs)


def test_redaction_long_sequence_reconciles():
    """A real run-length sequence that a reviewing model once miscounted.

    The script must report zero difference, so its verdict can go into the
    prompt as an established fact instead of hoping the model will not count.
    """
    runs = [16, 13, 15, 18, 7, 18, 12, 11, 21, 17, 5]
    src = "x".join("■" * n for n in runs)
    tgt = "。".join("■" * n for n in runs)
    assert vt.redaction_runs(src) == runs
    assert not vt.verify_redaction_runs([{"id": "962", "source": src, "target": tgt}])


def test_redaction_note_states_fact_when_clean():
    """When the runs match, the note must say so as a settled fact and tell the model not to count."""
    pairs = [{"id": "s1", "source": "A■■■B", "target": "甲■■■乙"}]
    note = vt.redaction_reconciliation_note(pairs)
    assert "Established fact" in note and "match exactly" in note
    assert "do not count" in note


def test_redaction_note_refuses_when_mismatched():
    """When the runs differ, the note must NOT claim they were reconciled."""
    pairs = [{"id": "s1", "source": "A■■■B", "target": "甲■乙"}]
    note = vt.redaction_reconciliation_note(pairs)
    assert "FAILED" in note and "Established fact" not in note


# ── Angle brackets in dialogue vs real tags ──────────────────────────────
# memoQ stores player-visible stage directions like <grunts> as escaped
# text. Parsed, they look like unknown opening tags. The old patterns
# swallowed both: `<g[^>]*>` matched <grunts>, and the generic `<[^>]+>`
# took any angle-bracket prose as a tag. Rule: an unknown angle token is a
# tag only with tag-shaped evidence (a closing mate, or attributes).

def test_dialogue_direction_is_not_a_tag():
    for text in ("He <grunts> and turns away.", "She <sighs>, then leaves.", "<groans>"):
        assert vt.extract_tags(text) == [], f"stage direction taken as tag: {text}"
    assert vt.extract_tags("他<咳嗽了两声>然后走开") == []


def test_unknown_paired_tag_is_still_a_tag():
    """Unknown name with a closing mate: tag-shaped evidence, still a tag."""
    assert vt.extract_tags("A <custom>x</custom> B") == ["<custom>", "</custom>"]


def test_attribute_bearing_unknown_tag_is_still_a_tag():
    """Prose does not write key=value. Attributes mean tag, closing mate or not."""
    assert vt.extract_tags('A <font color="red">x</font> B') == [
        '<font color="red">', "</font>",
    ]
    assert vt.extract_tags('A <font color="red"> B') == ['<font color="red">']


def test_memoq_g_tag_not_shadowed_by_g_prefixed_words():
    """<g> is a real memoQ tag and must survive the <grunts> fix (that is what `<g\\b` is for)."""
    assert vt.extract_tags('Press <g id="1">OK</g> now.') == ['<g id="1">', "</g>"]
    assert vt.extract_tags("Press <g>OK</g>.") == ["<g>", "</g>"]


def test_direction_word_does_not_trigger_count_mismatch_end_to_end():
    """Through verify_all, not only extract_tags: a test that skips the entry point
    cannot notice when the call site is removed."""
    clean = vt.verify_all([
        {"id": "s1", "source": "He <grunts> and leaves.", "target": "他哼了一声就走了。"}
    ])
    assert clean == [], f"stage direction caused a false alarm: {clean}"
    dirty = vt.verify_all([
        {"id": "s2", "source": 'Press <g id="1">OK</g>.', "target": "请按 OK。"}
    ])
    assert dirty and dirty[0].issue_type == "TAG_COUNT_MISMATCH", "a real lost tag was let through"


# ── Span boundaries (semantic report) ────────────────────────────────────
# Every example below is made up, shaped like real segments that shipped
# with every gate green.

def _rows(src, tgt, glossary=None):
    return tsr.align_spans(tsr.extract_spans(src), tsr.extract_spans(tgt),
                           glossary if glossary is not None else [])


def _flags(src, tgt, glossary=None):
    return [f for _, _, _, f in _rows(src, tgt, glossary) if f]


SAMPLE_GLOSSARY = [
    {"source": "属性", "target": "Element", "status": "approved"},
    {"source": "技能点", "target": "Skill Points", "status": "approved"},
    {"source": "攻击力", "target": "ATK", "status": "approved"},
    {"source": "物理攻击", "target": "ATK", "status": "approved"},
    {"source": "全属性加成", "target": "All Stat Bonus", "status": "approved"},
    {"source": "疾风之箭", "target": "Gale Arrow", "status": "approved"},
]


def test_span_shrink_is_caught():
    """Source highlights 当前队长; target only wraps "leader", "current" fell outside.

    Count, order and nesting are all legal; the wrapped text has no numbers
    and no glossary term. The older two flags were silent.
    """
    flags = _flags("队伍会跟随<color=#ffe2af>当前队长</color>的移动",
                   "The party follows the current <color=#ffe2af>leader</color>",
                   SAMPLE_GLOSSARY)
    assert any("shrunk" in f for f in flags), f"shrunken span let through: {flags}"


def test_span_expand_is_caught():
    """Source closes </color> right after {*val*}; 技能点 is not highlighted.
    Target pulled "Skill Points" inside the pair."""
    flags = _flags("下次升级<color=#ffe2af>增加{*val*}</color>技能点",
                   "The next level-up <color=#ffe2af>grants {*val*} Skill Points</color>",
                   SAMPLE_GLOSSARY)
    assert flags, "expanded span let through"


def test_empty_target_span_is_caught():
    """The target pair sits empty, with the word right after it."""
    flags = _flags("使<style=\"q5\">燃烧</style>延长",
                   "extends <style=\"q5\"></style>Burning by")
    assert any("empty target span" in f for f in flags), f"empty span let through: {flags}"


def test_variable_placeholder_not_counted_as_word():
    """{*element*}系 -> {*element*} Element is right. Counting "element" as a word would flag it."""
    assert not _flags("<color=#ffe2af>{*element*}系</color>技能",
                      "<color=#ffe2af>{*element*} Element</color> Skills",
                      SAMPLE_GLOSSARY)


def test_function_word_expansion_is_not_flagged():
    """必定 -> is guaranteed to: auxiliary words are a legitimate expansion, not a wider span."""
    assert not _flags("下次抽卡<color=#CFA803>必定</color>出现稀有",
                      "The next draw <color=#CFA803>is guaranteed to</color> contain a rare")


def test_abbreviation_is_exempted_by_glossary():
    """攻击力 -> ATK: an abbreviation's ratio is off by nature; the glossary exempts it."""
    assert not _flags("<style=\"accent-gn\">攻击力</style>提升",
                      "<style=\"accent-gn\">ATK</style> increased", SAMPLE_GLOSSARY)


def test_plural_and_hyphen_variants_are_same_term():
    """Gale Arrow/Arrows and All Stat/All-Stat Bonus are the same term, not a mismatch."""
    assert not _flags("<style=\"x\">疾风之箭</style>命中",
                      "<style=\"x\">Gale Arrows</style> hit", SAMPLE_GLOSSARY)
    assert not _flags("<style=\"x\">全属性加成</style>提升",
                      "<style=\"x\">All-Stat Bonus</style> up", SAMPLE_GLOSSARY)


def test_short_glossary_entry_does_not_hijack_longer_one():
    """Longest match: with 伤害 -> Attack DMG and 物理伤害 -> Physical DMG both in the table,
    a target saying Physical DMG must not be flagged by the short entry."""
    gloss = [{"source": "伤害", "target": "Attack DMG", "status": "approved"},
             {"source": "物理伤害", "target": "Physical DMG", "status": "approved"}]
    assert not _flags("<style=\"x\">物理伤害</style>提升",
                      "<style=\"x\">Physical DMG</style> up", gloss)
    # And a real mismatch is still reported, or the clean case above proves nothing.
    assert _flags("<style=\"x\">物理伤害</style>提升",
                  "<style=\"x\">Magic DMG</style> up", gloss)


def test_number_mismatch_still_takes_priority():
    """The number rule must not be crowded out by the newer rules."""
    flags = _flags("每<style=\"accent-gn\">1秒</style>使",
                   "every <style=\"accent-gn\">, </style>1s makes")
    assert any("number" in f for f in flags), f"number mismatch was crowded out: {flags}"


# ── ICU conditional variables ────────────────────────────────────────────

def test_icu_option_text_may_be_translated():
    """Option text is meant to be translated. It is not part of the tag's identity."""
    issues = vt.verify_segment(
        "1", "Gain {Gems:plural:Gem|Gems}.", "获得{Gems:plural:宝石|宝石}。")
    assert issues == [], issues


def test_icu_dropped_in_chinese_is_warning_not_critical():
    issues = vt.verify_segment("1", "Gain {Gems:plural:Gem|Gems}.", "获得宝石。")
    assert [i.severity for i in issues] == ["WARNING"], issues
    assert issues[0].issue_type == "ICU_CONDITIONAL_DROPPED"


def test_icu_resolved_to_bare_variable_is_warning():
    """{Amount:plural:...} -> {Amount}: the common Chinese shape."""
    issues = vt.verify_segment(
        "1", "Pick {Amount:plural:an item|{} items} to sell.",
        "选择{Amount}件物品出售。")
    assert [i.issue_type for i in issues] == ["ICU_CONDITIONAL_DROPPED"], issues


def test_icu_added_in_english_is_warning_with_engine_caveat():
    issues = vt.verify_segment("1", "选择{Amount}件物品。",
                               "Pick {Amount:plural:an item|{} items}.")
    assert [i.issue_type for i in issues] == ["ICU_CONDITIONAL_ADDED"], issues
    assert "engine" in issues[0].detail


def test_plain_variable_loss_is_still_critical():
    """A lost plain {X} is still CRITICAL. The downgrade applies to conditionals only."""
    issues = vt.verify_segment("1", "Gain {X} now.", "现在获得。")
    assert any(i.severity == "CRITICAL" for i in issues), issues


def test_nested_braces_extract_as_one_tag():
    """`{InBattle:(Repeats {X:diff()} times)|}` is one tag, not two truncated ones."""
    tags = vt.extract_tags("Deal damage.{InBattle:\n(Repeats {RepeatCount:diff()} times)|}")
    assert tags == ["{InBattle:\n(Repeats {RepeatCount:diff()} times)|}"], tags


def test_plural_only_conditional_cannot_absorb_lost_bare_variable():
    """{Amount:plural:turn|turns} prints a word, not the number. It must not cancel a
    missing {Amount}, or the number vanishes from the target with every check green."""
    issues = vt.verify_segment(
        "1", "{Amount}回合后爆炸。", "Explodes after {Amount:plural:turn|turns}.")
    assert any(i.severity == "CRITICAL" for i in issues), issues


def test_value_carrying_conditional_still_absorbs_bare_variable():
    """A branch containing {} prints the number, so it may stand in for the bare variable."""
    issues = vt.verify_segment("1", "选择{Amount}件物品。",
                               "Pick {Amount:plural:an item|{} items}.")
    assert [i.issue_type for i in issues] == ["ICU_CONDITIONAL_ADDED"], issues


# ── Project-specific BBCode ──────────────────────────────────────────────

def test_custom_bbcode_detected_by_pairing():
    found = vt.detect_paired_bbcode(["Gain [gold]Shield[/gold] and [wobble]shake[/wobble]"])
    assert found == {"gold", "wobble"}, found


def test_bare_bracket_text_is_not_taken_as_tag():
    """[TODO] / [1] / [s01] have no closing mate and must not be picked up."""
    assert vt.detect_paired_bbcode(["See [TODO] and [1] and [s01] here."]) == set()


def test_custom_tag_loss_is_caught_after_auto_detect():
    vt.set_custom_tags([])                       # start clean; tests share module state
    pairs = [{"id": "1", "source": "Gain [gold]Shield[/gold] 5.",
              "target": "获得护盾 5。"},
             {"id": "2", "source": "Lose [gold]Gold[/gold].",
              "target": "失去[gold]金币[/gold]。"}]
    ids = {i.seg_id for i in vt.verify_all(pairs) if i.severity == "CRITICAL"}
    assert ids == {"1"}, ids
    vt.set_custom_tags([])


def test_explicit_tags_flag_covers_self_closing():
    vt.set_custom_tags(["shake"])
    assert vt.extract_tags("a [shake] b") == ["[shake]"]
    vt.set_custom_tags([])


def test_inline_order_change_is_warning_not_critical():
    """Chinese word order moved [blue] and [gold] around. Normal translation; must not block delivery."""
    vt.set_custom_tags(["gold", "blue"])
    issues = vt.verify_segment(
        "1", "Find [blue]25%[/blue] more [gold]Gold[/gold].",
        "找到的[gold]金币[/gold]增加[blue]25%[/blue]。")
    assert [(i.severity, i.issue_type) for i in issues] == [
        ("WARNING", "INLINE_TAG_ORDER_CHANGED")], issues
    vt.set_custom_tags([])


def test_bbcode_crossed_nesting_is_critical():
    """[blue]word[/gold] is crossed nesting. The renderer breaks on it. It must not share a
    WARNING with "word order changed"."""
    issues = vt.verify_segment(
        "1", "[gold]a[/gold][blue]b[/blue]", "[blue]甲[/gold][gold]乙[/blue]")
    kinds = {i.issue_type for i in issues}
    assert "TAG_NESTING_ERROR" in kinds, issues
    assert all(i.severity == "CRITICAL"
               for i in issues if i.issue_type == "TAG_NESTING_ERROR")


def test_bbcode_reorder_without_crossing_stays_warning():
    """A whole pair moving is not an error: the wrapped word moved with the sentence."""
    try:
        vt.set_custom_tags({"gold", "blue"})
        issues = vt.verify_segment(
            "1", "[blue]25%[/blue] more [gold]Gold[/gold]",
            "[gold]金币[/gold]增加[blue]25%[/blue]")
    finally:
        vt.set_custom_tags(set())
    assert [i.issue_type for i in issues] == ["INLINE_TAG_ORDER_CHANGED"], issues
    assert issues[0].severity == "WARNING"


def test_bbcode_unpaired_name_is_not_nesting_error():
    """A bracket name that never closes in this segment is treated as self-closing."""
    assert vt.verify_segment("1", "[TODO] fix", "[TODO] 待修") == []


def test_bbcode_nesting_tolerates_icu_branches():
    """One [gold] opened outside the braces, each of two branches carries its own [/gold].
    Flattened: 1 open, 2 closes. Per branch: balanced. Must not be an error."""
    text = ("[gold]{Count:plural:{U:show:Torch+|Torch}[/gold]"
            "|{U:show:Torches+|Torches}[/gold]}.")
    assert vt.check_nesting(text) == []


# ── Line-break padding ───────────────────────────────────────────────────

def test_break_padding_anchored_to_source():
    """Extra spaces around <br> in the target are reported; spaces already in the source are not."""
    issues = vt.check_break_padding("1", "第一行<br>第二行", "Line one <br> line two")
    assert [i.issue_type for i in issues] == ["BREAK_TAG_PADDING"], issues
    assert vt.check_break_padding("2", "line one <br> line two", "第一行 <br> 第二行") == []


# ── pairs_io ─────────────────────────────────────────────────────────────

def _write(td, name, obj):
    p = Path(td) / name
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return p


def test_pairs_missing_target_key_is_an_error_not_a_pass():
    """A record with no 'target' key used to pass silently. It must raise."""
    with tempfile.TemporaryDirectory() as td:
        p = _write(td, "pairs.json", [{"id": "1", "source": "<b>x</b>"}])
        try:
            pairs_io.load_pairs(p)
        except pairs_io.PairsError as exc:
            assert "target" in str(exc)
        else:
            raise AssertionError("missing target key was accepted")


def test_pairs_duplicate_ids_are_an_error():
    with tempfile.TemporaryDirectory() as td:
        p = _write(td, "pairs.json", [
            {"id": "1", "source": "a", "target": "b"},
            {"id": "1", "source": "c", "target": "d"}])
        try:
            pairs_io.load_pairs(p)
        except pairs_io.PairsError as exc:
            assert "duplicate" in str(exc)
        else:
            raise AssertionError("duplicate ids were accepted")


def test_pairs_translatable_false_allows_empty_target():
    with tempfile.TemporaryDirectory() as td:
        p = _write(td, "pairs.json", [
            {"id": "1", "source": "DO NOT TRANSLATE", "target": "", "translatable": False}])
        rows = pairs_io.load_pairs(p, normalize=True)
        assert rows == [{"id": "1", "source": "DO NOT TRANSLATE", "target": ""}]


# ── CLI end to end ───────────────────────────────────────────────────────

def _cli(*args):
    return subprocess.run([sys.executable, "-m", "tag_transfer.cli", *args],
                          capture_output=True, text=True, cwd=str(REPO))


def test_verify_cli_with_pairs_file():
    """`verify --pairs` runs without memoQ, exits 1 on CRITICAL, 2 on bad input,
    and writes the semantic report when asked."""
    assert _cli("verify", "--help").returncode == 0
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        pairs = _write(td, "pairs.json", [
            {"id": "1", "source": "<b>攻击</b>提升", "target": "<b>ATK</b> up"},
            {"id": "2", "source": "<b>防御</b>提升", "target": "DEF up"}])
        r = _cli("verify", "--pairs", str(pairs), "--format", "json")
        assert r.returncode == 1 and '"seg_id": "2"' in r.stdout, r.stdout + r.stderr

        clean = _write(td, "clean.json", [
            {"id": "1", "source": "<b>攻击</b>提升", "target": "<b>ATK</b> up"}])
        r = _cli("verify", "--pairs", str(clean), "--semantic-report", str(td / "r.md"))
        assert r.returncode == 0, r.stdout + r.stderr
        assert "## Segment 1" in (td / "r.md").read_text(encoding="utf-8")

        bad = _write(td, "bad.json", [{"id": "1", "source": "x"}])
        r = _cli("verify", "--pairs", str(bad))
        assert r.returncode == 2 and "target" in r.stderr, r.stdout + r.stderr


def test_verify_cli_glossary_reaches_semantic_report():
    """--glossary must actually be passed through to the report, not only parsed."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        pairs = _write(td, "pairs.json", [
            {"id": "1", "source": "<b>攻击力</b>提升", "target": "<b>Speed</b> up"}])
        gloss = _write(td, "glossary.json", [{"source": "攻击力", "target": "ATK"}])
        r = _cli("verify", "--pairs", str(pairs), "--glossary", str(gloss),
                 "--semantic-report", str(td / "r.md"))
        assert r.returncode == 0, r.stdout + r.stderr
        assert "term mismatch" in (td / "r.md").read_text(encoding="utf-8")


# ── The README gallery ───────────────────────────────────────────────────

def test_examples_file_is_a_working_demo():
    """examples/pairs.json is the README's "it looks fine, it is not" gallery.

    If a change to verify.py or semantic_report.py stops any of the six cases
    from firing, the README starts promising something the tool no longer
    does. This test is what notices.
    """
    pairs = pairs_io.load_pairs(REPO / "examples" / "pairs.json", normalize=True)
    vt.set_custom_tags([])
    try:
        issues = vt.verify_all(pairs)
    finally:
        vt.set_custom_tags([])
    kinds = {}
    for i in issues:
        kinds.setdefault(i.seg_id, set()).add(i.issue_type)
    assert "TAG_COUNT_MISMATCH" in kinds.get("5", set()), kinds     # 4: custom tag lost
    assert "TAG_NESTING_ERROR" in kinds.get("6", set()), kinds      # 5: crossed
    assert kinds.get("7") == {"ICU_CONDITIONAL_DROPPED"}, kinds     # 6: warning only
    for quiet in ("1", "2", "3", "4", "8"):
        assert quiet not in kinds, (quiet, kinds.get(quiet))       # mechanical checks stay silent

    gloss = pairs_io.load_glossary(REPO / "examples" / "glossary.json")
    report = tsr.build_report(pairs, gloss)
    sections = dict(re.findall(r"## Segment (\d+)\n(.*?)(?=\n## |\Z)", report, re.S))
    assert "number mismatch" in sections["2"], sections["2"]        # 1
    assert "shrunk" in sections["3"], sections["3"]                 # 2
    assert "empty target span" in sections["4"], sections["4"]      # 3
    assert "term mismatch" in sections["8"], sections["8"]          # glossary demo
    assert "⚠️" not in sections["1"] and "❌" not in sections["1"], sections["1"]


# ── TMX output: escaping, namespaces, self-check ─────────────────────────
# A real run produced a TMX whose source side was escaped and whose target
# side was not. The file was not well-formed XML, so memoQ refused the
# import — and every in-memory check had reported "all segments passed",
# because nothing ever parsed the file that got written.

def test_tmx_escapes_the_target_side():
    """An & or < in the translation must not break the TMX."""
    src = _source_el('A <ph id="1">&lt;b&gt;</ph>B')
    seg = output.build_tmx_seg(src, "Salt {1} Pepper & 5 < 6")
    assert "&amp;" in seg and "&lt; 6" in seg, seg
    etree.fromstring(f"<seg>{seg}</seg>".encode("utf-8"))   # raises if broken


def test_tmx_source_and_target_escape_the_same_way():
    """The asymmetry was the bug: one side escaped, the other raw."""
    src = _source_el("Tom &amp; Jerry")
    assert output.build_full_seg(src) == output.escape_xml("Tom & Jerry")
    assert output.build_tmx_seg(src, "Tom & Jerry") == output.escape_xml("Tom & Jerry")


def test_unused_namespace_declaration_is_dropped():
    """lxml prints every in-scope namespace; xmlns:mq landed on every tag."""
    src = _source_el('x<ph id="1">&lt;b&gt;</ph>')
    assert "xmlns:mq" not in output.get_tag_xml_str(src, "1")


def test_used_namespace_declaration_is_kept():
    """The mirror case: memoQ <ph> really does contain <mq:rxt>, and stripping
    a declaration that IS used would make the fragment invalid."""
    src = _source_el('<ph id="1"><mq:rxt displaytext="&lt;br&gt;"/></ph>')
    tag = output.get_tag_xml_str(src, "1")
    assert "mq:rxt" in tag and 'xmlns:mq="MQXliff"' in tag, tag
    etree.fromstring(tag.encode("utf-8"))


def test_missing_tag_leaves_a_visible_placeholder():
    """A tag id with no match used to vanish silently. Silent loss is the one
    failure this project treats as worse than a loud one."""
    src = _source_el('x<ph id="1">&lt;b&gt;</ph>')
    assert output.build_tmx_seg(src, "a {9} b") == "a {9} b"


def test_generate_tmx_rejects_output_it_cannot_parse():
    """The guard itself: break escaping on purpose, the writer must notice."""
    src = _source_el("hi")
    results = [{"src_el": src, "src_text": "hi", "tgt_template": "a & b"}]
    original = output.escape_xml
    output.escape_xml = lambda text: text          # reintroduce the old bug
    try:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "broken.tmx"
            try:
                output.generate_tmx(results, str(out))
            except output.TmxValidationError as exc:
                assert "not well-formed" in str(exc), exc
            else:
                raise AssertionError("invalid TMX was written without complaint")
    finally:
        output.escape_xml = original


def test_generate_tmx_writes_parseable_file():
    src = _source_el(
        'Deal <ph id="1">&lt;b&gt;</ph>50<ph id="2">&lt;/b&gt;</ph> damage &amp; heal')
    results = [{"src_el": src, "src_text": "Deal {1}50{2} damage & heal",
                "tgt_template": "Inflige {1}50{2} de dégâts & soigne"}]
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "ok.tmx"
        output.generate_tmx(results, str(out))
        assert output.validate_tmx(out) == []
        assert f'creationtoolversion="{__import__("tag_transfer").__version__}"' \
            in out.read_text(encoding="utf-8")


# ── Placement: warnings must not travel inside the text ──────────────────
# The warning used to be prepended to the returned string and the caller
# recovered the text with split("\n")[-1]. Any genuinely multi-line target
# lost everything above its last newline.

def test_multiline_target_survives():
    """split("\\n")[-1] returned only "second line" and lost the rest."""
    tagged = "first line {1}\nsecond line"
    assert place._strip_model_preamble(tagged, "first line\nsecond line") == tagged


def test_model_preamble_is_stripped():
    """The other job that split("\\n")[-1] was doing, kept on purpose."""
    got = place._strip_model_preamble("Sure, here you go:\nHello {1} world", "Hello world")
    assert got == "Hello {1} world", got


def test_preamble_stripping_does_not_touch_a_reworded_target():
    """When the reply matches neither shape, return it whole and let the tag
    count report the problem. Guessing which line to keep is how content
    disappears."""
    odd = "I changed the wording {1} entirely"
    assert place._strip_model_preamble(odd, "Hello world") == odd


def test_place_tags_returns_warnings_separately():
    """No tags to place: the text comes back untouched, warnings empty."""
    assert place.place_tags("plain", [], "texte") == ("texte", [])


# ── Auto-detected tag names are scoped to their batch ────────────────────

def test_auto_detected_tags_do_not_leak_into_the_next_batch():
    """verify_all wrote the names it detected into module state and never put
    it back, so a second batch was checked against the first batch's tags."""
    vt.set_custom_tags([])
    try:
        vt.verify_all([{"id": "1", "source": "[gold]x[/gold]", "target": "[gold]y[/gold]"}])
        assert vt.custom_tags() == set(), vt.custom_tags()
        leftover = vt.verify_all(
            [{"id": "2", "source": "see [gold] below", "target": "see below"}],
            auto_detect_tags=False)
        assert leftover == [], [i.issue_type for i in leftover]
    finally:
        vt.set_custom_tags([])


def test_explicit_tags_survive_a_batch():
    """--tags is the caller's instruction; only auto-detection is batch-scoped."""
    vt.set_custom_tags(["jitter"])
    try:
        vt.verify_all([{"id": "1", "source": "[gold]x[/gold]", "target": "[gold]y[/gold]"}])
        assert vt.custom_tags() == {"jitter"}, vt.custom_tags()
    finally:
        vt.set_custom_tags([])


def test_verify_all_rejects_a_missing_key():
    """Callers that build pairs themselves skip pairs_io. Treating the missing
    key as an empty string would report the segment clean, which is the silent
    pass pairs_io exists to prevent."""
    try:
        vt.verify_all([{"id": "seg-7", "source": "plain text"}])
    except pairs_io.PairsError as exc:
        assert "seg-7" in str(exc) and "target" in str(exc), exc
    else:
        raise AssertionError("a record with no target key was reported clean")


# ── Archive extraction stays inside the work dir ─────────────────────────

def test_zip_slip_is_refused():
    """An .mqxlz is a ZIP from outside the tool."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        bad = td / "evil.mqxlz"
        with zipfile.ZipFile(bad, "w") as z:
            z.writestr("../escaped.txt", "nope")
        try:
            extract.extract_mqxlz(str(bad), str(td / "wd"))
        except ValueError as exc:
            assert "outside" in str(exc), exc
        else:
            raise AssertionError("a member outside the work dir was extracted")
        assert not (td.parent / "escaped.txt").exists()


if __name__ == "__main__":
    # Discovered automatically, not listed by hand: a hardcoded call list
    # silently skips any test added later.
    ran = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            ran += 1
    print(f"PASS ({ran} tests)")
