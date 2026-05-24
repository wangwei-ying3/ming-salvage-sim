# UI 素材清单 + AI 提示词

明末力挽狂澜 UI 素材规范。风格：**明末古风 painterly 彩绘**（朱红 / 金 / 旧纸调，写实有质感，P社战略游戏 HUD 观感）。所有素材遵循同一风格锚点，保证视觉一致性。

> 注：早期版本曾按「水墨风」规划，现已废弃。已出图的印章/卷轴为 painterly 彩绘，全套以此为准。

---

## 风格锚点（所有提示词共用）

每条提示词都拼接以下 **STYLE_ANCHOR**（**严格锁明朝**，不要清代/宋代/日式混入）：

```
game UI asset, Ming dynasty Chinese historical aesthetic,
strictly Ming dynasty (明代), NOT Qing dynasty, NOT Song dynasty, NOT Japanese,
painterly but clean, semi-realistic with tactile material texture,
soft studio lighting, crisp edges,
muted gold / cinnabar red / aged paper / faded jade palette, no bright modern colors,
no text, no characters, no signatures unless specified
```

**为什么 painterly 而非水墨**：印章/卷轴已按此风出图，HUD 图标需要质感与体积感（印石倒角、木轴高光、纸张卷边），纯水墨平涂撑不起战略游戏 HUD。

**反例**（出现立即重生）：水墨平涂无体积、扁平 flat 矢量、3D 塑料渲染感、鲜艳现代插画、清宫廷重彩、日式 sumi-e。

**调色板锁定**（生成后校色）：

- 旧纸底 `#f4ecd8`
- 朱砂红 `#9e2a2b`
- 暗金 `#d2a34a`
- 重墨木 `#43250f`
- 青黛 `#2c3e50`（仅地图水系）

**禁止**：鲜艳现代色、人物面孔细节、文字签名、卡通线条、塑料渲染感、现代符号。

---

## 一、HUD 图标

单物件、透明背景、512×512 方图（卷轴可横图）。

**图标专用尾巴**（STYLE_ANCHOR 之外再加）：
```
single object centered, isometric-ish front view,
transparent background (PNG with alpha), no baked drop shadow,
512x512, strategy game HUD icon
```

| 文件 | 用途 | 主体描述 |
|---|---|---|
| `icon_seal.png` | 奏疏（印章） | A traditional Chinese imperial seal (印章), carved jade-and-cinnabar stone seal stub standing upright, square top with a small carved beast knob (螭虎钮), deep red seal-paste residue on the carved face, weathered ancient stone texture, slight 3/4 angle |
| `icon_scroll.png` | 诏书草案（合上的卷轴） | A tightly rolled-up closed Chinese imperial edict scroll (卷起的圣旨卷轴), shown horizontally, fully wound shut so only the rolled cylinder shows, two carved dark-wood roller rods with brass end caps at both ends, aged cream silk-paper wound tight, faint yellow brocade trim along the rolled edge, tied closed with a thin silk cord |
| `icon_ming_emblem.png` | 大明图标（状态栏左侧） | A Ming dynasty imperial emblem, a circular gold medallion with a stylized five-clawed Chinese dragon coiled around the character 明, deep cinnabar red enamel inlay, ornate gold filigree ring border, regal and symmetrical |

**易错点**：
- 卷轴必须带 `tightly rolled-up` / `closed` / `fully wound shut` / `tied closed`，否则模型默认出摊开的。
- 印章带 `slight 3/4 angle` 才有体积，正面平视会发扁。

---

## 二、弹窗 / 面板背景图

代码有 3 个全屏弹窗 + 朝堂抽屉 + 地图详情小面板 + 加载页，共 6 张。

**背景专用尾巴**（STYLE_ANCHOR 之外再加）：
```
aged parchment / silk-paper texture, subtle grain and gently worn edges,
center area left completely empty and clear for UI text,
soft even lighting, low contrast so foreground UI stays readable,
game UI background panel
```

