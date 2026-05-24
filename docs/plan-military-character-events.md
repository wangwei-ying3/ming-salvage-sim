# 三大系统扩充计划：军事 / 人物 / 事件

> 范围：在不破"设定 JSON 是唯一来源"和"无 fallback"约束下，把军事系统做厚、人物系统加性格细节、事件系统补足李自成 / 张献忠 / 后金三条主推演线。
> 现状基线：`docs/modules/armies.md`、`docs/modules/characters.md`、`docs/modules/events.md` 已定核心数值；`content/external_powers.json` 已有后金/八旗/汉军/蒙古/朝鲜/流寇盘面；`content/events.json` 已散落张献忠谷城再反、李自成入河南、洛阳之屠、开封陷、攻北京等节点，但未串成线。

---

## 一、军事系统：调度 / 物资 / 打仗结算

### 1.1 现状缺口

- `armies.json` 只有兵力/士气/训练/装备/欠饷五项，没有位置、补给线、调度状态。
- `materials` 模块文档已写粮/银/火药/铁/马/船等，但 `content/` 下无对应表，结算时无凭据。
- 战斗只靠 simulator agent 叙事 + extractor 抽数值，没有「调谁、走多久、粮够不够、谁打谁」的结构化盘面，LLM 易拍脑袋。

### 1.2 设计方案

**A. 军队加调度字段**（改 `content/armies.json` schema + `models.Army`）

| 字段 | 含义 | 取值 |
|---|---|---|
| `garrison_region` | 驻地省份 id | regions.json key |
| `deployment` | 当前调度状态 | `garrison` / `marching` / `engaged` / `besieging` / `routed` |
| `march_target` | 调动目的地 | region id 或 `null` |
| `march_eta_months` | 还需几月到位 | int，0=已到 |
| `supply_days` | 随军粮草天数 | int，跌 0 触发哗变/溃散 |
| `commander` | 当前主将 character name | 必须 `status=active` |

> **约束**：`commander` 必须在 `characters.json` 且 `active`。换将走 court tool（兵部专属 `propose_commander_change`），不允许 LLM 凭空写。

**B. 新建 `content/materials.json`**（按 `docs/modules/materials.md` 落表）

只记会影响战役/赈灾结算的：粮、银（已在国库内库不重复）、火药、硝石、铁、战马、船只。每条 `{type, region, quantity, quality}`。征调/转运由 court tool 触发（户部 `propose_material_transfer(type, from_region, to_region, quantity)`），落库由 extractor 抽出的 `material_moves` 应用。

**C. 战斗结算链**（不新加 agent，复用 simulator + extractor）

simulator payload 加 `active_battles` 段：列出本月所有 `engaged`/`besieging` 军队、对手（external_power 或 bandit）、双方兵力/士气/补给、commander 属性。simulator 必须在邸报里给出战报，extractor 抽 `battle_results`：

```json
{
  "army_id": "ji_liao_zhen",
  "outcome": "victory|stalemate|defeat|rout",
  "casualties": {"own": 1200, "enemy": 800},
  "morale_delta": -10,
  "supply_delta": -15,
  "territory_change": {"region": "liaodong", "control_delta": -5}
}
```

`apply_battle_results` 落库：扣兵力、扣补给、改 region control、改 external_power leverage。败/溃军 `deployment` 自动转 `routed`，下月不能再调度。

**D. 玩家可下旨的动作**

- 调兵：`马祥麟率白杆兵自四川赴辽东` → 兵部 court tool `propose_army_dispatch`。
- 拨饷：走现有 `propose_directive` 文本路径，结算时 extractor 抽 `army_pay_delta`。
- 换将 / 督师：兵部 `propose_commander_change`，需要 `commander` 在朝且接命。
- 征调物资：户部 `propose_material_transfer`。

### 1.3 改动清单

- `content/armies.json`：补 6 字段。
- `content/materials.json`：新建。
- `ming_sim/models.py`：`Army` dataclass 加字段；新 `Material` dataclass。
- `ming_sim/db.py`：`armies` 表 schema migration；新 `materials` 表；`apply_battle_results` / `apply_material_moves`。
- `ming_sim/tools.py`：四个新 court tool 哨兵。
- `ming_sim/session.py`：`_apply_*` handler。
- `content/prompts/season_simulator.md`：payload 加 `active_battles` / `materials`，要求战报段。
- `content/prompts/score_extractor.md`：JSON schema 加 `battle_results` / `material_moves`。

---

## 二、人物系统：性格细化 + 郑芝龙 + 台湾

### 2.1 性格提升

