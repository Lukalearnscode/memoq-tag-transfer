"""Generate TMX files from tag-transferred segments."""

import re

from lxml import etree

from . import __version__


class TmxValidationError(Exception):
    """The generated TMX is not well-formed XML. Message is for humans."""


def escape_xml(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


INLINE_TAG_NAMES = {"ph", "bpt", "ept", "x", "g"}


_XMLNS_DECL_RE = re.compile(r'\s+xmlns:([\w.-]+)="[^"]*"')


def _clean_xmlns(raw):
    """Strip namespace noise lxml adds when serialising a subtree.

    lxml prints every namespace that is in scope at the element, not only the
    ones the fragment uses. Serialising a <ph> out of an mqxliff therefore
    carries the file-level xmlns:mq along, and it lands on every tag in the
    TMX. A declaration that IS used must stay: memoQ <ph> elements really do
    contain <mq:rxt>, and dropping its declaration makes the fragment invalid.
    """
    raw = raw.replace(' xmlns="urn:oasis:names:tc:xliff:document:1.2"', "")
    raw = re.sub(r"<(/?)ns\d+:", r"<\1", raw)
    raw = re.sub(r' xmlns:ns\d+="[^"]*"', "", raw)
    body = _XMLNS_DECL_RE.sub("", raw)
    for prefix in set(_XMLNS_DECL_RE.findall(raw)):
        used = re.search(rf"<{re.escape(prefix)}:|\s{re.escape(prefix)}:", body)
        if not used:
            raw = re.sub(rf'\s+xmlns:{re.escape(prefix)}="[^"]*"', "", raw)
    return raw


# XLIFF and TMX spell their inline tags differently. These tags are read out
# of an mqxliff, so they arrive in XLIFF spelling and must be translated
# before they go into a TMX:
#
#   XLIFF                     TMX 1.4
#   <ph id="3">…</ph>         <ph x="3">…</ph>
#   <x id="3"/>               <ph x="3"/>
#   <bpt id="1" rid="2">…     <bpt i="2" x="1">…
#   <ept id="4" rid="2">…     <ept i="2">…
#
# In TMX, "i" is #REQUIRED on bpt/ept and is what pairs them, "x" identifies a
# ph, and id/rid are not TMX attributes at all. Writing the XLIFF spelling
# produces a file no TMX validator accepts.
#
# This is an output-format concern only. Verification compares the mqxliff's
# own tags and keeps reading the XLIFF spelling, so pass tmx_spelling=True
# only on the way into a TMX file.
_OPEN_TAG_RE = re.compile(r'^<([\w.-]+)((?:\s[^>]*?)?)(/?)>')
_INLINE_ATTR_RE = re.compile(r'\b(id|rid|i|x)="([^"]*)"')


def _tmx_inline_attrs(name, attrs):
    found = dict(_INLINE_ATTR_RE.findall(attrs))
    if name in ("bpt", "ept"):
        # memoQ pairs a bpt with its ept through rid; TMX does it through i.
        pair = found.get("rid") or found.get("i") or found.get("id", "")
        out = f' i="{pair}"'
        own = found.get("id") or found.get("x")
        if name == "bpt" and own:
            out += f' x="{own}"'
        return out
    own = found.get("x") or found.get("id") or found.get("i")
    return f' x="{own}"' if own else ""


def _to_tmx_inline(raw):
    """Rewrite one inline tag from XLIFF spelling into TMX spelling."""
    m = _OPEN_TAG_RE.match(raw)
    if not m:
        return raw
    name, attrs, selfclose = m.group(1), m.group(2), m.group(3)
    # TMX has no <g>. memoQ does not emit one, so rather than invent a
    # bpt/ept split that nothing here can test, it is left untouched.
    if name not in INLINE_TAG_NAMES or name == "g":
        return raw
    tmx_name = "ph" if name == "x" else name
    rest = raw[m.end():]
    close = f"</{name}>"
    if not selfclose and rest.endswith(close):
        rest = rest[:-len(close)] + f"</{tmx_name}>"
    return (f"<{tmx_name}{_tmx_inline_attrs(name, attrs)}"
            f"{'/' if selfclose else ''}>" + rest)


def _escape_attr(text):
    return escape_xml(text).replace('"', "&quot;")


def get_tag_xml_str(src_el, tag_id, tmx_spelling=False):
    """Extract clean XML string for an inline tag by its id attribute."""
    for child in src_el:
        tag_name = etree.QName(child.tag).localname if "}" in child.tag else child.tag
        if tag_name not in INLINE_TAG_NAMES:
            continue
        child_id = child.get("id") or child.get("i")
        if child_id == str(tag_id):
            raw = _clean_xmlns(
                etree.tostring(child, encoding="unicode", with_tail=False))
            return _to_tmx_inline(raw) if tmx_spelling else raw
    return ""


def build_full_seg(el, tmx_spelling=False):
    """Build complete seg content from a source element (text + inline tag XML)."""
    if el is None:
        return ""
    parts = []
    if el.text:
        parts.append(escape_xml(el.text))
    for child in el:
        tag_name = etree.QName(child.tag).localname if "}" in child.tag else child.tag
        if tag_name in INLINE_TAG_NAMES:
            raw = _clean_xmlns(
                etree.tostring(child, encoding="unicode", with_tail=False))
            parts.append(_to_tmx_inline(raw) if tmx_spelling else raw)
        if child.tail:
            parts.append(escape_xml(child.tail))
    return "".join(parts)


def build_tmx_seg(src_el, template, tmx_spelling=False):
    """Replace {N} placeholders with tag XML; XML-escape everything around them.

    The text between the placeholders is plain target text, so an "&" or "<"
    in the translation has to be escaped or the whole TMX stops being
    well-formed XML and memoQ refuses the import. Only the substituted tag XML
    goes in raw. A placeholder whose tag cannot be found is left in place
    (escaped) rather than silently dropped: a visible {N} in the TM beats a
    tag that quietly disappeared.
    """
    out, pos = [], 0
    for m in re.finditer(r"\{(\d+)\}", template):
        out.append(escape_xml(template[pos:m.start()]))
        tag_xml = get_tag_xml_str(src_el, m.group(1), tmx_spelling)
        out.append(tag_xml if tag_xml else escape_xml(m.group(0)))
        pos = m.end()
    out.append(escape_xml(template[pos:]))
    return "".join(out)


def validate_tmx(path):
    """Parse a written TMX back. Returns a list of problems, empty when fine.

    The verifier checks the strings it holds in memory; nothing checked the
    file that actually got written. An escaping bug therefore passed every
    gate and only failed later, inside memoQ.
    """
    try:
        etree.parse(str(path))
        return []
    except etree.XMLSyntaxError as exc:
        return [f"generated TMX is not well-formed XML: {exc}"]


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
        f'  <header creationtool="memoq-tag-transfer" creationtoolversion="{__version__}"',
        '          segtype="sentence" o-tmf="memoQ" adminlang="en-US"',
        f'          srclang="{src_lang}" datatype="plaintext"/>',
        "  <body>",
    ]

    for r in results:
        src_seg = build_full_seg(r["src_el"], tmx_spelling=True)
        tgt_seg = build_tmx_seg(r["src_el"], r["tgt_template"], tmx_spelling=True)

        # The segment id was collected all along and then dropped on the floor.
        # tuid is how a TM entry stays traceable back to its source segment.
        tuid = str(r.get("id", "") or "")
        lines.append(f'    <tu tuid="{_escape_attr(tuid)}">' if tuid else "    <tu>")
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

    problems = validate_tmx(output_path)
    if problems:
        raise TmxValidationError(
            f"{output_path} was written but cannot be parsed back: "
            + "; ".join(problems)
            + ". The file is kept on disk for inspection."
        )
    return output_path
