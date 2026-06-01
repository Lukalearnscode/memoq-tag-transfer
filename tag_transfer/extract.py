"""Extract mqxliff from mqxlz (ZIP) files."""

import os
import zipfile

from lxml import etree

NS = {
    "x": "urn:oasis:names:tc:xliff:document:1.2",
    "mq": "MQXliff",
}


def extract_mqxlz(mqxlz_path, work_dir=None):
    """Unzip mqxlz and return the path to document.mqxliff."""
    if work_dir is None:
        work_dir = os.path.join(os.path.dirname(mqxlz_path), "work_dir")
    os.makedirs(work_dir, exist_ok=True)
    with zipfile.ZipFile(mqxlz_path, "r") as z:
        z.extractall(work_dir)
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
