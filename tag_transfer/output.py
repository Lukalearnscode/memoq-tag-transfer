"""Generate TMX files from tag-transferred segments."""

import re
from datetime import datetime, timezone
from pathlib import Path

from lxml import etree

from . import __version__


class TmxValidationError(Exception):
    """The generated TMX would not survive a memoQ import. Message is for humans."""


def escape_xml(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _escape_attr(text):
    return escape_xml(text).replace('"', "&quot;")


INLINE_TAG_NAMES = {"ph", "bpt", "ept", "x", "g"}

_XMLNS_DECL_RE = re.compile(r'\s+xmlns:([\w.-]+)="[^"]*"')
_ANY_XMLNS_DECL_RE = re.compile(r'\s+xmlns(?::[\w.-]+)?="[^"]*"')


def _local_name(el):
    return etree.QName(el.tag).localname if "}" in el.tag else el.tag


def _clean_xmlns(raw):
    """Strip namespace noise lxml adds when serialising a subtree.

    lxml prints every namespace that is in scope at the element, not only the
    ones the fragment uses. Serialising a <ph> out of an mqxliff therefore
    carries the file-level xmlns:mq along, and it lands on every tag in the
    TMX. A declaration that IS used must stay: memoQ <ph> elements really do
    contain <mq:rxt>, and dropping its declaration makes the fragment invalid.

    This is the verification path (the mqxliff's own spelling). The TMX path
    below never serialises a tag element as XML, so it does not go through here.
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


# ── The shape memoQ imports ───────────────────────────────────────────────
#
# memoQ writes inline tags into an mqxliff one way and accepts them back from
# a TMX another way. The written form is <ph>, <bpt>, <ept>, each holding
# memoQ's private <mq:rxt …> as escaped text, and a bpt/ept pair holds the
# two HALVES of one rxt:
#
#   <bpt id="1" rid="1">&lt;mq:rxt displaytext="…" val="…"&gt;</bpt>
#   <ept id="2" rid="1">&lt;/mq:rxt displaytext="…" val="…"&gt;</ept>
#
# Unescaped, the closing half is `</mq:rxt displaytext="…">` — a closing tag
# with attributes, which is not XML. memoQ reads its own files fine, but on a
# TMX import it unescapes each inline tag's content and parses it again, and
# that parse fails. The TMX itself is well-formed the whole time, so nothing
# on this side notices; only the import does, and it does not report a line.
#
# The only shape that has actually gone through a memoQ import (twice, on the
# same real 457-segment UI file, with 689 bpt/ept pairs and 1343 ph):
#
#   every ph/bpt/ept  ->  <ph x="ID">  with the rxt inside made self-closing
#   <bpt …>…rxt…&gt;  ->  <ph x="1">&lt;mq:rxt displaytext="…" val="…" /&gt;</ph>
#   <ept …>…/rxt…&gt; ->  <ph x="2">&lt;mq:rxt displaytext="…" val="…" /&gt;</ph>
#   <x id="5"/>       ->  <ph x="5"/>
#   root              ->  <tmx version="1.4" xmlns:mq="MQXliff">
#
# The cost: the bpt/ept pairing is not carried into the TM, so memoQ shows
# the two halves as two standalone tags and restores their look from
# displaytext. Pairing correctness is checked in verify.py on the pairs, not
# in the TMX, so a mis-scoped bold still gets caught. A "conformant" TMX
# 1.4 rendering — <bpt i="1" x="1">, <ept i="1"> — is what the TMX spec asks
# for and what memoQ refused. Do not bring it back without importing a file.
#
# x= is the tag's own id (bpt 1, ept 2, ph 3 …), never rid: that is the
# numbering in the file that imported, and it is what get_tag_xml_str
# matches placeholders on.

def _selfclose_inner(inner):
    """Normalise the private tag inside an inline tag to one self-closing tag.

        </mq:rxt displaytext="…" val="…">   ->  <mq:rxt displaytext="…" val="…" />
        <mq:rxt displaytext="…" val="…">    ->  <mq:rxt displaytext="…" val="…" />
        <mq:rxt displaytext="…" val="…"/>   ->  <mq:rxt displaytext="…" val="…" />

    Only content that as a whole looks like one tag is touched. Anything else
    (a plain-text placeholder, an odd fragment) is returned as it came in;
    rewriting it would do damage, not good.
    """
    s = inner.strip()
    if not (s.startswith("<") and s.endswith(">")):
        return inner
    if s.startswith("</"):
        s = "<" + s[2:]
    if s.endswith("/>"):
        s = s[:-2].rstrip() + " />"
    else:
        s = s[:-1].rstrip() + " />"
    return s


def _inner_text(el):
    """The content of one inline tag as a single string.

    memoQ stores the rxt two ways and both turn up in real files: as escaped
    text (the common one) and as a child element. lxml gives the first as
    el.text already unescaped once; the second is serialised here, minus the
    namespace declarations lxml prints, because the whole string is about to
    become escaped text anyway and the declaration lives on the TMX root.
    """
    parts = [el.text or ""]
    for child in el:
        raw = etree.tostring(child, encoding="unicode", with_tail=True)
        parts.append(_ANY_XMLNS_DECL_RE.sub("", raw))
    return "".join(parts)


def _memoq_ph(el):
    """One inline tag element in the shape memoQ imports (see above)."""
    tag_id = el.get("id") or el.get("i") or ""
    x = _escape_attr(tag_id)
    if _local_name(el) == "x":
        return f'<ph x="{x}"/>'
    inner = _inner_text(el)
    if not inner.strip():
        return f'<ph x="{x}"/>'
    return f'<ph x="{x}">{escape_xml(_selfclose_inner(inner))}</ph>'


def _inline_tag_string(el, memoq_shape):
    """XML string for one inline tag.

    memoq_shape=False is the mqxliff's own spelling, used to verify the
    transfer against the source. memoq_shape=True is the TMX form. <g> has no
    memoQ equivalent and memoQ never emits one, so it is left as it came.
    """
    if memoq_shape and _local_name(el) != "g":
        return _memoq_ph(el)
    return _clean_xmlns(etree.tostring(el, encoding="unicode", with_tail=False))


def get_tag_xml_str(src_el, tag_id, memoq_shape=False):
    """Extract clean XML string for an inline tag by its id attribute."""
    for child in src_el:
        if _local_name(child) not in INLINE_TAG_NAMES:
            continue
        child_id = child.get("id") or child.get("i")
        if child_id == str(tag_id):
            return _inline_tag_string(child, memoq_shape)
    return ""


def build_full_seg(el, memoq_shape=False):
    """Build complete seg content from a source element (text + inline tag XML)."""
    if el is None:
        return ""
    parts = []
    if el.text:
        parts.append(escape_xml(el.text))
    for child in el:
        if _local_name(child) in INLINE_TAG_NAMES:
            parts.append(_inline_tag_string(child, memoq_shape))
        if child.tail:
            parts.append(escape_xml(child.tail))
    return "".join(parts)


def build_tmx_seg(src_el, template, memoq_shape=False):
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
        tag_xml = get_tag_xml_str(src_el, m.group(1), memoq_shape)
        out.append(tag_xml if tag_xml else escape_xml(m.group(0)))
        pos = m.end()
    out.append(escape_xml(template[pos:]))
    return "".join(out)


_RXT_RE = re.compile(r"&lt;mq:rxt(.*?)&gt;", re.DOTALL)


def validate_tmx(path):
    """Parse a written TMX back and scan it for the shapes memoQ rejects.

    Returns a list of problems, empty when fine. The verifier checks the
    strings it holds in memory; this checks the file that actually got
    written, which is the only thing memoQ ever sees. Well-formed XML is
    necessary and nowhere near sufficient: every shape scanned for below was
    a well-formed TMX that memoQ refused without naming a line.
    """
    try:
        etree.parse(str(path))
    except etree.XMLSyntaxError as exc:
        return [f"generated TMX is not well-formed XML: {exc}"]

    text = Path(path).read_text(encoding="utf-8")
    problems = []
    n = len(re.findall(r"<(?:bpt|ept)\b", text))
    if n:
        problems.append(
            f"{n} bpt/ept tag(s) in the TMX; memoQ rejects paired halves, "
            "they must be downgraded to <ph>")
    n = text.count("&lt;/mq:rxt")
    if n:
        problems.append(
            f"{n} closing-half rxt(s) (&lt;/mq:rxt …&gt;); memoQ rejects them")
    n = sum(1 for m in _RXT_RE.finditer(text) if not m.group(1).rstrip().endswith("/"))
    if n:
        problems.append(f"{n} mq:rxt tag(s) not self-closing; memoQ rejects them")
    return problems


def generate_tmx(results, output_path, src_lang="zh-CN", tgt_lang="en-US",
                 creation_id="memoq-tag-transfer", stamp=None):
    """Generate a TMX file from processed segments.

    Args:
        results: list of dicts, each with:
            - id: segment id, written as tuid
            - src_el: lxml source element
            - src_text: source text with {N} placeholders
            - tgt_template: target text with {N} placeholders
        output_path: where to write the TMX file
        src_lang: source language code
        tgt_lang: target language code
        creation_id: written as creationid/changeid on header, tu and tuv
        stamp: creation timestamp (UTC, TMX format); now() when omitted

    Header, tu and tuv carry creationdate/creationid (and changedate/changeid
    on tu and the target tuv). TMX 1.4 makes them optional; memoQ reads them
    when it indexes the TM, and a file without them showed up as "cannot be
    opened" with no further detail. o-tmf is "TMX" for the same reason.
    """
    stamp = stamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    cid = _escape_attr(creation_id)
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<tmx version="1.4" xmlns:mq="MQXliff">',
        f'  <header creationtool="memoq-tag-transfer" creationtoolversion="{__version__}"',
        '          segtype="sentence" o-tmf="TMX" adminlang="en-US"',
        f'          srclang="{src_lang}" datatype="plaintext"',
        f'          creationdate="{stamp}" creationid="{cid}">',
        "  </header>",
        "  <body>",
    ]

    for r in results:
        src_seg = build_full_seg(r["src_el"], memoq_shape=True)
        tgt_seg = build_tmx_seg(r["src_el"], r["tgt_template"], memoq_shape=True)

        # tuid is how a TM entry stays traceable back to its source segment.
        tuid = str(r.get("id", "") or "")
        tu_attrs = f' tuid="{_escape_attr(tuid)}"' if tuid else ""
        tu_attrs += (f' creationdate="{stamp}" creationid="{cid}"'
                     f' changedate="{stamp}" changeid="{cid}"')
        lines.append(f"    <tu{tu_attrs}>")
        lines.append(f'      <tuv xml:lang="{src_lang}" creationdate="{stamp}" creationid="{cid}">')
        lines.append(f"        <seg>{src_seg}</seg>")
        lines.append("      </tuv>")
        lines.append(f'      <tuv xml:lang="{tgt_lang}" creationdate="{stamp}" creationid="{cid}"'
                     f' changedate="{stamp}" changeid="{cid}">')
        lines.append(f"        <seg>{tgt_seg}</seg>")
        lines.append("      </tuv>")
        lines.append("    </tu>")

    lines.append("  </body>")
    lines.append("</tmx>")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    problems = validate_tmx(output_path)
    if problems:
        raise TmxValidationError(
            f"{output_path} was written but memoQ would not import it: "
            + "; ".join(problems)
            + ". The file is kept on disk for inspection."
        )
    return output_path
