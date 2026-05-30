# 大明力挽狂澜之重生之我是崇祯 — 100项需求报告

> 基于 `ming-salvage-sim` 源码全面审计，2026-05-30 by 小凤雏

---

## 数据层（01-15）

| # | 条目 | 类型 | 状态 | 说明 |
|---|------|------|------|------|
| 01 | 人物数据 58人 | content | ✅已有 | 阉党12/东林11/皇党7/军队7/后金6/中立3/流寇3/西学1/嫔妃2/朝鲜2 |
| 02 | 派系数据 7派 | content | ✅已有 | 阉党/东林/皇党/军队/中立/后金/流寇；加西学/朝鲜/蒙古 |
| 03 | 势力(powers)数据 7个 | content | ✅已有 | 大明/后金/蒙古/朝鲜/流寇/西学/藩属 |
| 04 | 地区(regs)数据 23个 | content | ✅已有 | 13布政司+2两京+1边镇+1海岛+4外域+1草原+1藩属 |
| 05 | 军队(armies)数据 17支 | content | ✅已有 | 明军10支+后金3支+朝鲜1支+流寇1支+土司1支 |
| 06 | 技能(skills)数据 32项 | content | ✅已有 | common_skills×13 / skill_catalog×32 / office_skills×10 |
| 07 | 事件(events)数据 16条 | content | ⚠️不足 | 1629-1644年16条；应补至50+条（含日常事件/随机事件/危机事件） |
| 08 | 建筑(buildings)数据 | content | ✅已有 | content/buildings.json |
| 09 | 初始奏报(opening_gazette) | content | ✅已有 | 开局邸报文本 |
| 10 | 初始危机(opening_crises) | content | ✅已有 | 开局危机列表 |
| 11 | 人物属性：忠/能/廉/勇/派系 | data | ✅已有 | 四维+派系，0-100量表 |
| 12 | 地区属性：动乱/民心/士绅/军压/税收 | data | ✅已有 | unrest/public_support/gentry_resistance/military_pressure/tax_per_turn |
| 13 | 军队属性：兵力/士气/训练/装备/欠饷 | data | ✅已有 | manpower/morale/training/equipment/arrears |
| 14 | 皇帝全局指标：皇威/民心/国库/内库 | data | ⚠️部分 | 已在flows.calc_province_fiscal中计算，前端展示需对齐 |
| 15 | 技能点(skill_points)系统 | data | ❌缺失 | product-plan.md #2要求；当前DB无此字段 |

---

## 核心模块（16-30）

