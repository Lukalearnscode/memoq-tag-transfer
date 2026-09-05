"""
Load and check a pairs file before any verifier sees it.

A pairs file is a JSON list of segments:

    [{"id": "1", "source": "...", "target": "..."}, ...]

A dict with a "pairs" or "segments" list inside is accepted too.

Why this module exists: a verifier that receives a record with no "target"
key, or two records with the same id, used to exit 0 and print "passed".
Any step upstream that drops the target key turns the whole check green.
A silent pass looks exactly like a real pass, which makes it the more
dangerous failure. The opposite failure, a bare traceback on an empty or
truncated file, is loud but unhelpful.

Rules (breaking any one raises PairsError; the CLI exits with code 2):
  1. File must exist, be UTF-8, parse as JSON, and hold a non-empty list.
  2. Every record must be an object with a non-empty "id", a string
     "source" and a string "target".
  3. Ids must be unique (verifiers reconcile by id; duplicates overwrite
     each other).
  4. An empty "target" is an error unless the record says
     "translatable": false. That is the only sanctioned way to mark a
     segment as intentionally untranslated.
"""
from __future__ import annotations

import json
from pathlib import Path

MAX_LISTED = 5  # how many ids to name in an error message before "..."


class PairsError(Exception):
    """The pairs (or glossary) file cannot be used. Message is for humans."""


def _fmt_ids(ids: list[str]) -> str:
    head = ", ".join(ids[:MAX_LISTED])
    return head if len(ids) <= MAX_LISTED else f"{head} ... ({len(ids)} total)"


def _read_json(path, what: str):
    p = Path(path)
    if not p.exists():
        raise PairsError(f"{what} file not found: {p}")
    if p.is_dir():
        raise PairsError(f"{what} points to a directory, not a file: {p}")
    try:
        raw = p.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise PairsError(
            f"{what} file {p} is not UTF-8 ({exc.reason} at byte {exc.start}). "
            f"If it is GBK, convert first: iconv -f GBK -t UTF-8 '{p}' > fixed.json"
        ) from None
    if not raw.strip():
        raise PairsError(f"{what} file {p} is empty (0 bytes)")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PairsError(
            f"{what} file {p} is not valid JSON: line {exc.lineno} col {exc.colno}: {exc.msg}"
        ) from None


def load_pairs(path, *, normalize: bool = False, require_target: bool = True,
               what: str = "--pairs") -> list[dict]:
    """Read and check a pairs file.

    normalize=True returns only the id/source/target keys per record.
    """
    data = _read_json(path, what)
    if isinstance(data, dict):
        for key in ("pairs", "segments"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            raise PairsError(
                f"{what} file {path}: top level must be a list, "
                "or an object with a 'pairs' or 'segments' list"
            )
    if not isinstance(data, list):
        raise PairsError(
            f"{what} file {path}: top level must be a list, got {type(data).__name__}"
        )
    if not data:
        raise PairsError(f"{what} file {path} has 0 segments, nothing to check")

    rows: list[dict] = []
    ids: list[str] = []
    bad_type, no_id, bad_text, no_target, empty_target = [], [], [], [], []
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            bad_type.append(f"[{index}]")
            continue
        raw_id = item.get("id")
        if isinstance(raw_id, (int, float)) and not isinstance(raw_id, bool):
            raw_id = str(raw_id)
        if not isinstance(raw_id, str) or not raw_id.strip():
            no_id.append(f"[{index}]")
            continue
        seg_id = raw_id
        ids.append(seg_id)

        source = item.get("source", item.get("text"))
        if not isinstance(source, str):
            bad_text.append(seg_id)
            source = ""

        has_target = "target" in item
        target = item.get("target")
        if require_target:
            if not has_target or target is None:
                no_target.append(seg_id)
            elif not isinstance(target, str):
                bad_text.append(seg_id)
            elif not target.strip() and item.get("translatable", True) is not False:
                empty_target.append(seg_id)
        if not isinstance(target, str):
            target = ""

        row = dict(item)
        row["id"], row["source"], row["target"] = seg_id, source, target
        rows.append({"id": seg_id, "source": source, "target": target}
                    if normalize else row)

    problems = []
    if bad_type:
        problems.append(f"records that are not objects: {_fmt_ids(bad_type)}")
    if no_id:
        problems.append(f"missing or empty id: {_fmt_ids(no_id)}")
    if bad_text:
        problems.append(f"source/target is not a string: {_fmt_ids(bad_text)}")
    if no_target:
        problems.append(
            f"{len(no_target)} record(s) have no 'target' key: {_fmt_ids(no_target)} "
            "(this is the shape of a pipeline dropping a key, not of an empty translation)"
        )
    if empty_target:
        problems.append(
            f"{len(empty_target)} empty translation(s): {_fmt_ids(empty_target)} "
            "(if a segment is meant to stay untranslated, add \"translatable\": false)"
        )
    dups = sorted({i for i in ids if ids.count(i) > 1})
    if dups:
        problems.append(f"duplicate ids: {_fmt_ids(dups)}")
    if problems:
        raise PairsError(f"{what} file {path} cannot be used: " + "; ".join(problems))
    return rows


def load_glossary(path, what: str = "--glossary") -> list[dict]:
    """Read a glossary file.

    Accepts either a bare list of entries or an object {"entries": [...]}.
    Each entry: {"source": "...", "target": "A|B"}; "|" separates accepted
    variants of the translation. An object with "status": "none" means
    "no glossary" and returns an empty list.
    """
    data = _read_json(path, what)
    if isinstance(data, dict):
        if data.get("status") == "none":
            return []
        entries = data.get("entries", [])
    else:
        entries = data
    if not isinstance(entries, list):
        raise PairsError(f"{what} file {path}: 'entries' must be a list")
    return entries
