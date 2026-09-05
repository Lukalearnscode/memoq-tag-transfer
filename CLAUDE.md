# Agent Guide for memoq-tag-transfer

This file helps AI coding agents (Claude Code, Cursor, Copilot, etc.) understand this project quickly.

## What this project does

A CLI tool that transfers inline XML tags from source text to target text in memoQ translation files (.mqxlz), then outputs TMX files for import into memoQ Translation Memory. It also verifies tags, with or without memoQ: `verify` accepts an .mqxlz file or a plain JSON list of source/target pairs.

**Input**: `.mqxlz` file (ZIP containing `document.mqxliff`), or `--pairs pairs.json`
**Output**: `.tmx` file (standard Translation Memory eXchange format), verification report, optional semantic-position report (markdown)

## Architecture (5-step pipeline)

```
extract.py → parse.py → place.py → verify.py → output.py
```

1. `extract.py` — Unzips mqxlz, parses mqxliff XML with lxml. Handles UTF-8 BOM.
2. `parse.py` — Walks `<trans-unit>` elements, classifies inline tags (`ph`, `bpt`, `ept`, `x`, `g`) by their attributes. Outputs simplified text with `{N}` placeholders.
3. `place.py` — Sends source (with `{N}` placeholders) + plain target to an LLM. The LLM inserts `{N}` markers into the target at correct positions. Validates tag count with Counter-based matching.
4. `verify.py` — Post-placement verification, segment by segment. See "What verify checks" below.
5. `output.py` — Replaces `{N}` placeholders with real tag XML from source. Generates TMX.

Two modules outside the linear pipeline:

- `pairs_io.py` — Loads and validates `--pairs` and `--glossary` JSON before any verifier sees it. A missing `target` key, a duplicate `id`, an empty file or a truncated JSON all raise `PairsError` (CLI exit code 2). Before this module, a missing target key passed silently as "clean".
- `semantic_report.py` — Catches the error class `verify.py` structurally cannot: tags all present, correct count, correct order, but wrapped around the wrong words. Lists what each tag pair wraps in source vs target, with five automatic flags (number mismatch, empty target span, term mismatch, span expanded, span density). Exposed as `verify --semantic-report PATH`, optionally with `--glossary`.

Entry point: `cli.py` (three subcommands: `analyze`, `transfer`, `verify`). `place.py` (and therefore the `openai` package) is imported lazily inside `transfer`, so `analyze` and `verify` work without an API key.

Tests: `python3 tests/test_verify.py` — plain script, no pytest needed, auto-discovers `test_*` functions in its own module namespace (a hardcoded call list silently skips new tests). 48 tests. Every example segment in the tests is made up; it keeps the shape of a real bug, not the real words. `test_examples_file_is_a_working_demo` runs `examples/pairs.json` and asserts each README gallery case still fires; keep it in sync when editing either.

## What verify checks

| Issue type | Severity | Meaning |
|---|---|---|
| `TAG_COUNT_MISMATCH` | CRITICAL | Different number of tags |
| `TAG_CONTENT_MISMATCH` | CRITICAL | Different tags after normalisation (Counter, duplicates exact) |
| `TAG_NESTING_ERROR` / `SOURCE_NESTING_ERROR` | CRITICAL | Illegal nesting of bpt/ept, g, HTML pairs or BBCode pairs (stack check; ICU branches expanded first) |
| `STRUCTURAL_TAG_ORDER_CHANGED` | CRITICAL | Position-anchored tags (bpt/ept/g) changed order |
| `INLINE_TAG_ORDER_CHANGED` | WARNING | Word-wrapping tags (BBCode/HTML pairs) changed order; normal when word order changed |
| `PLACEHOLDER_ORDER_CHANGED` | WARNING | `{N}`, `%s` etc. changed order |
| `ICU_CONDITIONAL_DROPPED` / `ICU_CONDITIONAL_ADDED` | WARNING | `{N:plural:a\|b}` resolved away in a Chinese target, or added in an English one |
| `BREAK_TAG_PADDING` | WARNING | Spaces next to `<br>` that the source does not have |
| `REDACTION_RUN_MISMATCH` | CRITICAL | Runs of ■ differ (compared run by run, not by total) |

Design decisions worth knowing before changing any of it:

