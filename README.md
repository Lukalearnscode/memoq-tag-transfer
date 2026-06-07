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
4. **Verify** — check every segment: tag count, content, nesting, order
5. **Output** — generate TMX file

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
