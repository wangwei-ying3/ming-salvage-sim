# 产品计划

阶段性产品改造计划。优先级建议：5 → 3 → 6 → 2 → 4 → 1。

---

## 1. UI 中国风水墨

当前 `web/src/main.tsx` 单文件 1114 行 + `web/src/styles.css`。

改造点：

- **调色板**：宣纸 `#f4ecd8` / 墨 `#1a1a1a` / 朱砂 `#9e2a2b` / 青黛 `#2c3e50`。CSS 变量重写。
- **字体**：标题 `Ma Shan Zheng` / `Noto Serif SC`，正文 `Noto Serif SC`。
- **边框**：`border-image` 水墨纹 PNG，或 SVG 笔刷描边。
- **按钮**：去 `lucide-react` 现代图标，改 SVG 印章 + 篆字/楷书标签。hover 用朱印 `box-shadow`。
- **地图节点**：坐标硬编码在 `web_app.py:184 map_nodes`。UI 层换水墨晕染 + 篆字地名。后端不动。
- **卡片**：奏折竖排（可选）。诏书区底色用米黄宣纸纹理。

前置：拆 `main.tsx` 到 `components/`，否则单文件改样式冲突大。

风险：水墨素材成本（图片/字体许可）。

---

## 2. 技能树（管理 / 政治 / 军事 / 学识）

现有 `content/skills.json` + `skill_grants` 表是**大臣视角**技能。本需求是**皇帝视角**技能树，月末投点。

数据：

- 新表 `emperor_skills`：id, tree, tier, prereq, cost, name, desc, effect。
- 新表 `emperor_skill_grants`：skill_id, acquired_turn。
- `game_state` 加列 `skill_points`。
- 新内容文件 `content/emperor_skills.json`，四树各 ~10-15 技能。

流程：

- `resolve_directives` 收尾发技能点（基础 +1/月，重大事件 +2）。
- 颁诏 `/api/decree/issue` 成功后前端跳技能树页 → 投点 → 回主局面。
- 新接口：`GET /api/emperor/skills`、`POST /api/emperor/skills/{id}/learn`。

效果接入：

- **优先做被动加成**——影响 `execution_evaluator_agent` prompt 上下文（"皇帝已掌握 X，对 Y 类执行 +N 倾向"）。
- 不要硬改数值公式，保持 LLM 主导。

设计先决：

- 四树互斥还是兼修？
- 技能点可否重置？
- 是否解锁新草案类型 / 召见对象？

---

## 3. 历史事件预置

现 `content/events.json` 是抽样池，`main.py:select_events` 随机选 N 条。

改造：

- 事件 schema 加字段 `trigger`：`{year, quarter, condition}`。`condition` 用 dict 描述（如 `{"metric": "皇威", "op": ">=", "value": 60}`）。
- `events` 表加列 `trigger_year`、`trigger_quarter`、`trigger_condition`（JSON）。需 DB 迁移。
- `select_events` 改造：先取所有满足触发条件的预置事件，再从随机池补足槽位。
- 新增 `docs/modules/historical-timeline.md` 列时间表：1627Q4 登基 → 1628 魏忠贤倒台 → 1629 己巳之变 → 1630 袁崇焕下狱 → ... → 1644 北京失守。每条挂事件 id。

风险：预置事件多了挤掉随机性，回合槽位需要保留 1-2 个给随机。

---

## 4. 开局教程

第一月（1627Q4）特殊处理。

数据：

- `game_state` 加列 `tutorial`，默认 1，教程完成后置 0。
- `game_state` 加列 `tutorial_step`，标记当前步骤。

流程：

- 前端检测 `tutorial=1` → 覆盖层引导：地图 → 召见 → 草案 → 颁诏 → 技能树。
- 第一月预置事件 = "登基夜的未来记忆"（见 `docs/dev-roadmap.md`）。
- 第一位大臣首条对话用引导 prompt（"陛下初登基，臣略陈..."）。
- 教程完成写 `tutorial=0`，永不再触发。