| 文件 | 尺寸 | 用途 | 主体描述 |
|---|---|---|---|
| `bg_state.png` | 1600×1000 | 国势与奏报弹窗 | A Ming dynasty official report scroll surface, wide aged memorial-paper texture, faint vertical ruling lines like an old official document, subtle cinnabar seal marks in the corners, ornate dark-wood and gold edge trim |
| `bg_chat.png` | 1600×1000 | 大臣召对弹窗 | A Ming dynasty palace audience hall interior, dim imperial throne hall, red lacquer columns, carved gold ceiling beams, hanging palace lanterns, faint incense haze, warm shadowed atmosphere, deep and softly blurred |
| `bg_edict.png` | 1600×1000 | 诏书草案弹窗（展开圣旨） | A Ming dynasty unrolled imperial edict scroll spread fully open horizontally, a thick carved dark-wood roller rod with brass dragon-head end caps at the LEFT edge and another identical roller rod at the RIGHT edge, wide aged cream silk-paper spread flat between the two rods, faint yellow brocade border along top and bottom |
| `bg_court.png` | 512×1024 竖 | 朝堂抽屉 / 大臣选择 | A Ming dynasty court antechamber, red lacquer wall, carved wood lattice screen, a faint hanging scroll painting, soft warm light, vertical composition |
| `bg_node.png` | 512×640 | 地图详情小面板（点省份/关隘节点浮出，显示地区数值+驻军） | A small Ming dynasty intelligence report card, a worn slip of memorial paper with a thin dark-wood frame, faint cinnabar seal stamp in one corner |
| `bg_loading.png` | 1920×1080 | 加载页 | A Ming dynasty study desk scene, a closed imperial scroll, an ink stone and brush, a burning candle casting warm light, antique parchment atmosphere, quiet and atmospheric |

**易错点**：
- 每张必带 `center area left empty for UI text`，否则花纹挡内容。
- `bg_edict` 必带 `roller rod at the LEFT edge and another at the RIGHT edge` / `spread fully open between the two rods`，否则丢轴。
- `bg_chat` 宜深、虚化（`deep and softly blurred`），别抢聊天文字。

---

## 三、全屏地图背景

| 文件 | 尺寸 | 用途 |
|---|---|---|
| `map_ming_china.png` | 1920×1080 | 主地图底图 |

**主体描述**（STYLE_ANCHOR 之外加）：
```
A Ming dynasty era map of China as a strategy game background,
hand-painted antique cartography style,
the landmass of Ming China with coastline, the Great Wall winding across the north,
major rivers (Yellow River and Yangtze), mountain ranges as soft painted relief,
surrounding ocean in muted blue-green, aged parchment paper texture,
no place-name labels, no text, no grid, no icons,
low contrast and slightly desaturated so UI markers stay readable on top,
1920x1080 landscape, full-screen game map background
```

**易错点**：必带 `no place-name labels, no text, no grid, no icons` 留干净给节点；必带 `low contrast and slightly desaturated` 防节点被吃。

**fallback**：生不出来就下明代公版地图扫描（《大明混一图》1389 / 罗洪先《广舆图》1555 / 《郑和航海图》），裁中国本土，PS 调色到锚点调色板。

---

## 字体（独立于素材）

Google Fonts，免费商用：

- 标题：`Ma Shan Zheng`（楷书）或 `ZCOOL XiaoWei`（小楷）
- 正文 / 数字：`Noto Serif SC`（思源宋体）

引入：`<link>` 进 `web/index.html`。

---

## 一致性检查清单

每张生成后过一遍：

- [ ] 调色板只含锚点 5 色？多出的去 PS 改。
- [ ] 风格是 painterly 彩绘、有质感体积？水墨平涂 / 扁平矢量 / 塑料 3D 重生。
- [ ] 明代而非清代？（无剃发辫、无顶戴花翎、无马蹄袖）
- [ ] 有无文字 / 签名？有则裁掉或重生。
- [ ] HUD 图标：背景透明、无白边锯齿、无 baked 阴影？
- [ ] 背景图：中心区干净、能压住 UI 文字？

---

## 推荐工作流

1. 选一个 AI 工具（Midjourney / 即梦），全程不换。换工具风格立刻飘。
2. 同一会话连续生成，风格继承。Midjourney 用 `--sref` 锁风格参考。
3. 印章/卷轴已出图——后续每张与已出图比对，飘了重生。
4. 全套一次性出，别零散补，零散补必穿帮。

---

## 交付路径

素材放 `web/public/assets/`。文件名只是代码引用用，命名规则：`icon_`=小图标，`bg_`=弹窗/页面背景，`map_`=地图底。

```
web/public/assets/
├── icon_seal.png         印章图标 —— 底部「奏疏」按钮
├── icon_scroll.png       卷轴图标（合上的）—— 底部「诏书草案」按钮
├── icon_ming_emblem.png  大明国章圆牌 —— 左上角状态栏，替皇冠
├── bg_state.png          国势奏报弹窗背景
├── bg_chat.png           大臣召对弹窗背景
├── bg_edict.png          诏书草案弹窗背景（展开圣旨形态）
├── bg_court.png          朝堂抽屉背景（窄竖图，左侧大臣选择栏）
├── bg_node.png           地区情报小卡背景（点地图节点浮出的小框）
├── bg_loading.png        加载页背景（启动「启封奏牍」那屏）
└── map_ming_china.png    大明全图底图（整个主背景地图）
```

放好后告我，我接入 `web/src/styles.css`（`background-image` / `<img>`）。
