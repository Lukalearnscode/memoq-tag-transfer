"""Command-line interface for memoQ Tag Transfer."""

import argparse
import os
import sys

from .extract import extract_mqxlz, parse_mqxliff
from .parse import extract_segments
from .place import place_tags, get_client, get_model
from .output import generate_tmx


def cmd_analyze(args):
    """Analyze an mqxlz file and show source/target segments with tags."""
    mqxliff_path = extract_mqxlz(args.input, args.work_dir)
    _, units = parse_mqxliff(mqxliff_path)
    segments = extract_segments(units)

    start = args.start - 1 if args.start else 0
    end = args.end if args.end else len(segments)
    selected = segments[start:end]

    for i, seg in enumerate(selected, start=start + 1):
        print(f"--- Row {i} (id={seg['id']}) ---")
        print(f"  SRC: {seg['src_text']}")
        if seg["src_tags"]:
            for t in seg["src_tags"]:
                print(f"       {{{t['id']}}}: {t['type']} — {t['detail']}")
        print(f"  TGT: {seg['tgt_text']}")
        has_tags = any("{" in seg["tgt_text"] for _ in [1])
        if seg["tgt_text"] and not seg["src_tags"]:
            print("       (no tags)")
        elif not seg["tgt_text"]:
            print("       (empty)")
        print()

    print(f"Total: {len(segments)} segments, showing {len(selected)}")


def cmd_transfer(args):
    """Transfer tags from source to target and generate TMX."""
    mqxliff_path = extract_mqxlz(args.input, args.work_dir)
    _, units = parse_mqxliff(mqxliff_path)
    segments = extract_segments(units)

    start = args.start - 1 if args.start else 0
    end = args.end if args.end else len(segments)
    selected = segments[start:end]

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

    if errors:
        print(f"\n⚠️  {len(errors)} segments had issues:")
        for row, msg in errors:
            print(f"  Row {row}: {msg[:100]}")


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

    args = parser.parse_args()
    if args.command == "analyze":
        cmd_analyze(args)
    elif args.command == "transfer":
        cmd_transfer(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
