# 人物性格细化计划

> 从 `docs/plan-military-character-events.md` 第二节 2.1 / 2.4 部分拆出。聚焦"性格细化 + style 进化"，不含郑芝龙 / 台湾（仍留母文档 2.2 / 2.3）。
> 约束沿用："设定 JSON 是唯一来源"、"无 fallback"、不破坏 DeepSeek 前缀缓存。

---

## 一、现状缺口

- 每角色已有 `style`（单串）、`personal_skills`（3 条短语）、`loyalty/ability/integrity/courage` 四维。
- `style` 太短（如"持重守法" vs "明哲守位"），LLM 演不出区分度。
- 朝堂列表按官品排序，同品同部多人无次序，玩家找不到重点人。
- 缺"小巧思"：口头禅、忌讳、私下关系、对皇帝的私人记忆点。

---

## 二、设计方案

### A. `style` 内容结构（仅改 `characters.json`，不动 schema）

原："持重守法"（4 字泛标签）。

改为塞进一段，覆盖四层：

```json
"style": "持重守法。奏对必引《左传》，畏寒冬日披狐裘，对内臣板着脸。与钱龙锡同年交厚，与温体仁素不合。崇祯元年九月廷推力争钱谦益当用被驳，至今介怀。"
```

格式约定：

1. 句首 4–6 字基调标签（"持重守法"），稳定不变。
2. 一句 quirks：口头禅 / 习惯 / 忌讳。
3. 一句 private_ties：1–2 个同年/师生/政敌关系。
4. 一句 emperor_memory：与皇帝过往交集（可空）。

后三层均可被进化覆写、追加；首句基调不动。

### B. `style` 进化机制（核心新增）

- `content/prompts/score_extractor.md` JSON schema 加 `character_style_updates` 段：

  ```json
  "character_style_updates": [
    {"name": "韩爌", "new_style": "持重守法。奏对必引《左传》……（新增一句）崇祯二年正月被皇帝当殿斥为「迂阔」，自此奏对不敢再引古。"}
  ]
  ```

- simulator agent 生成邸报时，若大臣经历明显事件（被斥/被擢/丧子/挚友罢黜/与皇帝密谈），允许在 extractor JSON 里改 style。
- 改写规则：基调标签首句不动，quirks/ties/memory 可增可改可删，整段保持四层结构 ≤200 字。
- `apply_score_extraction`（issues.py）多一支：把 `character_style_updates` 写回 `characters` 表 `style` 列。
- `GameContent.characters` 内存对象同步更新，下回合 `registry.create_minister_agent` 重建 system prompt 时自动读到新版。

**缓存边界**：`style` 改了 → 该大臣 system prompt 改了 → 前缀缓存对该大臣失效一次。可接受：进化频率低（多数月份无变化），且只影响被改的那一个 agent。其他大臣缓存不受影响。

### C. 召对触发的硬进化（不靠 extractor）

皇帝主动"调教"路径，与现有后宫 `cultivate_consort` 对称：新 court tool `record_minister_memory(minister, memory_text)`，皇党/司礼监等心腹可在召对中调，把"皇帝亲口对某大臣说过/许过/斥过什么"直接追加到该大臣 `style` 末段。比依赖 extractor 推断更稳。

### D. 排序小巧思（registry 层）

- 同品级内按 `loyalty + ability` 综合排。
- 名册标记角标：新进 `offstage→active` 转的人物名旁加「新到任」、超过 6 个月未召见的加「久疏」。

### E. prompt 强调

`content/prompts/minister_agent.md` 加一段："严格按 `style` 段里的基调、口头禅、习惯、交友、记忆演绎，记忆段是你对皇帝的真实态度，不得忽略也不得反向曲解。"

---

## 三、改动清单

- `content/characters.json`：扩写 `style` 段（先给主要 10 人，按 4 层格式重写一段；其余角色 style 保留原样）。**不改 schema、不加字段**。
- `ming_sim/registry.py`：朝臣名册排序 + 角标。
- `ming_sim/issues.py`：`apply_score_extraction` 加 `character_style_updates` 落库支。
- `ming_sim/tools.py`：新 court tool `record_minister_memory` 哨兵。
- `ming_sim/session.py`：`record_minister_memory` 哨兵 handler（追加到 style 末段，调 db 落库 + content 同步）。
- `ming_sim/db.py`：`update_character_style(name, new_style)`；`GameContent.characters` 内存同步。
- `content/prompts/score_extractor.md`：JSON schema 加 `character_style_updates` 段 + 改写规则（基调不动、四层结构、≤200 字）。
- `content/prompts/season_simulator.md`：允许在大臣经历明显事件时改其 style。
- `content/prompts/minister_agent.md`：加一段"严格按 `style` 演绎，记忆段=你对皇帝的真实态度"。

---

## 四、落地顺序

1. 主要 10 人 `style` 按 4 层格式重写（纯 JSON 改动，最低风险，可独立 commit）。
2. `minister_agent.md` 加演绎强调（同样纯 prompt 改动）。
3. registry 排序 + 角标。
4. `record_minister_memory` 工具 + handler（硬进化路径，比 extractor 更稳，先上）。
5. extractor `character_style_updates` schema + 落库（软进化路径，需 db migration / content 同步联调）。
6. 用 `balance-playtest` skill 跑 10 回合，验证：
   - 缓存命中率（看日志/费用）未明显跌；
   - style 不漂移、不超长、基调首句未被覆盖；
   - 排序角标显示正确。

---

## 五、风险点

- **prompt 缓存**：`style` 进化会让被改大臣的 system prompt 改变 → 前缀缓存对该大臣本月失效一次。务必保证未变动的大臣 `style` 字节级不变（避免误改顺序/空格触发整体缓存失效）。
- **style 漂移**：extractor 反复改写易让 `style` 越写越长、风格走样。硬约束：基调标签首句不动、整段 ≤200 字、新内容追加而非随意覆盖旧记忆；超长由 db 层截断（保留首句基调 + 最近 N 句记忆）。
- **进化触发过频**：simulator 每月都给"被斥/被擢"剧情会让 style 月月变。prompt 端强约束"仅在明显事件时才动 style"，且 extractor 端可在 issues.py 落库前去重（同 name 同月只保留最后一条）。
- **硬进化越权**：`record_minister_memory` 若任何 agent 都能调会被滥用。限定调用者为皇党 / 司礼监 / 心腹 court tools 白名单（在 tools.py 哨兵层校验）。
