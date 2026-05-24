"""数据类：游戏实体与状态容器。L0 叶子模块。

CourtContext 的 state/db 注解用字符串前向引用，避免 import db.py。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from ming_sim.constants import ECONOMY_ACCOUNTS


@dataclass
class ChatResult:
    action: str
    next_minister: str = ""
    refresh_ministers: List[str] = field(default_factory=list)


@dataclass
class LLMConfig:
    api_key: str
    base_url: str
    model: str


@dataclass
class Character:
    name: str
    office: str
    office_type: str
    faction: str
    aliases: List[str]
    personal_skills: List[str]
    loyalty: int
    ability: int
    integrity: int
    courage: int
    style: str
    birth_year: int = 0  # 历史生年（公历，0=未填）
    historical_death_year: int = 0  # 历史卒年（公历，0=未填）
    historical_death_month: int = 0  # 1-12，0=未指定
    current_office: str = ""  # 当前实职（方便追踪调任）
    imprisoned_cause: str = ""  # 下狱原因
    death_cause: str = ""  # 死因
    status: str = "active"  # active | dismissed | imprisoned | exiled | retired | dead
    east_bureau_bound: bool = False  # 入东厂后不可调出


@dataclass
class Event:
    id: str
    title: str
    kind: str
    summary: str
    urgency: int
    severity: int
    credibility: int
    interests: List[str]
    audiences: List[str]
    resolve_condition: str = ""
    fail_condition: str = ""
    trigger_year: int = 0   # 历史锚定触发年（公历，0=非历史锚定，靠 trigger_gate）
    trigger_month: int = 0  # 1-12，0=年内任意月
    precondition: str = ""  # 简短人话描述（如"民变>=60 + 陕西 unrest>=80"），目前只展示不强校验
    event_type: str = "situation"  # situation=转 bar issue；node=只播报不转 issue；ending=交结局判定
    trigger_gate: Dict[str, str] = field(default_factory=dict)  # seed 候选门槛：{metric: 比较式}，全满足才进候选


@dataclass
class Faction:
    name: str
    satisfaction: int
    leverage: int
    agenda: str


@dataclass
class SocialClass:
    """阶级人口：朝堂外的社会基本盘。
    region_id="" 表示全国汇总；非空表示该省切片（key 与 regions.id 对应）。
    机制：lev 高 + sat 低 → 易触发该省/该阶级骚乱事件，由 LLM 在推演中判定。
    """
    name: str            # 农民/士绅/官僚/军户/商人/匠户/宗藩
    region_id: str       # "" = 全国汇总；否则匹配 regions.id
    population: int      # 万人（粗估，全国汇总=各省合计的参考值）
    satisfaction: int    # 0-100
    leverage: int        # 0-100
    agenda: str          # 一句话诉求


@dataclass
class Region:
    id: str
    name: str
    kind: str
    population: int
    public_support: int
    unrest: int
    natural_disaster: str
    human_disaster: str
    registered_land: int
    hidden_land: int
    tax_per_turn: int
    grain_security: int
    gentry_resistance: int
    military_pressure: int
    status: str


@dataclass
class Army:
    id: str
    name: str
    station: str
    theater: str
    commander: str
    controller: str
    troop_type: str
    manpower: int
    maintenance_per_turn: int
    supply: int
    morale: int
    training: int
    equipment: int
    arrears: int
    mobility: int
    loyalty: int
    status: str


@dataclass
class Building:
    id: str
    region_id: str
    name: str
    category: str          # 财政/军事/民生/科技/交通/内廷
    level: int             # 规模 1-5
    condition: int         # 完好 0-100，产出折算系数
    maintenance: int       # 每月维护费 万两
    risk: int              # 0-100
    output_metric: str     # 国库/内库/军备/民心 或 "" 表示纯叙事
    output_amount: int     # 每月产出量
    status: str


@dataclass
class ExternalPower:
    id: str
    name: str
    leader: str
    stance: str
    leverage: int
    satisfaction: int
    military_strength: int
    cohesion: int
    supply: int
    agenda: str
    status: str
    last_action: str = "尚无新动"


@dataclass
class GameState:
    year: int = 1627
    period: int = 8
    turn: int = 1
    turn_phase: str = "summoning"  # summoning | reviewing | issued —— 见 session.TurnPhase
    metrics: Dict[str, int] = field(
        default_factory=lambda: {
            "国库": 320,
            "内库": 440,
            "民心": 46,
            "皇威": 58,
        }
    )
    log: List[str] = field(default_factory=list)

    def clamp(self) -> None:
        for key, value in list(self.metrics.items()):
            if key in ECONOMY_ACCOUNTS:
                self.metrics[key] = max(0, value)
            else:
                self.metrics[key] = max(0, min(100, value))

    def next_period(self) -> None:
        self.turn += 1
        self.period += 1
        if self.period > 12:
            self.period = 1
            self.year += 1


@dataclass
class CourtContext:
    state: "GameState"
    db: "object"  # GameDB；用 object 注解避免 import db.py 造成环
    previous_summary: str = ""


def period_label(year: int, month: int) -> str:
    return f"{year}年{month}月"


def monthly_amount(amount: int) -> int:
    return max(0, round(int(amount) / 3))
