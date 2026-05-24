# 建筑模块

建筑模块记录地区内重要设施——开局建筑、预设建筑，以及玩家诏书后续兴建的建筑。是内政与经济的重要部分（火炮厂、矿厂、常平仓、边堡、织造局等）。

## 数据来源与表

- 开局/预设建筑设定在 `content/buildings.json`，`GameDB.seed_static_data` 入库。
- `buildings` 表：每座建筑一行。`building_logs` 表：每次数值变动一条日志（镜像 `region_logs`/`army_logs`）。

## 核心数值

- `level`：规模与能力，1-5。
- `condition`：完好 0-100，**同时是产出折算系数**（实际产出 = `output_amount × condition / 100`）。
- `maintenance`：每月维护费，整数万两，固定从国库扣。
- `risk`：贪腐、事故、扰民、被毁的可能，0-100。
- `output_metric` / `output_amount`：结构化产出。`output_metric` 白名单 `国库`/`内库`/`民心`/`皇威`/`""`（空串=纯叙事无结算产出）；`output_amount` 为每月产出量（国库/内库单位万两，民心/皇威为量表点数）。

## 建筑类型（category 白名单）

`财政` / `军事` / `民生` / `科技` / `交通` / `内廷`。不在白名单的类别直接报错。

## 运行机制

**日常运行纯程序化，不调 LLM。** 每月 `flows.apply_fixed_period_flows` 遍历所有建筑：

- 按 `output_metric` 把折算后产出加进对应账户（国库/内库走 `economy_ledger`；民心/皇威直改量表）。
- 从国库扣 `maintenance`（能扣多少扣多少，跟军饷同逻辑）。

`condition`/`risk`/`level` 是静态的，不自然漂移——只有 LLM 才改。

## 玩家如何影响建筑

皇帝只能通过圣旨命人修、建、查、拨款、停办或追责。**建筑的新建/扩建/废止全部走局势（issue），没有独立的 building_delta/new_buildings 顶层字段。**

- 皇帝下旨建火炮厂/修边堡/设织造局 → `score_extractor` 立一条 `initiative` 局势。
- 局势 bar 跑完结案 → 该 issue 的 `effect_on_resolve`（或失败时 `effect_on_fail`）里的 `buildings` 段落地建筑。
- `buildings` 段是数组，每项 `action` ∈ `create`（新建）/`modify`（改既有数值）/`remove`（拆毁）。落地由 `issues._apply_issue_buildings` 处理。

季末推演（`season_simulator`）邸报只叙事描述建筑运转/产出/损坏，**不给建筑写数值增减、不代标新建筑**。推演官按需调 `list_buildings`/`inspect_building` 查实时数据。

`origin` 列标来源：`preset`（开局设定）/`issue`（局势结案新建）。

## 查询

- 大臣 / 推演官 tool：`list_buildings`、`inspect_building`。
- gate key 寻址：`building.<id>.<field>`（issue 的 trigger_gate / eval_gate 可用）。
- Web：`GET /api/buildings[?region_id=]`；地图省份节点弹窗显示该省建筑。
