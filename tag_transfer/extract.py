"""Extract mqxliff from mqxlz (ZIP) files."""

import os
import zipfile

from lxml import etree

NS = {
    "x": "urn:oasis:names:tc:xliff:document:1.2",
    "mq": "MQXliff",
}


def _safe_extract(z, work_dir):
    """extractall, but refuse members that would land outside work_dir.

    An .mqxlz is a ZIP from outside the tool. A member named "../../x" makes
    a plain extractall write wherever it likes.
    """
    dest = os.path.realpath(work_dir)
    for member in z.namelist():
        target = os.path.realpath(os.path.join(dest, member))
        if target != dest and not target.startswith(dest + os.sep):
            raise ValueError(
                f"refusing to extract {member!r}: it points outside {dest}")
    z.extractall(dest)


def extract_mqxlz(mqxlz_path, work_dir=None):
    """Unzip mqxlz and return the path to document.mqxliff."""
    if work_dir is None:
        work_dir = os.path.join(os.path.dirname(mqxlz_path), "work_dir")
    os.makedirs(work_dir, exist_ok=True)
    with zipfile.ZipFile(mqxlz_path, "r") as z:
        _safe_extract(z, work_dir)
    return os.path.join(work_dir, "document.mqxliff")


def parse_mqxliff(mqxliff_path):
    """Parse mqxliff XML, handling BOM. Returns (root, list of trans-units)."""
    with open(mqxliff_path, "rb") as f:
        raw = f.read()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    root = etree.fromstring(raw)
    units = root.xpath("//x:trans-unit", namespaces=NS)
    return root, units
