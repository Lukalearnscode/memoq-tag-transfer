"""Command-line interface for memoQ Tag Transfer."""

import argparse
import json
import os
import sys

from .extract import extract_mqxlz, parse_mqxliff
from .parse import extract_segments
from .output import generate_tmx, build_full_seg, build_tmx_seg
from .pairs_io import PairsError, load_pairs, load_glossary
from .verify import verify_all, verify_redaction_runs, set_custom_tags, print_report


def _segments_from_file(args):
    mqxliff_path = extract_mqxlz(args.input, args.work_dir)
    _, units = parse_mqxliff(mqxliff_path)
    segments = extract_segments(units)
    start = args.start - 1 if args.start else 0
    end = args.end if args.end else len(segments)
    return segments, segments[start:end], start


def cmd_analyze(args):
    """Show source/target segments and their tags."""
    segments, selected, start = _segments_from_file(args)

    for i, seg in enumerate(selected, start=start + 1):
        print(f"--- Row {i} (id={seg['id']}) ---")
        print(f"  SRC: {seg['src_text']}")
        if seg["src_tags"]:
            for t in seg["src_tags"]:
                print(f"       {{{t['id']}}}: {t['type']} — {t['detail']}")
        print(f"  TGT: {seg['tgt_text']}")
        if seg["tgt_text"] and not seg["src_tags"]:
            print("       (no tags)")
        elif not seg["tgt_text"]:
            print("       (empty)")
        print()

    print(f"Total: {len(segments)} segments, showing {len(selected)}")


def cmd_transfer(args):
    """Transfer tags from source to target and generate TMX."""
    # Imported here so that `analyze` and `verify` work without the openai
    # package or an API key.
    from .place import place_tags, get_client, get_model

    _, selected, start = _segments_from_file(args)

    client = get_client()
    model = get_model()
    results = []
    errors = []

    for i, seg in enumerate(selected, start=start + 1):
        if not seg["src_tags"]:
            print(f"  Row {i}: no tags, skip")
            continue
        if not seg["tgt_text"]:
            print(f"  Row {i}: empty target, skip")
            continue

        print(f"  Row {i}: {len(seg['src_tags'])} tags ... ", end="", flush=True)
        try:
            tagged = place_tags(
                seg["src_text"], seg["src_tags"], seg["tgt_text"],
                client=client, model=model,
            )
            if tagged.startswith("⚠️"):
                print("TAG MISMATCH")
                errors.append((i, tagged))
            else:
                print("OK")
            results.append({
                "id": seg["id"],
                "src_el": seg["src_el"],
                "src_text": seg["src_text"],
                "tgt_template": tagged.split("\n")[-1],
            })
        except Exception as e:
            print(f"ERROR: {e}")
            errors.append((i, str(e)))

    if results:
        output_path = args.output or os.path.splitext(args.input)[0] + ".tmx"
        generate_tmx(results, output_path, args.src_lang, args.tgt_lang)
        print(f"\nTMX written: {output_path} ({len(results)} segments)")

        verify_pairs = []
        for r in results:
            verify_pairs.append({
                "id": r.get("id", ""),
                "source": build_full_seg(r["src_el"]),
                "target": build_tmx_seg(r["src_el"], r["tgt_template"]),
            })
        issues = verify_all(verify_pairs)
        print_report(verify_pairs, issues)

    if errors:
        print(f"\n⚠️  {len(errors)} segments had issues:")
        for row, msg in errors:
            print(f"  Row {row}: {msg[:100]}")


def _pairs_for_verify(args):
    """Segments come either from an .mqxlz file or from a --pairs JSON file."""
    if args.pairs:
        return load_pairs(args.pairs, normalize=True)
    if not args.input:
        raise PairsError("give an .mqxlz file, or --pairs pairs.json")
    _, selected, _ = _segments_from_file(args)
    return [{
        "id": seg["id"],
        "source": build_full_seg(seg["src_el"]),
        "target": build_full_seg(seg["tgt_el"]) if seg["tgt_el"] is not None else "",
    } for seg in selected]


