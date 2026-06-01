# memoQ Tag Transfer

将 memoQ 导出的 mqxlz 文件中，源文的 inline tag（颜色标记、技能链接、样式标签等）自动转移到译文对应位置，输出 TMX 文件导入 memoQ 翻译记忆库。

Transfer inline tags (color markers, skill links, style tags, etc.) from source to target in memoQ mqxlz files. Outputs TMX for import into memoQ Translation Memory.

## The Problem / 解决什么问题

Game localization files exported from memoQ contain inline tags like `<ph>` that control text styling (colors, links, font sizes). When translators work on the target text, these tags need to be placed at the correct positions — a tedious, error-prone manual task.

游戏本地化文件中有大量 inline tag 控制文字样式（颜色高亮、技能链接、字号等）。翻译时这些 tag 需要手动放到译文正确位置——费时、易错。

This tool automates the process: it reads the source tags, understands their semantics, and uses an LLM to place them into the translated text.

本工具自动完成这个过程：读取源文 tag，理解其语义，用 LLM 将 tag 放入译文对应位置。

## How It Works / 工作原理

```
mqxlz file → extract mqxliff → parse tags → LLM placement → TMX output
                                                                ↓
                                              import into memoQ local TM
```

1. **Extract**: Unzip mqxlz to get the mqxliff XML
2. **Parse**: Identify all `<ph>` tags and their semantics (accent-gn, linktext, physical, etc.)
3. **Place**: Use an LLM (DeepSeek recommended) to insert tags at correct positions in target text
4. **Output**: Generate a TMX file ready for memoQ import

## Supported Tag Types / 支持的 Tag 类型

| Tag | Semantic | Example |
|-----|----------|---------|
| accent-gn | Green highlight (numbers, stats) | `{1}40{2}` Energy |
| linktext + Q5 | Clickable skill link (4-tag group) | `{3}{4}Frostbeam{5}{6}` |
| physical | Damage color | `{1}Attack DMG{2}` |
| br | Line break | structural |
| size / tipsYellow | Title styling | structural |
| i / text_third_gray | Gray italic description | structural |

Unknown tags are flagged for manual review instead of guessing.

## Install / 安装

```bash
git clone https://github.com/YOUR_USERNAME/memoq-tag-transfer.git
cd memoq-tag-transfer
pip install -e .
```

## Setup / 配置

Copy `.env.example` to `.env` and fill in your API key:

```bash
cp .env.example .env
```

```env
# DeepSeek (recommended, cheap and effective)
DEEPSEEK_API_KEY=sk-your-key-here
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
```

Any OpenAI-compatible API works (DeepSeek, OpenAI, local models via Ollama, etc.).

## Usage / 使用

### Analyze — Preview segments and tags

```bash
memoq-tag-transfer analyze your_file.mqxlz
memoq-tag-transfer analyze your_file.mqxlz --start 1 --end 20
```

### Transfer — Place tags and generate TMX

```bash
memoq-tag-transfer transfer your_file.mqxlz
memoq-tag-transfer transfer your_file.mqxlz -o output.tmx --start 1 --end 50
memoq-tag-transfer transfer your_file.mqxlz --src-lang zh-CN --tgt-lang en-US
```

### Import TMX into memoQ / 导入 memoQ

1. Open memoQ → Resources → Translation Memories → Create new local TM
2. Set language pair (e.g., zh-CN ↔ en-US)
3. Import the generated TMX file
4. Link the TM to your project → Pre-translate

## Design Decisions / 设计决策

**Why TMX instead of writing back to mqxlz?**
Writing back to mqxlz has multiple failure points (ZIP format incompatibility, filename mismatch, server permissions). TMX + local TM + pre-translate is a proven reliable workflow.

**Why LLM for tag placement?**
Rule-based placement works for simple cases (pure numbers), but game localization often has word order changes between languages (e.g., Chinese → English) that require semantic understanding. LLMs handle this reliably.

**Why not batch API calls?**
Single-segment processing is more reliable. Batch calls with DeepSeek can produce inconsistent results for tag placement.

## License

MIT
