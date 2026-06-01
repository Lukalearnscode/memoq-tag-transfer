# Agent Guide for memoq-tag-transfer

This file helps AI coding agents (Claude Code, Cursor, Copilot, etc.) understand this project quickly.

## What this project does

A CLI tool that transfers inline XML tags (`<ph>`) from source text to target text in memoQ translation files (.mqxlz), then outputs TMX files for import into memoQ Translation Memory.

**Input**: `.mqxlz` file (ZIP containing `document.mqxliff`)
**Output**: `.tmx` file (standard Translation Memory eXchange format)

## Architecture (4-step pipeline)

```
extract.py → parse.py → place.py → output.py
```

1. `extract.py` — Unzips mqxlz, parses mqxliff XML with lxml. Handles UTF-8 BOM.
2. `parse.py` — Walks `<trans-unit>` elements, classifies `<ph>` tags by their `displaytext` attribute (accent-gn, linktext, physical, etc.). Outputs simplified text with `{N}` placeholders.
3. `place.py` — Sends source (with `{N}` placeholders) + plain target to an LLM. The LLM inserts `{N}` markers into the target at correct positions. Validates tag count matches.
4. `output.py` — Replaces `{N}` placeholders with real `<ph>` XML from source. Generates TMX.

Entry point: `cli.py` (two subcommands: `analyze` and `transfer`).

## Key constraints — read before making changes

- **Never serialize full mqxliff with lxml** — `etree.tostring(root)` breaks memoQ compatibility (changes XML declaration quotes, self-closing tag spaces, attribute order, CDATA, &quot;). Only use lxml for reading/parsing.
- **Never deepcopy ph elements** — causes namespace pollution (ns0 prefix). Extract ph XML strings directly and strip xmlns manually.
- **Target text is sacred in replay mode** — when the target already has a translation, the plain text must not change. Only tags are inserted.
- **All source tags must appear in target** — same count, no extras, no omissions. Validate after LLM returns.
- **mrk revision marks** — `mq:tctype="del"` content must be skipped, `"ins"` content must be kept. Never mix them.
- **Single-segment API calls** — batch calls produce unreliable tag placement. Process one segment at a time.

## Tech stack

- Python 3.9+
- lxml (XML parsing)
- openai SDK (LLM calls via any OpenAI-compatible API)
- python-dotenv (config)

## Running locally

```bash
bash setup.sh          # install deps + create .env
# edit .env with API key
memoq-tag-transfer analyze test.mqxlz
memoq-tag-transfer transfer test.mqxlz
```

## Adding a new tag type

1. Add detection logic in `parse.py:classify_tag()` — match on `displaytext` content
2. Add placement rules in `place.py:SYSTEM_PROMPT` if the new tag has unique behavior
3. Unknown tags are already handled gracefully (flagged for user review)
