# Usage Examples

## Example: Analyze a file

```bash
$ memoq-tag-transfer analyze skills.mqxlz --start 1 --end 3

--- Row 1 (id=1) ---
  SRC: 消耗{1}40{2}点{3}能量{4}，持续{5}4{6}秒
       {1}: gn_open — green highlight start
       {2}: style_close — style close
       {3}: gn_open — green highlight start
       {4}: style_close — style close
       {5}: gn_open — green highlight start
       {6}: style_close — style close
  TGT: Consumes 40 Energy for 4s

--- Row 2 (id=2) ---
  SRC: {1}{2}寒冰射线{3}{4}造成{5}物理伤害{6}
       {1}: link_open — skill link start
       {2}: q5_open — skill link style start
       {3}: style_close — style close
       {4}: link_close — skill link close
       {5}: phys_open — physical damage color start
       {6}: style_close — style close
  TGT: Frostbeam deals Attack DMG
```

## Example: Transfer tags

```bash
$ memoq-tag-transfer transfer skills.mqxlz -o skills.tmx

  Row 1: 6 tags ... OK
  Row 2: 6 tags ... OK
  Row 3: no tags, skip

TMX written: skills.tmx (2 segments)
```

## Generated TMX

```xml
<?xml version="1.0" encoding="utf-8"?>
<tmx version="1.4">
  <header creationtool="memoq-tag-transfer" creationtoolversion="0.1.0"
          segtype="sentence" o-tmf="memoQ" adminlang="en-US"
          srclang="zh-CN" datatype="plaintext"/>
  <body>
    <tu>
      <tuv xml:lang="zh-CN">
        <seg>消耗<ph id="1">...</ph>40<ph id="2">...</ph>点<ph id="3">...</ph>能量<ph id="4">...</ph>，持续<ph id="5">...</ph>4<ph id="6">...</ph>秒</seg>
      </tuv>
      <tuv xml:lang="en-US">
        <seg>Consumes <ph id="1">...</ph>40<ph id="2">...</ph> <ph id="3">...</ph>Energy<ph id="4">...</ph> for <ph id="5">...</ph>4s<ph id="6">...</ph></seg>
      </tuv>
    </tu>
  </body>
</tmx>
```
