# memoQ Tag Transfer

**一句话说明 / TL;DR:**
把 memoQ 文件里源文的颜色标签、技能链接等 inline tag 自动放到译文正确位置，输出 TMX 文件导入翻译记忆库。

> Automatically transfer inline tags (colors, skill links, styles) from source to target in memoQ mqxlz files → output TMX → import into memoQ TM.

---

## Why / 这工具解决什么问题？

游戏本地化的翻译文件里有大量 inline tag，长这样：

```
源文: 消耗<ph id="1">...</ph>40<ph id="2">...</ph>点能量
译文: Consumes 40 Energy     ← 标签哪去了？？
```

翻译完之后，tag 丢了。手动放回去？一个文件几百行，每行 5-10 个 tag，放错一个游戏里就显示异常。

**本工具自动完成这件事**：读源文 tag → 理解语义 → 用 AI 放到译文正确位置 → 输出 TMX。

---

## Quick Start / 一键开始

```bash
git clone https://github.com/YOUR_USERNAME/memoq-tag-transfer.git
cd memoq-tag-transfer
bash setup.sh
```

装好后，编辑 `.env` 填入你的 [DeepSeek API Key](https://platform.deepseek.com/)（也支持任何 OpenAI 兼容 API）。

然后就能用了：

```bash
# 第一步：看看文件里有什么
memoq-tag-transfer analyze your_file.mqxlz

# 第二步：转移 tag，生成 TMX
memoq-tag-transfer transfer your_file.mqxlz
```

---

## How It Works / 工作原理

```
┌─────────────┐    ┌───────────┐    ┌───────────┐    ┌───────────┐
│  .mqxlz     │ →  │  解析 tag  │ →  │  AI 放置   │ →  │  输出 TMX  │
│  (ZIP 压缩)  │    │  识别语义  │    │  到译文中   │    │  导入 memoQ │
└─────────────┘    └───────────┘    └───────────┘    └───────────┘
```

1. **Extract** — 解压 mqxlz，拿到 mqxliff（XML 格式的双语文件）
2. **Parse** — 解析每个 `<ph>` 标签的类型：是颜色高亮？技能链接？换行符？
3. **Place** — 把源文 tag 和纯译文一起发给 LLM，让它在正确位置插入 tag
4. **Output** — 生成 TMX 文件，可直接导入 memoQ 本地翻译记忆库

---

## Supported Tags / 支持的 Tag 类型

| Tag 类型 | 作用 | 源文示例 | 译文结果 |
|----------|------|----------|----------|
| accent-gn | 数值/属性绿色高亮 | 消耗`{1}`40`{2}`点能量 | Consumes `{1}`40`{2}` Energy |
| linktext + Q5 | 可点击技能链接（4 tag 一组） | `{1}{2}`寒冰射线`{3}{4}` | `{1}{2}`Frostbeam`{3}{4}` |
| physical | 物理伤害颜色 | `{1}`物理伤害`{2}` | `{1}`Attack DMG`{2}` |
| br | 换行 | 位置不变 | 位置不变 |
| size + tipsYellow | 标题样式 | 段落开头 | 段落开头 |
| i + text_third_gray | 灰色斜体描述 | 描述段开头 | 描述段开头 |

遇到没见过的 tag？工具会**标记出来让你确认**，不会乱猜。

---

## Commands / 命令详解

### `analyze` — 预览文件内容

```bash
# 查看全部
memoq-tag-transfer analyze file.mqxlz

# 只看第 1~20 行
memoq-tag-transfer analyze file.mqxlz --start 1 --end 20
```

输出示例：
```
--- Row 1 (id=1) ---
  SRC: 消耗{1}40{2}点{3}能量{4}，持续{5}4{6}秒
       {1}: gn_open — green highlight start
       {2}: style_close — style close
       ...
  TGT: Consumes 40 Energy for 4s
```

### `transfer` — 转移 tag 并生成 TMX

```bash
# 基本用法（输出到同名 .tmx）
memoq-tag-transfer transfer file.mqxlz

# 指定输出路径
memoq-tag-transfer transfer file.mqxlz -o output.tmx

# 只处理部分行
memoq-tag-transfer transfer file.mqxlz --start 1 --end 50

# 指定语言对（默认 zh-CN → en-US）
memoq-tag-transfer transfer file.mqxlz --src-lang ja-JP --tgt-lang en-US
```

---

## Import into memoQ / 导入 memoQ

生成 TMX 后：

1. 打开 memoQ → **资源** → **Translation Memories** → **新建本地 TM**
2. 设置语言对（如 zh-CN ↔ en-US）
3. **导入** 生成的 TMX 文件
4. 在项目设置中关联该 TM → **预翻译**

再次导入相同源文的条目会自动覆盖旧条目。

---

## Configuration / 配置

编辑 `.env` 文件：

```env
# DeepSeek（推荐，便宜好用）
DEEPSEEK_API_KEY=sk-your-key-here
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat

# 或者用 OpenAI
# OPENAI_API_KEY=sk-your-key-here
# OPENAI_BASE_URL=https://api.openai.com/v1
# OPENAI_MODEL=gpt-4o
```

支持任何 OpenAI 兼容 API（DeepSeek / OpenAI / Ollama 本地模型等）。

---

## Project Structure / 项目结构

```
memoq-tag-transfer/
├── setup.sh                 # 一键安装
├── tag_transfer/
│   ├── cli.py               # 命令行入口
│   ├── extract.py           # 解压 mqxlz → mqxliff
│   ├── parse.py             # 解析 trans-unit，识别 tag 类型
│   ├── place.py             # 调用 LLM 把 tag 放到译文中
│   └── output.py            # 生成 TMX 文件
├── examples/sample.md       # 使用示例
├── .env.example             # API key 模板
├── requirements.txt
└── pyproject.toml           # pip install -e .
```

---

## Design Decisions / 为什么这样设计？

**为什么输出 TMX 而不是写回 mqxlz？**
写回 mqxlz 有多个失败点（ZIP 格式不兼容、lxml 序列化破坏 memoQ 兼容性、服务器权限）。TMX + 本地 TM + 预翻译是经过验证的可靠方案。

**为什么用 LLM 放 tag 而不是纯规则？**
简单场景（纯数值高亮）可以用规则，但游戏本地化经常有语序变化（中→英），需要语义理解。LLM 能可靠处理这类情况。

**为什么单条处理而不是批量？**
批量调 API 放 tag 结果不稳定。单条处理虽然慢一点，但准确率高得多。

---

## License

MIT — 随便用，不用问。
