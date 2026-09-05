# memoQ Tag Transfer

翻译完的文件里，颜色、链接等格式标签（tag）丢了。这个工具自动把它们放回去，再检查有没有放错。

After translation, inline formatting tags (colors, links, styles) are lost. This tool puts them back, then checks whether they landed in the right place.

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

Then edit `.env` with your [DeepSeek API Key](https://platform.deepseek.com/) (or any OpenAI-compatible API). Only the `transfer` command needs a key. `verify` runs without one.

装好后编辑 `.env`，填入 [DeepSeek API Key](https://platform.deepseek.com/)（也支持 OpenAI 等兼容 API）。只有 `transfer` 需要 key，`verify` 不需要。

## Use / 使用

```bash
# Preview what's in the file / 预览文件内容
memoq-tag-transfer analyze your_file.mqxlz

# Transfer tags → generate TMX / 转移 tag，生成 TMX
memoq-tag-transfer transfer your_file.mqxlz

# Verify tag consistency / 验证 tag 完整性
memoq-tag-transfer verify your_file.mqxlz

# No memoQ? Verify a plain JSON list of source/target pairs
# 没有 memoQ？直接验证一份源文/译文对照的 JSON
memoq-tag-transfer verify --pairs examples/pairs.json

# Also write a table of what each tag pair wraps in source vs target
# 顺带导出一张表：每对 tag 在源文包了什么、在译文包了什么
memoq-tag-transfer verify --pairs examples/pairs.json --semantic-report report.md

# Add a glossary, so the table can also spot wrong terms inside tags
# 再给一份术语表，这张表就能查出 tag 里的术语错
memoq-tag-transfer verify --pairs examples/pairs.json \
    --glossary examples/glossary.json --semantic-report report.md

# Your project invented its own [tags]? Paired ones are found automatically.
# Self-closing ones need to be named.
# 项目自己发明的 [方括号 tag]：成对的自动认，单个的要报名字
memoq-tag-transfer verify --pairs examples/pairs.json --tags shake,pause
```

Exit code: `0` clean, `1` at least one CRITICAL, `2` the input file could not be used.
The example file `examples/pairs.json` contains several deliberate errors (the six cases below), so it exits `1`.

退出码：`0` 干净，`1` 有 CRITICAL，`2` 输入文件读不了。示例文件故意放了几处错（就是下面六段），所以会退出 `1`。

Output of `transfer` is a `.tmx` file. Import it into memoQ as a local Translation Memory, then pre-translate.

`transfer` 输出 `.tmx` 文件，导入 memoQ 本地翻译记忆库，然后预翻译即可。

## It looks fine. It is not. / 看起来没问题，其实是错的

Six short segments. Read each pair and decide whether the translation is right. Then read what the tool says. All six are in `examples/pairs.json`, so you can run this yourself:

```bash
memoq-tag-transfer verify --pairs examples/pairs.json --glossary examples/glossary.json --semantic-report report.md
```

六段短句。先自己判断译文对不对，再看工具怎么说。六段都在 `examples/pairs.json` 里，上面这条命令自己就能跑。

**1. The number is right. The colour is on the wrong word. / 数字对了，颜色包错了词**

```
源文: 造成<color=#ff0000>150</color>点伤害        ← red around the number
译文: Deals 150 <color=#ff0000>Damage</color>    ← red around "Damage"
```

Same tags, same count, same order. Every mechanical check passes. The side-by-side table says **number mismatch**: the source span holds "150", the target span holds no number.

标签一样、数量一样、顺序一样，机械检查全过。对照表报：**数字不一致**，源文标签包的是 150，译文标签里一个数字都没有。

**2. The highlight got shorter. / 高亮变短了**

```
源文: 队伍会跟随<color=#ffe2af>当前队长</color>的移动
译文: The party follows the current <color=#ffe2af>leader</color>
```

"current" fell outside the tag. The table says **span may have shrunk**: four Chinese characters went in, one English word came out.

「current」掉到了标签外面。对照表报：**跨度可能缩小**，进去四个汉字，出来一个英文词。

**3. The tags are there. Nothing is inside them. / 标签在，里面是空的**

```
源文: 使<style="q5">燃烧</style>的时间延长
译文: extends <style="q5"></style>Burning duration
```

Count, order and nesting are all legal. The table says **empty target span**: "Burning" sits right after the closing tag instead of between the pair.

数量、顺序、嵌套全合法。对照表报：**译文标签里是空的**，Burning 紧贴在闭合标签后面，没进到标签中间。

**4. A tag the tool has never heard of went missing. / 一个工具没见过的标签丢了**

```
源文: 获得[gold]护盾[/gold]
译文: Gain Shield
```

`[gold]` is not on any standard list, so a whitelist-only checker never sees it. This tool notices that `[gold]` and `[/gold]` both appear in the batch, treats the name as a tag, and reports **2 tags missing**.

`[gold]` 不在任何标准名单上，只认白名单的检查器看不见它。这个工具发现整批文字里 `[gold]` 和 `[/gold]` 都出现过，就把它当标签，报：**丢了 2 个标签**。

**5. Every tag is present. They are crossed. / 标签一个不少，交叉了**

```
源文: [gold]金币[/gold]和[blue]宝石[/blue]
译文: [gold]Gold[/blue] and [blue]Gems[/gold]
```

The same four tags on both sides, so a count check passes. The nesting check says **crossed nesting: [gold] was closed by [/blue]**. A game engine draws this wrong or crashes on it.

两边都是这四个标签，数数能过。嵌套检查报：**交叉嵌套，[gold] 被 [/blue] 关掉了**。游戏引擎画这一句会出错，或者直接崩。

**6. This one is not an error, and the tool knows. / 这一个不是错，工具也知道**

```
源文: Pick {Amount:plural:an item|{} items} to sell.
译文: 选择{Amount}件物品出售。
```

The English variable chooses between "an item" and "N items" at runtime. Chinese has no plural, so the translator collapsed it to `{Amount}`. A strict checker calls this a lost tag. This tool gives a **warning to glance at**, not an error. On one real game's official translation, that single difference was over 800 false alarms.

英文变量在运行时从「an item」和「N items」里选一个显示。中文没有单复数，译员把它收成 `{Amount}`。死板的检查器会说标签丢了。这个工具只给一句**提醒**，不当错误。在一款游戏的官方译文上，这一条差别就是八百多条假警报。

## How it works / 工作原理

```
.mqxlz → Extract tags → AI places tags into translation → Verify → Output TMX
.mqxlz → 提取 tag    → AI 把 tag 放到译文正确位置      → 验证   → 输出 TMX
```

1. **Extract** — unzip the mqxlz, read the XML
2. **Parse** — identify each tag's type (color, link, style, etc.)
3. **Place** — send source tags + plain translation to an LLM, one segment at a time; it inserts tags at the right positions
4. **Verify** — check every segment (the table below)
5. **Output** — write the TMX

## What `verify` checks / verify 查什么

| Check | Severity | In plain words | 大白话 |
|---|---|---|---|
| Tag count | CRITICAL | Same number of tags on both sides | 两边 tag 数量一样 |
| Tag content | CRITICAL | Same tags, duplicates counted exactly | 是同样的 tag，重复的也一个不差 |
| Nesting | CRITICAL | Pairs open and close in a legal order; `[a]x[/b]` is broken | 成对 tag 要一层套一层，`[a]x[/b]` 是坏的 |
| Order of position tags | CRITICAL | memoQ's own bpt/ept/g tags keep the source order | memoQ 自己的定位 tag 不能换位置 |
| Order of word-wrapping tags | WARNING | `[gold]…[/gold]` may move when the word order changes | 包着词的 tag 跟着词换位置是正常的 |
| Order of placeholders | WARNING | `{N}`, `%s` may move | 变量占位符换位置是正常的 |
| Conditional variables | WARNING | `{N:plural:a\|b}`: the option text may be translated; a Chinese target may drop it | 条件变量里的选项文字可以翻；中文去掉整个变量只提醒 |
| Spaces around line breaks | WARNING | `<br>` must not gain spaces the source did not have | 换行 tag 旁边不能凭空多出空格 |
| Redaction blocks ■ | CRITICAL | Runs of ■ must match one by one | 涂黑块的每一串长度都要一样 |

### Custom tags / 自定义 tag

Standard BBCode names (`[color]`, `[b]`, `[url]`, about 40 more) are on a whitelist. A whitelist is what keeps `[TODO]` and `[1]` from setting off alarms. But games invent their own tags: `[gold]`, `[jitter]`. So any name that appears as both `[x]` and `[/x]` anywhere in the batch is treated as a tag too. Self-closing custom tags have no `[/x]` and cannot be found this way; pass them with `--tags`.

标准的方括号 tag（`[color]`、`[b]`、`[url]` 等四十来个）在白名单里。白名单的好处是 `[TODO]`、`[1]` 这种普通方括号不会被当成 tag。但游戏会自己发明 tag，比如 `[gold]`、`[jitter]`。所以整批文字里，凡是既出现 `[x]` 又出现 `[/x]` 的名字，也当 tag 查。单个不成对的自定义 tag 认不出来，用 `--tags` 报名字。

### Conditional variables / 条件变量

Some engines write `{Gems:plural:Gem|Gems}` and pick "Gem" or "Gems" at runtime. The text after the colon is meant to be translated, so it is not compared. Only the name and the type are. A Chinese target that drops the whole thing (Chinese has no plurals) gets a WARNING. An English target that loses it gets a CRITICAL: English needs the plural.

有些游戏引擎会写 `{Gems:plural:Gem|Gems}`，运行时由游戏决定显示哪个词。冒号后面的文字是要翻译的，所以不参与比对，只比变量名和类型。中文没有单复数，译文把整个变量去掉只给 WARNING。英文译文丢了它是 CRITICAL，英文需要单复数。

### The check that mechanical verification misses / 机械校验抓不到的那一类错

Count, content and order can all be correct while the tags wrap the wrong words: the source has `{green}40{/green}` around a number, the target has the same pair around a verb. Every mechanical check passes; the output is still wrong.

数量、内容、顺序全对，tag 却包错了词：源文 `{green}40{/green}` 包的是数值，译文同一对 tag 包了动词。所有机械校验都过，结果仍然是错的。

`--semantic-report` writes a side-by-side table of what each tag pair wraps in source vs target, and raises five flags automatically:

1. **number mismatch** — the numbers inside the pair differ
2. **empty target span** — the source pair wraps text, the target pair wraps nothing
3. **term mismatch** — the source span contains a glossary term, the target span lacks its translation (needs `--glossary`)
4. **span expanded** — a glossary translation appears inside the target pair but its source term is outside the source pair (needs `--glossary`)
5. **span density** — the wrapped text is much longer or shorter than the source suggests

No flag does not mean pass. It means the machine has nothing to say and a person should read that row.

`--semantic-report` 出一张表，每对 tag 在源文包了什么、译文包了什么并排列出，并自动标出五种情况：数字对不上、译文 tag 里是空的、术语表里的词在译文 tag 里没出现（要术语表）、术语表里的词从 tag 外面混进来了（要术语表）、包住的内容比源文明显长或短。没标的行不等于通过，只是机器没话说，该人来看。

## Input formats / 输入格式

`--pairs`: a JSON list. Each record has `id`, `source`, `target`. A segment that is meant to stay untranslated must say `"translatable": false`; an empty `target` without it is an error, and so is a missing `target` key or a duplicate `id`. These used to pass silently.

```json
[
  {"id": "1", "source": "消耗<style=\"accent-gn\">40</style>点能量", "target": "Consumes <style=\"accent-gn\">40</style> Energy"},
  {"id": "2", "source": "DO NOT TRANSLATE", "target": "", "translatable": false}
]
```

`--glossary`: a JSON list of `{"source", "target"}`. Several accepted translations are separated by `|`.

```json
[
  {"source": "攻击力", "target": "ATK"},
  {"source": "金币", "target": "Gold|Coins"}
]
```

## Tests / 测试

```bash
python3 tests/test_verify.py
```

48 regression tests, no pytest needed. Each one exists because the corresponding bug reached a real file. The example segments in the tests are made up; they keep the shape of the real ones, not the words. One test runs `examples/pairs.json` and checks that every case above still fires.

48 个回归测试，不需要 pytest。其中一条专门跑 `examples/pairs.json`，确认上面六段每一段都还能报出来。每个测试都对应一个真实踩过的坑，不是为了覆盖率写的。测试里的例句是编的，保留真实句子的形状，不保留原话。

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