- **Tag identity is normalised**: name + `id`/`i` attribute + inner text. Display attributes are ignored. For ICU conditionals the identity is `{Name:type}` only; option text is translatable.
- **BBCode is a whitelist plus auto-detection.** The whitelist (`BBCODE_TAGS`) exists so `[TODO]` and `[1]` are not tags. `verify_all` also scans the whole batch and adds any name that appears as both `[x]` and `[/x]` (`detect_paired_bbcode`). Self-closing custom tags come in through `--tags`. Do not replace the whitelist with a wildcard.
- **An unknown `<angle>` token is a tag only with evidence**: a closing mate in the text, an attribute, or a closing/self-closing marker. Otherwise it is stage-direction prose such as `<grunts>` that memoQ stored as escaped text.
- **Word-wrapping vs position-anchored tags get different verdicts** on order changes (WARNING vs CRITICAL). Whether a moved pair still wraps the right word is `semantic_report.py`'s job.
- **Counting is never delegated to an LLM.** The redaction reconciliation note exists to state the settled count in the prompt so the model has nothing left to count.

## Key constraints — read before making changes

- **Never serialize full mqxliff with lxml** — `etree.tostring(root)` breaks memoQ compatibility (changes XML declaration quotes, self-closing tag spaces, attribute order, CDATA, &quot;). Only use lxml for reading/parsing.
- **Never deepcopy tag elements** — causes namespace pollution (ns0 prefix). Extract tag XML strings directly and strip xmlns manually.
- **Target text is sacred** — when the target already has a translation, the plain text must not change. Only tags are inserted.
- **All source tags must appear in target** — same count, same content (Counter match), no extras, no omissions.
- **mrk revision marks** — `mq:tctype="del"` content must be skipped, `"ins"` content must be kept. Never mix them.
- **Single-segment API calls** — batch calls produce unreliable tag placement. Process one segment at a time.
- **Tag verification from original source** — always read tags from the original zip/mqxliff, never from intermediate parsed files.
- **Never hardcode API keys** — read from environment / `.env` only. Do not leave a key as a fallback default (`os.environ.get("KEY", "sk-...")` looks safe and is not).
- **Tests that guard a check must go through the real entry point** (`verify_all` or the CLI), not only the helper function. A test that calls the helper directly stays green when the call site is deleted.
- **When syncing code in from a private pipeline** — `verify.py`, `semantic_report.py`, `pairs_io.py` and `tests/` originate from a private localization pipeline. Anything ported in must be scrubbed of client names, project names and real segment text before commit. Comments that cite a real bug keep the lesson and drop the identifier: "on a real 525-tag file", not the game's name. Example segments are rewritten to keep the shape, not the words. Grep the diff for client names before pushing, not after.

## Supported inline tag types

| Tag | XLIFF element | Description |
|-----|--------------|-------------|
| ph | `<ph>` | Placeholder (colors, styles, links, line breaks) |
| bpt | `<bpt>` | Begin paired tag |
| ept | `<ept>` | End paired tag |
| x | `<x/>` | Standalone placeholder |
| g | `<g>` | Group/span tag |

Plus, in plain text: HTML pairs (`<b>`, `<i>`, `<font>` ...), BBCode (`[color]`, `[b]`, project-specific pairs), `{variables}`, ICU conditionals `{N:plural:a|b}`, `%s`/`%d`, `\n`.

## Tech stack

- Python 3.9+
- lxml (XML parsing)
- openai SDK (LLM calls via any OpenAI-compatible API; only needed for `transfer`)
- python-dotenv (config)

## Running locally

```bash
bash setup.sh
# edit .env with API key
memoq-tag-transfer analyze test.mqxlz
memoq-tag-transfer transfer test.mqxlz
memoq-tag-transfer verify test.mqxlz
memoq-tag-transfer verify --pairs examples/pairs.json --glossary examples/glossary.json --semantic-report report.md
```

## Adding a new tag type

1. Add detection logic in `parse.py:classify_tag()` — match on `displaytext` content
2. Add the tag name to `output.py:INLINE_TAG_NAMES` if it's a new element type
3. Add placement rules in `place.py:SYSTEM_PROMPT` if the new tag has unique behavior
4. For plain-text verification, add a pattern to `verify.py` `STRUCTURAL_PATTERNS` or `PLACEHOLDER_PATTERNS`, and a regression test
5. Unknown tags are already handled gracefully (flagged for user review)