> 已拆出至 [`docs/plan-character-personality.md`](./plan-character-personality.md)。本节略。

### 2.2 郑芝龙约束条件

**人物**：加入 `characters.json`，`status=active`（崇祯元年已受抚），`office=福建总兵官 / 五虎游击` 类，`faction` 新增 `海商` 或归 `军队`。

**核心约束**（写入 `quirks` + 专属 court tool 规则）：

1. **地理锁**：调度时 `propose_army_dispatch` 若目标 region 非沿海（非福建/广东/浙江/南直隶），需触发"郑芝龙抗命/拖延"事件——他不肯把水师远离海贸根据地。
2. **财赋绑定**：他每月给朝廷一笔"海税"（写入固定 flow），但若朝廷动他海上专营（如户部要查私市、市舶司加征），下月海税清零并触发"郑氏请辞"事件。
3. **效忠条件**：`loyalty` 受三事件影响——朝廷承认其海上垄断 (+)、朝廷启用其他海商如刘香（−）、台湾红夷（荷兰）压迫（+，他要朝廷支援）。
4. **私军边界**：他的"郑家军"在 `armies.json` 是独立单位 `commander=郑芝龙` 锁定，皇帝无法 `propose_commander_change` 换走，只能罢官（罢则全军反/降）。

### 2.3 台湾设定

**新 region**（`regions.json`）：`id=taiwan`，`status="荷兰东印度公司占据南部大员，西班牙占北部鸡笼/淡水，土著与汉人移民并存"`，初始 `control=0`（不在明朝直辖），`unrest=低`、`tax=0`。

**关联 external power**（`external_powers.json` 新增）：
- `dutch_voc`：荷兰东印度公司，leader=普特曼斯（任内 1629–1636），agenda=巩固大员、扩贸易、与郑芝龙争海权。
- `spanish_taiwan`：西班牙鸡笼据点，1626 占领、1642 被荷兰逐出（历史锚点）。

**约束**：

- 台湾 region 不能由皇帝直接下旨征税/调兵——必须先有 `收复台湾` issue 立项，issue 推进条件硬绑定：(a) 郑芝龙 loyalty ≥ 70 + (b) 福建水师 `military_strength` ≥ 60 + (c) 国库拨款 ≥ 200 万两。
- 若不收复：1642 历史锚点触发"荷兰逐西班牙"邸报；1661（远超主线）才有郑成功收复，本游戏内不强制结束。
- 若收复：触发"台湾设府"事件，region 转 `control=直辖`，每月加海贸税，但 `dutch_voc.stance` 转敌对。

### 2.4 改动清单（仅郑芝龙 / 台湾部分；性格相关见 [`plan-character-personality.md`](./plan-character-personality.md)）

- `content/characters.json`：新增郑芝龙条目（style 按 4 层格式写）。**不改 schema、不加字段**。
- `content/regions.json`：新增 taiwan。
- `content/external_powers.json`：新增 dutch_voc、spanish_taiwan。
- `content/armies.json`：新增 `郑家军` / 福建水师，`commander_locked=true` 字段。
- `ming_sim/issues.py`：新 issue 模板 `收复台湾`。

---

## 三、事件系统：三线推演

### 3.1 设计原则

每条线立为一个长线 `issue`（带 bar 进度），事件按月推进 bar，bar 触发节点事件。bar 两端含义：good 端=朝廷压制，bad 端=该势力坐大。bar 跌 0 触发线终结（败局或重大失土）。

### 3.2 李自成线（issue: `闯军坐大`）

**bar**：100=被剿灭/招抚成功，0=攻陷北京。开局 bar≈85（崇祯元年仅为高迎祥部下小角色）。

**推进 / 回退条件**（gate）：

| 触发数值 | 事件 | bar 变化 |
|---|---|---|
| 陕西 unrest≥70 + 国库赈粮<10 万石 | 王二起义 → 高迎祥聚众 | −5 |
| 高迎祥死（1636 历史锚） | 李自成继闯王 | 节点事件，不动 bar |
| 河南 unrest≥75 + 藩禄未改 | **李自成入河南**（已存在 events.json） | −10 |
| 河南 + 藩禄改革推 50+ | 福王散财犒军，洛阳保 | +15 |
| 洛阳陷 | **李自成烹福王**（已存在） | −15 |
| 孙传庭督师 + 拨饷≥100 万 + bar≥40 | 潼关大捷可能 | +20 |
| 孙传庭战死（1643 历史锚） | 闯军入关中 | −25 |
| bar≤10 | **攻北京**（已存在，主线终点） | 终局 |