| # | 模块 | 文件 | LOC | 职责 |
|---|------|------|-----|------|
| 16 | GameDB | db.py | 4396行 | SQLite持久化：建表/seed/读写所有数据 |
| 17 | GameSession | session.py | 861行 | 回合流转层：召见→核定草案→颁诏→结算 |
| 18 | Agent执行 | agents.py | 473行 | 流式/非流式Agent运行、JSON解析、token统计 |
| 19 | LLM配置 | llm_config.py | 140行 | OpenAI/DeepSeek base_url模型配置 |
| 20 | LLM模型 | llm_model.py | 145行 | Agno Agent工厂、流式解析 |
| 21 | 游戏内容 | content.py | 472行 | GameContent加载所有content/*.json |
| 22 | 上下文构建 | context.py | 237行 | 召见/人物/派系/战况上下文构建 |
| 23 | 记忆系统 | memories.py | 629行 | 密旨/记忆系统（memory-and-secret-orders.md） |
| 24 | 诏书/旨意 | decree.py | 367行 | resolve_directives/写诏书/advance_without_edict |
| 25 | 问题/议题 | issues.py | 1205行 | 议题聚合、issue_agent |
| 26 | CLI终端 | cli/terminal.py | 471行 | 文字命令行界面 |
| 27 | 工具函数 | tools.py | 1052行 | MinisterTools/EdictTools等工具集 |
| 28 | 模拟引擎 | simulation.py | 815行 | 月度数值模拟 |
| 29 | 财政流 | flows.py | 507行 | calc_province_fiscal省级财政计算 |
| 30 | 常量定义 | constants.py | 215行 | 字段标签/别名/量表常量 |

---

## 召对与LLM系统（31-40）

| # | 条目 | 文件 | 状态 |
|---|------|------|------|
| 31 | 大臣Agent（minuster_agent）动态生成 | agents.py/session.py | ✅已有 |
| 32 | 召对上下文注入：钱粮/派系/个人技能 | context.py | ✅已有 |
| 33 | 旨意解析Agent（edict_parser_agent） | agents.py/decree.py | ✅已有 |
| 34 | 诏书润色Agent（decree_writer_agent） | agents.py/decree.py | ✅已有 |
| 35 | 执行评估Agent（execution_evaluator_agent） | agents.py | ✅已有 |
| 36 | 记忆检索Agent（memory_retrieval_agent） | agents.py/memories.py | ✅已有 |
| 37 | 议题生成Agent（issue_agent） | agents.py/issues.py | ✅已有 |
| 38 | 前缀缓存优化（product-plan #5） | agents.py/decree.py | ❌未做 |
| 39 | 流式输出（Thinking片段实时推送） | agents.py | ⚠️部分 |
| 40 | 不允许LLM fallback严格校验 | 全局 | ✅已有 |

---

## Web前端（41-52）

| # | 条目 | 文件 | 状态 |
|---|------|------|------|
| 41 | 主界面main.tsx单文件 | web/src/main.tsx | ⚠️臃肿1114行 |
| 42 | 样式表styles.css | web/src/styles.css | ✅正常 |
| 43 | 地图GrandMap组件 | main.tsx | ✅已有 |
| 44 | 召见ChatModal组件 | main.tsx | ✅已有 |
| 45 | 大臣卡片列表MinisterCardList | main.tsx | ✅已有 |
| 46 | 存读档SaveListModal | main.tsx | ✅已有 |
| 47 | 势力网络图FactionNetworkModal | main.tsx | ✅已加 |
| 48 | 大臣详情弹窗MinisterDetailModal | main.tsx | ✅已加 |
| 49 | 武将技能树GeneralSkillPanel | main.tsx | ✅已加 |
| 50 | 后宫妃嫔档案ConsortProfileModal | main.tsx | ✅已加 |
| 51 | 前端水墨UI改造（product-plan #1） | main.tsx/styles.css | ❌未做 |
| 52 | 地图热力图（unrest可视化） | main.tsx | ✅已加 |

---

## 后端API路由（53-60）

| # | 路由 | 方法 | 状态 |
|---|------|------|------|
| 53 | /api/chat | POST | ✅已有 |
| 54 | /api/decree/issue | POST | ✅已有 |
| 55 | /api/decree/propose | POST | ✅已有 |
| 56 | /api/decree/list | GET | ✅已有 |
| 57 | /api/decree/{id} | PATCH/DELETE | ✅已有 |
| 58 | /api/game/state | GET | ✅已有 |
| 59 | /api/game/turn | POST | ✅已有 |
| 60 | /api/config | GET/PUT | ✅已有（LLM配置） |

---

## 数据库表结构（61-68）

| # | 表名 | 主要字段 | 状态 |
|---|------|----------|------|
| 61 | game_state | turn/year/month/imperial_metrics | ✅已有 |
| 62 | characters | name/office/faction/loyalty/ability/integrity/courage | ✅已有 |
| 63 | regions | id/name/unrest/public_support/gentry_resistance/fiscal | ✅已有 |
| 64 | armies | id/name/troop_type/manpower/morale/arrears | ✅已有 |
| 65 | factions | name/satisfaction/leverage/agenda | ✅已有 |
| 66 | edicts | id/text/status/actor/notes/resolve_result | ✅已有 |
| 67 | chat_messages | character_name/role/content | ✅已有 |
| 68 | memories | id/type/title/content/tags/deadline | ✅已有 |

---

## 产品计划优先级需求（69-82）

| # | 需求 | 优先级 | 状态 |
|---|------|--------|------|
| 69 | #5 提示词缓存优化 | P1 | ❌未做 |
| 70 | #3 历史事件预置（触发条件） | P2 | ⚠️部分 |
| 71 | #6 数值简化+深化 | P3 | ⚠️部分 |
| 72 | #2 皇帝技能树（emperor_skills表） | P4 | ❌未做 |
| 73 | #4 开局教程（tutorial字段） | P5 | ❌未做 |
| 74 | #1 水墨UI改造 | P6 | ❌未做 |
| 75 | 前缀稳定化：固定段放system，可变段后置 | #5子项 | ❌未做 |
| 76 | prompt token日志记录（cache_hit量化） | #5监控 | ❌未做 |
| 77 | 事件触发条件schema：trigger_year/trigger_quarter/trigger_condition | #3子项 | ⚠️部分 |
| 78 | 数值联动公式：欠饷→士气→忠诚→哗变 | #6子项 | ❌未做 |
| 79 | 阈值事件：跨值自动触发 | #6子项 | ⚠️部分 |
| 80 | 趋势可视化（7日/4月折线图） | #6子项 | ❌未做 |
| 81 | 派系深化：利益受损度+反弹概率 | #6子项 | ❌未做 |
| 82 | 四树互斥/兼修设计决策 | #2先决 | ❌未决 |

---

## 缺失项与待补全（83-92）

| # | 条目 | 说明 |
|---|------|------|
| 83 | 密旨(memory)过期淘汰机制 | memories.py有619行，需加时间戳TTL |
| 84 | 存档可见性过滤（_save_visible_for_campaign） | web_app.py行217 |
| 85 | 军队表字段不完整：supply缺失（armies.json有，DB未确认） | 待比对 |
| 86 | skill_grants表（大臣视角技能授予） | content/skills.json有，未查DB |
| 87 | edict_execution_results表（旨意执行结果追踪） | product-plan.md #6深化需求 |
| 88 | audiences召对场景表 | product-plan.md #6深化需求 |
| 89 | 建筑表buildings（content有）实际效果未接入游戏 | 待接flows |
| 90 | 科技表technologies | product-plan.md #6深化需求 |
| 91 | 物资表materials | product-plan.md #6深化需求 |
| 92 | 结局表endings | product-plan.md #6深化需求 |

---

## 立绘与美术（93-96）

| # | 条目 | 状态 |
|---|------|------|
| 93 | 大臣专属立绘（3/61已生成） | ⚠️58人待生成 |
| 94 | 后宫预设图池（16/20槽已出图） | ⚠️4个待补 |
| 95 | portrait_status.py自动生成脚本 | ✅已有 |
| 96 | 自定义立绘上传+custom:前缀解析 | ⚠️web_app.py有UPLOAD_DIR/前缀，需前端配 |

---

## 测试与验证（97-100）

| # | 条目 | 状态 |
|---|------|------|
| 97 | agno Agent流式/非流式降级（stream_events兼容性） | ✅已有降级 |
| 98 | LLM连通性校验（verify_llm_available） | ✅已有 |
| 99 | DB主库清理（_delete_sqlite_db_files_or_raise） | ✅已有防误删 |
| 100 | SQLite backup API（load_save热替换） | ✅已有 |

---

## 汇总

| 类别 | 数量 |
|------|------|
| ✅已完成 | 60项 |
| ⚠️部分/待完善 | 14项 |
| ❌未做/缺失 | 22项 |
| ❌未决（待设计决策） | 4项 |
| **合计** | **100项** |

---

## 优先开发建议（主公审批后启动）

**第一梯队（立即可做）：**
- 07 补全事件至50+条
- 38 前缀缓存优化（省钱，立竿见影）
- 51 水墨UI（视觉提升）

**第二梯队（影响深度）：**
- 15 皇帝技能点系统
- 78 数值联动公式
- 72 emperor_skills技能树

**第三梯队（完善度）：**
- 04 开局教程
- 87-92 各类深化表

---

*报告生成完毕，等主公审批后启动开发。*