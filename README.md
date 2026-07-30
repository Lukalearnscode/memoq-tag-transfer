# memoQ Tag Transfer

翻译完的文件里，颜色、链接等格式标签（tag）丢了。这个工具自动把它们放回去。

After translation, inline formatting tags (colors, links, styles) are lost. This tool puts them back automatically.

```
源文: 消耗 {green}40{/green} 点能量        ← tag 标记了"40"是绿色
译文: Consumes 40 Energy                   ← 翻译后 tag 没了
结果: Consumes {green}40{/green} Energy    ← 工具自动放回
```

## Install / 安装

```bash
git clone https://github.com/Lukalearnscode/memoq-tag-transfer.git
cd memoq-tag-transfer
bash setup.sh
```

Then edit `.env` with your [DeepSeek API Key](https://platform.deepseek.com/) (or any OpenAI-compatible API).

装好后编辑 `.env`，填入 [DeepSeek API Key](https://platform.deepseek.com/)（也支持 OpenAI 等兼容 API）。

## Use / 使用

```bash
# Preview what's in the file / 预览文件内容
memoq-tag-transfer analyze your_file.mqxlz

# Transfer tags → generate TMX / 转移 tag，生成 TMX
memoq-tag-transfer transfer your_file.mqxlz

# Verify tag consistency / 验证 tag 完整性
memoq-tag-transfer verify your_file.mqxlz

# Verify + report what each tag pair actually wraps / 顺带导出 tag 语义位置对照表
memoq-tag-transfer verify your_file.mqxlz --semantic-report report.md
```

Output is a `.tmx` file. Import it into memoQ as a local Translation Memory, then pre-translate.

输出 `.tmx` 文件，导入 memoQ 本地翻译记忆库，然后预翻译即可。

## How it works / 工作原理

```
.mqxlz → Extract tags → AI places tags into translation → Output TMX
.mqxlz → 提取 tag    → AI 把 tag 放到译文正确位置      → 输出 TMX
```

1. **Extract** — unzip the mqxlz, read the XML
2. **Parse** — identify each tag's type (color, link, style, etc.)
3. **Place** — send source tags + plain translation to an LLM, it inserts tags at correct positions
4. **Verify** — check every segment: tag count (Counter-based), tag content after
   normalization, structural nesting (stack validation), and tag order
5. **Output** — generate TMX file

### The check that mechanical verification misses / 机械校验抓不到的那一类错

Tag count, content and order can all be correct while the tags wrap the wrong
words: source has `{green}40{/green}` around a number, target has the same pair
around a verb. Every mechanical check passes; the output is still wrong.

数量、内容、顺序全对，tag 却包错了词——源文 `{green}40{/green}` 包的是数值，
译文同一对 tag 包了动词。所有机械校验都过，结果仍然是错的。

`--semantic-report` writes a side-by-side table of what each tag pair wraps in
source vs target, flags mismatched numbers automatically, and leaves the rest
for human review.

## Tests / 测试

```bash
python3 tests/test_verify.py
```

Regression tests for BBCode coverage, bracket false-positives, and the number
regex. Each test exists because the corresponding bug reached a real file.

每个测试都对应一个真实踩过的坑，不是为了覆盖率写的。

## Configuration / 配置

Edit `.env`:

```env
DEEPSEEK_API_KEY=sk-your-key-here
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
```

Works with any OpenAI-compatible API (DeepSeek / OpenAI / Ollama / etc).

## Why TMX, not write back to mqxlz? / 为什么输出 TMX？

Writing back to mqxlz has too many failure points (ZIP format, XML serialization, namespace issues). TMX + local TM + pre-translate is the reliable path.

## License

MIT