### 3.3 张献忠线（issue: `献军流窜`）

**bar**：100=被剿/招抚定，0=据川称帝。

| 触发 | 事件 | bar 变化 |
|---|---|---|
| 湖广 unrest≥65 | 张献忠破舒城 | −5 |
| 1638 招抚（熊文灿在职） | 谷城受抚 | +10（但留隐患） |
| 受抚 + 未削兵权 + 1639.05 | **谷城再反**（已存在） | −15 |
| 杨嗣昌督师入川 | 围剿 | bar 视战果 ±10 |
| 杨嗣昌死（1641 历史锚） | 献军入川加速 | −15 |
| 1644 攻成都 | 大西国 | 终局 |

### 3.4 后金线（issue: `后金叩关`）

**bar**：100=后金内乱/收复辽东，0=入关定鼎。开局 bar≈55（皇太极刚立）。

| 触发 | 事件 | bar 变化 |
|---|---|---|
| 1629.10（己巳之变历史锚） | 皇太极绕道蒙古破口入塞 | −10，触发"袁崇焕勤王" |
| 袁崇焕下狱/凌迟（玩家选） | 北线将领离心 | −5 |
| 1636.04 历史锚 | 皇太极称帝改国号大清 | −5 |
| 朝鲜 `satisfaction`≥60 + 明朝援朝 | 朝鲜稳，东江存 | +5 |
| 1637 丙子胡乱朝鲜降 | 东江孤立 | −10 |
| 1641-1642 松锦之战 | 视玩家是否催战 | 催战且粮不足 → 洪承畴降，−20；缓战且援足 → +15 |
| 蒙古林丹汗死（1634） | 漠南诸部归后金 | −10 |
| bar≤15 | 后金入关 | 终局 |

### 3.5 三线交互

- **共振**：李自成 bar 跌破 30 时，后金 bar 自动 −10（朝廷无力北顾）。反之亦然。
- **资源互斥**：玩家同月对两条线大规模拨饷（≥100 万），第二条按 0.7 折扣到账，差额记"户部挪借"。
- **历史锚点强约束**：上表所有日期由 `context.historical_anchor_for_month` 喂入 simulator，**未达触发条件也必须叙事**（如 1636 皇太极称帝是历史既定），但 bar 影响仅在玩家未压制时生效。

### 3.6 改动清单

- `content/events.json`：补 3 线缺失的中间节点事件（王二起义、高迎祥继闯王、张献忠破舒城、谷城受抚、皇太极称帝、丙子胡乱、松锦之战、林丹汗死）。
- `content/seed_events.json`：3 个 issue 模板（`闯军坐大` / `献军流窜` / `后金叩关`），含 bar 初值、gate 表、终局条件。
- `ming_sim/issues.py`：支持 issue 间 cross-influence（A bar 变动触发 B bar 变动）。
- `ming_sim/context.py`：`historical_anchor_for_month` 补全 1628–1644 三线锚点。

---

## 四、推荐落地顺序

1. **人物 quirks + 排序角标**（最快出体感差异，2–3 小时）。
2. **三线 issue 模板 + 历史锚点**（让推演有骨架，半天）。
3. **郑芝龙 + 台湾**（依赖人物字段，独立可测）。
4. **军事调度字段 + materials.json**（DB migration 风险最大，最后做）。
5. **战斗结算链**（依赖 4，extractor schema 改动需重测平衡）。

每步用 `balance-playtest` skill 跑 10 回合验证不崩盘。

---

## 五、风险点

- **prompt 缓存**：`style` 进化会让被改大臣的 system prompt 改变 → 前缀缓存对该大臣本月失效一次。可接受：进化频率低（多数月份无变化）、只波及单个 agent，其他大臣缓存不受影响。务必保证未变动的大臣 `style` 字节级不变（避免误改顺序/空格触发整体缓存失效）。
- **style 漂移**：extractor 反复改写易让 `style` 越写越长、风格走样。硬约束：基调标签首句不动、整段 ≤200 字、新内容追加而非随意覆盖旧记忆；超长由 db 层截断（保留首句基调 + 最近 N 句记忆）。
- **bar 共振**：三线互相影响易出现"一崩全崩"死局，gate 设计时 cross-influence 系数从 0.3 起调。
- **郑芝龙锁将**：`commander_locked=true` 是新约束，要保证罢官路径不留悬空军队（罢则全军 `deployment=routed` + `commander=null`）。
- **台湾 region**：开局不入 region 列表会让 LLM 列地区时漏掉，需在 prompt 显式说明"台湾未直辖、仅供讨论"。
