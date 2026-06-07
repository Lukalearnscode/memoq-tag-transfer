"""Generate TMX files from tag-transferred segments."""

import re

from lxml import etree


def escape_xml(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


INLINE_TAG_NAMES = {"ph", "bpt", "ept", "x", "g"}


def _clean_xmlns(raw):
    raw = raw.replace(' xmlns="urn:oasis:names:tc:xliff:document:1.2"', "")
    raw = re.sub(r' xmlns:ns\d+="[^"]*"', "", raw)
    raw = re.sub(r"<ns\d+:", "<", raw)
    raw = re.sub(r"</ns\d+:", "</", raw)
    return raw


def get_tag_xml_str(src_el, tag_id):
    """Extract clean XML string for an inline tag by its id attribute."""
    for child in src_el:
        tag_name = etree.QName(child.tag).localname if "}" in child.tag else child.tag
        if tag_name not in INLINE_TAG_NAMES:
            continue
        child_id = child.get("id") or child.get("i")
        if child_id == str(tag_id):
            raw = etree.tostring(child, encoding="unicode", with_tail=False)
            return _clean_xmlns(raw)
    return ""


def build_full_seg(el):
    """Build complete seg content from a source element (text + inline tag XML)."""
    if el is None:
        return ""
    parts = []
    if el.text:
        parts.append(escape_xml(el.text))
    for child in el:
        tag_name = etree.QName(child.tag).localname if "}" in child.tag else child.tag
        if tag_name in INLINE_TAG_NAMES:
            raw = etree.tostring(child, encoding="unicode", with_tail=False)
            parts.append(_clean_xmlns(raw))
        if child.tail:
            parts.append(escape_xml(child.tail))
    return "".join(parts)


def build_tmx_seg(src_el, template):
    """Replace {N} placeholders in template with actual tag XML from source."""
    def replacer(m):
        return get_tag_xml_str(src_el, m.group(1))
    return re.sub(r"\{(\d+)\}", replacer, template)


def generate_tmx(results, output_path, src_lang="zh-CN", tgt_lang="en-US"):
    """Generate a TMX file from processed segments.

    Args:
        results: list of dicts, each with:
            - src_el: lxml source element
            - src_text: source text with {N} placeholders
            - tgt_template: target text with {N} placeholders
        output_path: where to write the TMX file
        src_lang: source language code
        tgt_lang: target language code
    """
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<tmx version="1.4">',
        '  <header creationtool="memoq-tag-transfer" creationtoolversion="0.1.0"',
        '          segtype="sentence" o-tmf="memoQ" adminlang="en-US"',
        f'          srclang="{src_lang}" datatype="plaintext"/>',
        "  <body>",
    ]

    for r in results:
        src_seg = build_full_seg(r["src_el"])
        tgt_seg = build_tmx_seg(r["src_el"], r["tgt_template"])

        lines.append("    <tu>")
        lines.append(f'      <tuv xml:lang="{src_lang}">')
        lines.append(f"        <seg>{src_seg}</seg>")
        lines.append("      </tuv>")
        lines.append(f'      <tuv xml:lang="{tgt_lang}">')
        lines.append(f"        <seg>{tgt_seg}</seg>")
        lines.append("      </tuv>")
        lines.append("    </tu>")

    lines.append("  </body>")
    lines.append("</tmx>")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return output_path