与 #2、#3 配合：教程顺带演示技能树投点，第一月预置事件铺垫历史线。

---

## 5. 提示词缓存优化

OpenAI / DeepSeek 前缀缓存规则：前缀需稳定 + 共同前缀 ≥ 1024 token 才命中。

现况问题（`main.py:create_minister_agent` / `state_context` / `event_context`）：

- 大臣 prompt = 人物设定 + skills + tools + **本月奏报 + 钱粮 + 派系态势**。后半段每月变 → 杀整体前缀。
- 多处把动态数据拼在 system prompt 前段。

改造：

- **前缀稳定段**：`content/prompts/game_world.md` + 大臣 base prompt + 人物档案 + skill 列表 + tool schema → 进 system prompt 头部，永不变。
- **可变段后置**：本月奏报、钱粮、派系态势放 user message 或 system prompt 末尾。
- DeepSeek 自动前缀缓存，无需标记。
- OpenAI 需要 `cache_control` 标记 → 看 Agno `OpenAIChat` 是否暴露。不暴露则只能享受 DeepSeek 一侧缓存。
- `edict_parser_agent` / `decree_writer_agent` / `execution_evaluator_agent` 同理：固定 schema + few-shot 示例放前缀，回合数据放后面。

监控：

- 加日志记录每次 prompt 长度 + `prompt_tokens_details.cached_tokens`。
- 量化命中率前后对比。

前置：先验证 Agno 是否暴露 `cache_control`。若不暴露，绕过 Agno 自起 client 风险大，仅做"前缀稳定化"即可。

---

## 6. 数值优化：简化 + 深化

现有数值层：

- 钱粮整数（国库、内库，万两）。
- 0-100 量表：民心、皇威、边防、民变、党争、执行、瞒报。
- 地区字段：public_support / unrest / grain_security / gentry_resistance / military_pressure / population / registered_land / hidden_land / tax_quarter。
- 军队字段：supply / morale / training / equipment / arrears / mobility / loyalty / manpower / maintenance_quarter。

### 简化

- **合并冗余**：执行、瞒报与皇威高度相关 → 评估是否合并成单一"朝廷执行力"。
- **隐藏次级数值**：玩家界面只显示一线指标（钱粮 / 民心 / 皇威 / 边防），次级指标（瞒报、党争、士绅阻力）改成隐性，只在事件文本和回奏里露出。
- **数值少而强**：减少玩家面板的数字密度，强化每个数字的解释力。

### 深化

- **联动公式**：当前 `apply_execution_eval` 是 LLM 直接给 delta。深化方向是建立"数值之间的因果链"：
  - 欠饷高 → 士气↓ → 忠诚↓ → 哗变事件触发。
  - 民心低 + 民变高 → 地区税收基数↓。
  - 党争高 → 执行偏离↑ → 旨意结果与原意偏差↑。
- **阈值事件**：数值跨阈值自动触发事件（民心 < 30 → "京师粮价上涨"；边防 < 40 → "后金绕袭传闻"）。挂到 #3 历史事件预置之上。
- **趋势可视**：前端给每个数值加 7 日/4 月迷你折线图，玩家看趋势而非瞬值。
- **派系深化**：当前派系数值简单。加"利益受损度" + "反弹概率"，让派系不只是数字而是会主动出招的实体。

### 实施顺序

1. 先做"隐藏次级数值"——零侵入，只改前端。
2. 再做"阈值事件"——和 #3 同期。
3. 最后做"联动公式"——需要重写 `apply_execution_eval`，风险大。

设计先决：

- 哪些数值合并？哪些隐藏？需先和玩法定下来。
- 联动公式是写死规则还是让 LLM 评估时参考"联动提示"？后者更灵活但费 token。

---

## 关键约束（适用所有项）

参见 `CLAUDE.md`：

- 不允许 LLM fallback。
- 设定文件 `content/*` 是唯一来源，不在代码硬编码副本。
- 钱粮是整数（万两），其他量表是 0-100。
- 网页端不允许空过回合。
