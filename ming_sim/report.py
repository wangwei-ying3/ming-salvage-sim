"""回合展示与变更报告纯函数（返回 str / 打印）。L7。"""

from __future__ import annotations

from typing import Dict, List, Optional

from ming_sim.assets import format_money, format_money_delta, wrap
from ming_sim.constants import ECONOMY_ACCOUNTS, SCORE_METRICS, TURN_UNIT
from ming_sim.context import character_from_name, format_metric_delta
from ming_sim.db import GameDB
from ming_sim.models import Event, GameState, period_label


def metric_bar(value: int) -> str:
    filled = value // 10
    return "█" * filled + "░" * (10 - filled)


def print_header(state: GameState, db: Optional[GameDB] = None) -> None:
    print("\n" + "=" * 88)
    print(f"崇祯重生 MVP | {period_label(state.year, state.period)} | 第 {state.turn} 回合")
    print("=" * 88)
    for key in ECONOMY_ACCOUNTS:
        print(f"{key:>4}: {format_money(state.metrics[key])}")
    for key in SCORE_METRICS:
        value = state.metrics[key]
        print(f"{key:>4}: {value:>3}/100 {metric_bar(value)}")
    if db is not None:
        print()
        print(wrap(db.region_report(limit=3)))
        print(wrap(db.army_report(limit=3)))
    print()


def format_region_changes(changes: List[Dict[str, object]]) -> str:
    if not changes:
        return f"本{TURN_UNIT}未见明确地区盘面变化。"
    parts = []
    for change in changes:
        delta = change["delta"]
        if delta is None:
            parts.append(f"{change['region']}{change['label']}改为{change['new']}（{change['reason']}）")
        else:
            sign = "+" if int(delta) > 0 else ""
            parts.append(f"{change['region']}{change['label']}{sign}{int(delta)}（{change['reason']}）")
    return "；".join(parts) + "。"


def format_army_changes(changes: List[Dict[str, object]]) -> str:
    if not changes:
        return f"本{TURN_UNIT}未见明确军队盘面变化。"
    parts = []
    for change in changes:
        delta = change["delta"]
        field = str(change["field"])
        if delta is None:
            parts.append(f"{change['army']}{change['label']}改为{change['new']}（{change['reason']}）")
        elif field == "manpower":
            sign = "+" if int(delta) > 0 else ""
            parts.append(f"{change['army']}{change['label']}{sign}{int(delta)}人（{change['reason']}）")
        elif field == "maintenance_per_turn":
            parts.append(f"{change['army']}{change['label']}{format_money_delta(int(delta))}（{change['reason']}）")
        else:
            sign = "+" if int(delta) > 0 else ""
            parts.append(f"{change['army']}{change['label']}{sign}{int(delta)}（{change['reason']}）")
    return "；".join(parts) + "。"


def format_external_power_changes(changes: List[Dict[str, object]]) -> str:
    if not changes:
        return f"本{TURN_UNIT}未见明确外部势力盘面变化。"
    parts = []
    for change in changes:
        delta = change["delta"]
        if delta is None:
            parts.append(f"{change['power']}{change['label']}改为{change['new']}（{change['reason']}）")
        else:
            sign = "+" if int(delta) > 0 else ""
            parts.append(f"{change['power']}{change['label']}{sign}{int(delta)}（{change['reason']}）")
    return "；".join(parts) + "。"


def metric_delta(before: Dict[str, int], after: Dict[str, int]) -> Dict[str, int]:
    keys = list(before.keys())
    for key in after:
        if key not in before:
            keys.append(key)
    return {key: after.get(key, 0) - before.get(key, 0) for key in keys if after.get(key, 0) != before.get(key, 0)}


def status_delta(before: Dict[str, int], after: Dict[str, int]) -> str:
    return format_metric_delta(metric_delta(before, after)).replace("数值变化：", "")


def status_delta_from_delta(delta: Dict[str, int]) -> str:
    return format_metric_delta(delta).replace("数值变化：", "")


def build_period_report(
    event: Event,
    edict: Dict[str, object],
    evaluation: Dict[str, object],
    delta: Dict[str, int],
    economy_moves: List[Dict[str, object]],
    region_changes: List[Dict[str, object]],
    army_changes: List[Dict[str, object]],
) -> str:
    executor = character_from_name(edict["执行者"])
    lines = [
        f"\n{TURN_UNIT}末总结奏章：",
        f"奏事：{event.title}",
        f"主办：{executor.name}（{executor.office}）",
        f"奉旨结果：{evaluation['result_level']}。{evaluation['public_report']}",
    ]
    if economy_moves:
        money = "；".join(
            f"{move['account']}{format_money_delta(int(move['delta']))}（{move['category']}）"
            for move in economy_moves
        )
        lines.append(f"钱粮流水：{money}。")
    else:
        lines.append(f"钱粮流水：本{TURN_UNIT}未形成新入账或出账。")
    lines.append("地区变化：" + format_region_changes(region_changes))
    lines.append("军队变化：" + format_army_changes(army_changes))
    lines.append("数值变化：" + status_delta_from_delta(delta))
    lines.append(f"局势影响：{evaluation['impact']}")
    if evaluation["followup_event"]:
        lines.append(f"后续奏报：{evaluation['followup_event']}")
    return "\n".join(lines)
