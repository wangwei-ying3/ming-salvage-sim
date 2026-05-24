"""大臣 Agent 工具集：查询工具 + court tools（拟旨/退下/换人）。L5。"""

from __future__ import annotations

from ming_sim.constants import TURN_UNIT
from ming_sim.context import state_context
from ming_sim.models import Character, CourtContext
from ming_sim.skills import available_skill_ids, skill_template


def build_minister_tools(character: Character, context: CourtContext):
    skill_ids = set(available_skill_ids(character, context.db))

    def view_state() -> str:
        """查看当前大明核心国势数值（含派系/阶级/外部势力）。"""
        return (
            state_context(context.state)
            + "。派系：" + context.db.faction_report()
            + "。" + context.db.class_report()
            + "。外部：" + context.db.external_power_report()
        )

    def list_memorials() -> str:
        """查看当前在办的所有事项（issue）。"""
        rows = context.db.list_active_issues()
        if not rows:
            return f"本{TURN_UNIT}无在办事项。"
        lines = []
        for idx, row in enumerate(rows, 1):
            kind_tag = "系统" if row["kind"] == "situation" else "皇帝推动"
            lines.append(
                f"{idx}. #{row['id']}[{kind_tag}]{row['title']}"
                f"（bar {int(row['bar_value'])}/{row['bar_good_meaning']}，{row['stage_text']}）"
            )
        return "\n".join(lines)

    def inspect_memorial(slot: int) -> str:
        """查看某条在办事项的细节。slot 是事项编号（由 list_memorials 给出）。"""
        rows = context.db.list_active_issues()
        try:
            n = int(slot)
        except (ValueError, TypeError):
            return f"slot 必须是整数 1-{len(rows)}。"
        if n < 1 or n > len(rows):
            return f"slot 越界 {n}。本{TURN_UNIT}有 {len(rows)} 条在办事项。"
        row = rows[n - 1]
        return (
            f"#{row['id']} {row['title']}（bar {int(row['bar_value'])}，{row['bar_bad_meaning']}↔{row['bar_good_meaning']}）。"
            f"阶段：{row['stage_text']}。牵涉：{row['faction_hint'] or '—'}。"
            f"结案条件：{row['resolve_condition'] or '（未填）'}。失败条件：{row['fail_condition'] or '（未填）'}。"
        )

    def list_regions() -> str:
        f"""查看两京十三省最危险地区和账面{TURN_UNIT}税。"""
        return context.db.region_report(limit=6)

    def inspect_region(region_name: str) -> str:
        """查看某一地区人口、民心、动乱、天灾、人祸、田亩和税收。"""
        try:
            return context.db.region_detail(region_name)
        except ValueError as e:
            return f"未找到地区 '{region_name}'。可先调 list_regions 看地区 id/名称列表。错误：{e}"

    def list_armies() -> str:
        """查看大明主要军队的驻扎、维护费、补给、士气和欠饷警讯。"""
        return context.db.army_report(limit=6)

    def inspect_army(army_name: str) -> str:
        """查看某支军队驻扎地、兵种、人数、维护费、补给、士气、训练和欠饷。"""
        try:
            return context.db.army_detail(army_name)
        except ValueError as e:
            return f"未找到军队 '{army_name}'。可先调 list_armies 看军队 id/名称列表。错误：{e}"

    def list_external_powers() -> str:
        """查看后金、蒙古、朝鲜、流寇等外部势力状态。"""
        return context.db.external_power_report()

    def list_buildings() -> str:
        """查看全国在册建筑（火炮厂、矿厂、常平仓、边堡、织造局等）的等级、完好、维护费与产出。"""
        return context.db.buildings_report()

    def inspect_building(building_name: str) -> str:
        """查看某座建筑的类别、等级、完好、维护费、风险与产出。"""
        try:
            return context.db.building_detail(building_name)
        except ValueError as e:
            return f"未找到建筑 '{building_name}'。可先调 list_buildings 看建筑列表。错误：{e}"

    def estimate_resistance(slot: int) -> str:
        """估算某条在办事项若下旨推动的主要阻力。slot 是事项编号（由 list_memorials 给出）。"""
        rows = context.db.list_active_issues()
        try:
            n = int(slot)
        except (ValueError, TypeError):
            return f"slot 必须是整数 1-{len(rows)}。"
        if n < 1 or n > len(rows):
            return f"slot 越界 {n}。本{TURN_UNIT}有 {len(rows)} 条在办事项。"
        row = rows[n - 1]
        db = context.db
        faction_lev_avg = db.conn.execute("SELECT AVG(leverage) AS v FROM factions").fetchone()["v"] or 50
        resistance = int(row["severity"]) // 4 + int(faction_lev_avg) // 6
        tags = row["faction_hint"] or ""
        if any(t in tags for t in ("边", "军")):
            arrears_avg = db.conn.execute("SELECT AVG(arrears) AS v FROM armies").fetchone()["v"] or 0
            resistance += int(arrears_avg) // 12
        if any(t in tags for t in ("百姓", "地方", "士绅")):
            unrest_avg = db.conn.execute("SELECT AVG(unrest) AS v FROM regions").fetchone()["v"] or 0
            resistance += int(unrest_avg) // 12
        if any(t in tags for t in ("户部", "财")):
            resistance += max(0, 500 - context.state.metrics["国库"]) // 50
        if resistance >= 28:
            level = "高"
        elif resistance >= 18:
            level = "中"
        else:
            level = "低"
        return f"{row['title']}阻力{level}，主要牵涉：{tags or '—'}。估算阻力值：{resistance}。"

    def check_treasury() -> str:
        """查国库、内库、收支和欠账。"""
        return skill_template("check_treasury_prefix") + context.db.treasury_report(context.state)

    def audit_tax_arrears(target: str = "各省积欠") -> str:
        """清查积欠、估算可追收入库。"""
        return skill_template("audit_tax_arrears", target=target)

    def allocate_payroll(target: str = f"本{TURN_UNIT}急需钱粮处") -> str:
        """核算军饷调度。"""
        return skill_template("allocate_payroll", target=target)

    def propose_directive(decree_text: str) -> str:
        """当皇帝示意采纳（"准奏"、"善"、"就这么办"、"卿且去办" 等）时，把方才所议处置方案
        拟成一道圣旨草稿呈给皇帝审阅。皇帝确认后才正式入档。

        参数：
        - decree_text：完整圣旨正文，明确执行者、关键动作、期限、回奏要求。不带 Markdown。

        皇帝若仅在咨询、未示采纳，不要调此 tool。"""
        text = (decree_text or "").strip()
        if not text:
            return "拟旨失败：圣旨正文为空。"
        # 返回草稿标记，由 minister_chat / GameSession.chat 截获展示给皇帝确认，不在此入库。
        return f"__pending_directive__{text}"

    def dismiss_minister() -> str:
        """皇帝示意退下（如"退下""好，去办吧""朕知道了"等），调此 tool 结束本次召见。"""
        return "__dismiss__"

    def summon_minister(name: str) -> str:
        """皇帝要召见另一位大臣（如"传袁崇焕来""叫毕自严进来"），调此 tool 换人。name 填大臣姓名。"""
        return f"__summon__{name}"

    tools = [
        view_state,
        list_memorials,
        inspect_memorial,
        list_regions,
        inspect_region,
        list_armies,
        inspect_army,
        list_external_powers,
        list_buildings,
        inspect_building,
        estimate_resistance,
        propose_directive,
        dismiss_minister,
        summon_minister,
    ]
    if "check_treasury" in skill_ids:
        tools.append(check_treasury)
    if "allocate_payroll" in skill_ids:
        tools.extend([check_treasury, allocate_payroll])
    if "audit_tax_arrears" in skill_ids:
        tools.append(audit_tax_arrears)
    unique_tools = []
    seen_tool_names: set = set()
    for tool in tools:
        name = getattr(tool, "__name__", str(tool))
        if name in seen_tool_names:
            continue
        seen_tool_names.add(name)
        unique_tools.append(tool)
    return unique_tools


