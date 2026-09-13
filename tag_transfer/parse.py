"""Parse trans-units: extract source/target text and classify inline tags."""

import html
import re

from lxml import etree

from .extract import NS


# Style names invented by one project's text system, not part of any standard.
# A different project ships different names, so this is data, not control flow:
# replace or extend PROJECT_STYLES instead of editing classify_tag. Anything
# not listed here still resolves through _infer_unknown_tag.
PROJECT_STYLES = {
    "accent-gn": ("gn_open", "green highlight start"),
    "physical": ("phys_open", "physical damage color start"),
    "ItemQuality_5": ("q5_open", "skill link style start"),
    "tipsYellow": ("tip_open", "tips title style start"),
    "text_third_gray": ("gray_open", "gray description style start"),
}


_DISPLAYTEXT_RE = re.compile(r'displaytext="([^"]*)"')


def rxt_displaytext(ph_element):
    """The rxt displaytext of a <ph>, or None when there is no rxt at all.

    memoQ writes the rxt two ways and both turn up in real files: as a child
    element, and as escaped text inside the <ph>:

        <ph id="3">&lt;mq:rxt displaytext=&quot;&amp;lt;br&amp;gt;&quot; ...&gt;</ph>

    Only the child form was handled, so on a file written the second way
    find() returned None and every single <ph> classified as "unknown" —
    1323 of them on one real UI file, every <br> and <strong> among them.
    The LLM then got "unknown" plus a line of raw XML where it should have
    read "line break".

    Both forms are unescaped to the same string, so the callers below can
    keep matching on plain "<br>" and 'style="..."'.
    """
    rxt = ph_element.find("{MQXliff}rxt")
    if rxt is not None:
        return rxt.get("displaytext", "")
    text = ph_element.text or ""
    if "mq:rxt" not in text:
        return None
    m = _DISPLAYTEXT_RE.search(text)
    return html.unescape(m.group(1)) if m else ""


def classify_tag(ph_element):
    """Identify the semantic type of a <ph> element. Returns (type, detail)."""
    dt = rxt_displaytext(ph_element)
    if dt is None:
        return ("unknown", ph_element.text or "")

    for style_name, result in PROJECT_STYLES.items():
        if f'style="{style_name}"' in dt:
            return result
    if "</style>" in dt:
        return ("style_close", "style close")
    if "linktext=" in dt:
        code = dt.split("linktext=")[1].split("&")[0].split('"')[0].split(">")[0]
        return ("link_open", f"skill link start code={code}")
    if "</linktext>" in dt:
        return ("link_close", "skill link close")
    if "<br>" in dt:
        return ("br", "line break")
    if "<i>" in dt:
        return ("italic_open", "italic start")
    if "</i>" in dt:
        return ("italic_close", "italic close")
    if "size=" in dt and "/size" not in dt:
        size = dt.split("size=")[1].split("&")[0].split('"')[0].split(">")[0]
        return ("size_open", f"font size start size={size}")
    if "</size>" in dt:
        return ("size_close", "font size close")

    return _infer_unknown_tag(dt)


def _infer_unknown_tag(displaytext):
    """Try to infer the semantic type of an unknown tag from its displaytext."""
    dt = displaytext.lower()

    if 'style="' in dt and "</style>" not in dt:
        style_name = dt.split('style="')[1].split('"')[0]
        return ("style_open", f"unknown style '{style_name}'")

    if "linktext=" in dt or "link=" in dt:
        return ("link_open", "unknown link type")

    if dt.startswith("<") and "=" not in dt and "/" not in dt:
        tag = dt.strip("<>").split()[0]
        return ("html_open", f"HTML tag <{tag}>")
    if dt.startswith("</"):
        return ("html_close", "HTML close tag")

    return ("unknown", displaytext)


def _process_inline_tag(child, parts, tags):
    """Process a single inline tag element (ph, bpt, ept, x, g)."""
    tag_name = etree.QName(child.tag).localname if "}" in child.tag else child.tag

    if tag_name == "ph":
        tid = child.get("id", "?")
        tag_type, detail = classify_tag(child)
        parts.append(f"{{{tid}}}")
        tags.append({"id": tid, "type": tag_type, "detail": detail, "tag_name": "ph"})
    elif tag_name == "bpt":
        tid = child.get("id", child.get("i", "?"))
        inner = child.text or ""
        parts.append(f"{{{tid}}}")
        tags.append({"id": tid, "type": "bpt", "detail": f"paired tag open: {inner[:40]}", "tag_name": "bpt"})
    elif tag_name == "ept":
        tid = child.get("id", child.get("i", "?"))
        inner = child.text or ""
        parts.append(f"{{{tid}}}")
        tags.append({"id": tid, "type": "ept", "detail": f"paired tag close: {inner[:40]}", "tag_name": "ept"})
    elif tag_name == "x":
        tid = child.get("id", "?")
        parts.append(f"{{{tid}}}")
        tags.append({"id": tid, "type": "standalone", "detail": "standalone placeholder", "tag_name": "x"})
    elif tag_name == "g":
        tid = child.get("id", "?")
        parts.append(f"{{{tid}}}")
        tags.append({"id": tid, "type": "g_open", "detail": "group tag open", "tag_name": "g"})


def simplify_segment(el):
    """Convert a source/target element to simplified text with {N} placeholders.

    Returns (simplified_text, list_of_tag_info_dicts).
    """
    if el is None:
        return "", []

    parts = []
    tags = []

    if el.text:
        parts.append(el.text)

    for child in el:
        tag_name = etree.QName(child.tag).localname if "}" in child.tag else child.tag

        if tag_name == "mrk":
            tctype = child.get("{MQXliff}tctype", "")
            if tctype == "del":
                pass
            else:
                if child.text:
                    parts.append(child.text)
                for sub in child:
                    _process_inline_tag(sub, parts, tags)
                    if sub.tail:
                        parts.append(sub.tail)
        else:
            _process_inline_tag(child, parts, tags)

        if child.tail:
            parts.append(child.tail)

    return "".join(parts), tags


def extract_segments(units):
    """Extract all source/target segments from trans-units.

    Returns list of dicts with keys:
        id, src_text, src_tags, tgt_text, tgt_tags, src_el, tgt_el
    """
    results = []
    for unit in units:
        uid = unit.get("id", "")
        src_el = unit.find("x:source", NS)
        tgt_el = unit.find("x:target", NS)
        src_text, src_tags = simplify_segment(src_el)
        tgt_text, tgt_tags = simplify_segment(tgt_el)
        results.append({
            "id": uid,
            "src_text": src_text,
            "src_tags": src_tags,
            "tgt_text": tgt_text,
            "tgt_tags": tgt_tags,
            "src_el": src_el,
            "tgt_el": tgt_el,
        })
    return results
