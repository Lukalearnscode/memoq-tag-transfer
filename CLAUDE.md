# Agent Guide for memoq-tag-transfer

This file helps AI coding agents (Claude Code, Cursor, Copilot, etc.) understand this project quickly.

## What this project does

A CLI tool that transfers inline XML tags from source text to target text in memoQ translation files (.mqxlz), then outputs TMX files for import into memoQ Translation Memory.

**Input**: `.mqxlz` file (ZIP containing `document.mqxliff`)
**Output**: `.tmx` file (standard Translation Memory eXchange format)

## Architecture (5-step pipeline)

```
extract.py → parse.py → place.py → verify.py → output.py
```

1. `extract.py` — Unzips mqxlz, parses mqxliff XML with lxml. Handles UTF-8 BOM.
2. `parse.py` — Walks `<trans-unit>` elements, classifies inline tags (`ph`, `bpt`, `ept`, `x`, `g`) by their attributes. Outputs simplified text with `{N}` placeholders.
3. `place.py` — Sends source (with `{N}` placeholders) + plain target to an LLM. The LLM inserts `{N}` markers into the target at correct positions. Validates tag count with Counter-based matching.
4. `verify.py` — Post-placement verification: tag count, content (Counter-based exact match), nesting (stack validation for bpt/ept and g pairs), structural tag order, placeholder order.
5. `output.py` — Replaces `{N}` placeholders with real tag XML from source. Generates TMX.

Entry point: `cli.py` (three subcommands: `analyze`, `transfer`, `verify`).

## Key constraints — read before making changes

- **Never serialize full mqxliff with lxml** — `etree.tostring(root)` breaks memoQ compatibility (changes XML declaration quotes, self-closing tag spaces, attribute order, CDATA, &quot;). Only use lxml for reading/parsing.
- **Never deepcopy tag elements** — causes namespace pollution (ns0 prefix). Extract tag XML strings directly and strip xmlns manually.
- **Target text is sacred** — when the target already has a translation, the plain text must not change. Only tags are inserted.
- **All source tags must appear in target** — same count, same content (Counter match), no extras, no omissions.
- **mrk revision marks** — `mq:tctype="del"` content must be skipped, `"ins"` content must be kept. Never mix them.
- **Single-segment API calls** — batch calls produce unreliable tag placement. Process one segment at a time.
- **Tag verification from original source** — always read tags from the original zip/mqxliff, never from intermediate parsed files.

## Supported inline tag types

| Tag | XLIFF element | Description |
|-----|--------------|-------------|
| ph | `<ph>` | Placeholder (colors, styles, links, line breaks) |
| bpt | `<bpt>` | Begin paired tag |
| ept | `<ept>` | End paired tag |
| x | `<x/>` | Standalone placeholder |
| g | `<g>` | Group/span tag |

## Tech stack

- Python 3.9+
- lxml (XML parsing)
- openai SDK (LLM calls via any OpenAI-compatible API)
- python-dotenv (config)

## Running locally

```bash
bash setup.sh
# edit .env with API key
memoq-tag-transfer analyze test.mqxlz
memoq-tag-transfer transfer test.mqxlz
memoq-tag-transfer verify test.mqxlz
```

## Adding a new tag type

1. Add detection logic in `parse.py:classify_tag()` — match on `displaytext` content
2. Add the tag name to `output.py:INLINE_TAG_NAMES` if it's a new element type
3. Add placement rules in `place.py:SYSTEM_PROMPT` if the new tag has unique behavior
4. Unknown tags are already handled gracefully (flagged for user review)