def build_simulator_tools(context: CourtContext):
    """月末推演日讲官的只读查询工具。

    与大臣 tool 的差异：纯只读、无 court tool（拟旨/退下/换人）、无 skill 闸。
    推演官借这些 tool 按需查实时盘面，让月末邸报有据，不靠 payload 静态快照瞎编。
    """
    def view_state() -> str:
        """查看当前大明核心国势数值（含派系/阶级/外部势力）。"""
        return (
            state_context(context.state)
            + "。派系：" + context.db.faction_report()
            + "。" + context.db.class_report()
            + "。外部：" + context.db.external_power_report()
        )

    def list_issues() -> str:
        """查看当前在办的所有事项（issue）及进度。"""
        rows = context.db.list_active_issues()
        if not rows:
            return f"本{TURN_UNIT}无在办事项。"
        lines = []
        for row in rows:
            kind_tag = "系统" if row["kind"] == "situation" else "皇帝推动"
            lines.append(
                f"#{row['id']}[{kind_tag}]{row['title']}"
                f"（bar {int(row['bar_value'])}/{row['bar_good_meaning']}，{row['stage_text']}）"
            )
        return "\n".join(lines)

    def inspect_issue(issue_id: int) -> str:
        """查看某条在办事项细节。issue_id 是事项编号（由 list_issues 给出，带 # 的数字）。"""
        rows = context.db.list_active_issues()
        try:
            n = int(issue_id)
        except (ValueError, TypeError):
            return "issue_id 必须是整数。"
        row = next((r for r in rows if int(r["id"]) == n), None)
        if row is None:
            return f"未找到在办事项 #{n}。可先调 list_issues 看清单。"
        return (
            f"#{row['id']} {row['title']}（bar {int(row['bar_value'])}，"
            f"{row['bar_bad_meaning']}↔{row['bar_good_meaning']}）。"
            f"阶段：{row['stage_text']}。牵涉：{row['faction_hint'] or '—'}。"
            f"结案条件：{row['resolve_condition'] or '（未填）'}。失败条件：{row['fail_condition'] or '（未填）'}。"
        )

    def list_regions() -> str:
        f"""查看两京十三省最危险地区和账面{TURN_UNIT}税。"""
        return context.db.region_report(limit=8)

    def inspect_region(region_name: str) -> str:
        """查看某一地区人口、民心、动乱、天灾、人祸、田亩和税收。"""
        try:
            return context.db.region_detail(region_name)
        except ValueError as e:
            return f"未找到地区 '{region_name}'。可先调 list_regions 看地区列表。错误：{e}"

    def list_armies() -> str:
        """查看大明主要军队的驻扎、维护费、补给、士气和欠饷警讯。"""
        return context.db.army_report(limit=8)

    def inspect_army(army_name: str) -> str:
        """查看某支军队驻扎地、兵种、人数、维护费、补给、士气、训练和欠饷。"""
        try:
            return context.db.army_detail(army_name)
        except ValueError as e:
            return f"未找到军队 '{army_name}'。可先调 list_armies 看军队列表。错误：{e}"

    def list_external_powers() -> str:
        """查看后金、蒙古、朝鲜、流寇等外部势力状态。"""
        return context.db.external_power_report()

    def check_treasury() -> str:
        """查国库、内库、收支和欠账明细。"""
        return context.db.treasury_report(context.state)

    return [
        view_state,
        list_issues,
        inspect_issue,
        list_regions,
        inspect_region,
        list_armies,
        inspect_army,
        list_external_powers,
        check_treasury,
    ]