def cmd_verify(args) -> int:
    """Verify tag consistency between source and target. Returns the exit code."""
    try:
        pairs = _pairs_for_verify(args)
        glossary = load_glossary(args.glossary) if args.glossary else []
    except PairsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.tags:
        set_custom_tags(args.tags.split(","))

    issues = verify_all(pairs, auto_detect_tags=not args.no_auto_tags)
    issues += verify_redaction_runs(pairs)

    if args.format == "json":
        print(json.dumps({
            "segments": len(pairs),
            "issues": [{"seg_id": i.seg_id, "severity": i.severity,
                        "issue_type": i.issue_type, "detail": i.detail} for i in issues],
        }, ensure_ascii=False, indent=2))
    else:
        print_report(pairs, issues)

    # Optional: semantic-position report. verify_all proves the tags are all
    # there; this proves they wrap the right words. Separate flag because it
    # produces a long markdown table meant for human review, not a pass/fail.
    if args.semantic_report:
        from .semantic_report import write_report
        write_report(pairs, args.semantic_report, glossary)
        print(f"\nSemantic-position report written: {args.semantic_report}",
              file=sys.stderr if args.format == "json" else sys.stdout)

    return 1 if any(i.severity == "CRITICAL" for i in issues) else 0


def main():
    parser = argparse.ArgumentParser(
        prog="memoq-tag-transfer",
        description="Transfer inline tags from source to target in memoQ mqxlz files",
    )
    sub = parser.add_subparsers(dest="command")

    # analyze
    p_analyze = sub.add_parser("analyze", help="Show segments and tags in an mqxlz file")
    p_analyze.add_argument("input", help="Path to .mqxlz file")
    p_analyze.add_argument("--start", type=int, help="Start row (1-based)")
    p_analyze.add_argument("--end", type=int, help="End row (inclusive)")
    p_analyze.add_argument("--work-dir", help="Temp directory for extraction")

    # transfer
    p_transfer = sub.add_parser("transfer", help="Transfer tags and generate TMX")
    p_transfer.add_argument("input", help="Path to .mqxlz file")
    p_transfer.add_argument("-o", "--output", help="Output TMX path (default: same name as input)")
    p_transfer.add_argument("--start", type=int, help="Start row (1-based)")
    p_transfer.add_argument("--end", type=int, help="End row (inclusive)")
    p_transfer.add_argument("--src-lang", default="zh-CN", help="Source language (default: zh-CN)")
    p_transfer.add_argument("--tgt-lang", default="en-US", help="Target language (default: en-US)")
    p_transfer.add_argument("--work-dir", help="Temp directory for extraction")

    # verify
    p_verify = sub.add_parser(
        "verify",
        help="Verify tag consistency in source vs target (exit 1 on CRITICAL, 2 on bad input)")
    p_verify.add_argument("input", nargs="?", help="Path to .mqxlz file")
    p_verify.add_argument(
        "--pairs", metavar="JSON",
        help='Instead of an .mqxlz file: a JSON list of {"id","source","target"} (no memoQ needed)')
    p_verify.add_argument("--start", type=int, help="Start row (1-based)")
    p_verify.add_argument("--end", type=int, help="End row (inclusive)")
    p_verify.add_argument("--work-dir", help="Temp directory for extraction")
    p_verify.add_argument(
        "--tags", default="", metavar="a,b",
        help="Project-specific BBCode tag names, comma-separated (e.g. gold,blue). "
             "Paired tags are detected automatically; this is for self-closing ones")
    p_verify.add_argument(
        "--no-auto-tags", action="store_true",
        help='Turn off "a name that appears as both [x] and [/x] is a tag"')
    p_verify.add_argument(
        "--glossary", metavar="JSON",
        help='Glossary for the semantic report: [{"source": ..., "target": "A|B"}]')
    p_verify.add_argument("--format", choices=["text", "json"], default="text")
    p_verify.add_argument(
        "--semantic-report", metavar="PATH",
        help="Also write a markdown table of what each tag pair wraps in source vs target",
    )

    args = parser.parse_args()
    if args.command == "analyze":
        cmd_analyze(args)
    elif args.command == "transfer":
        cmd_transfer(args)
    elif args.command == "verify":
        sys.exit(cmd_verify(args))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
