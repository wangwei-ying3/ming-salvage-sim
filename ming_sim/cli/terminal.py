"""CLI 终端层：input()/print() 驱动，调 GameSession 跑回合。L9。

play_turn 状态机搬入此处；GameSession 持游戏状态，terminal 只做 I/O。
拟旨 draft 待确认：大臣 propose_directive → session 返回 pending 草案
→ 终端打印草稿 → 皇帝 可/准→confirm，驳→reject。
"""

from __future__ import annotations

import re
from typing import List, Optional

from ming_sim.constants import COURT_BREAK_COMMANDS, EXIT_COMMANDS, TURN_UNIT
from ming_sim.assets import wrap
from ming_sim.context import match_minister_from_text
from ming_sim.exceptions import ExitGame
from ming_sim.models import Character, GameState
from ming_sim.session import GameSession, TurnPhase
from ming_sim.skills import print_all_skill_cards, print_skill_card, skill_display_name

_STATUS_LABEL = {
    "active": "在朝",
    "dismissed": "已罢官",
    "imprisoned": "下狱",
    "exiled": "流戍",
    "retired": "致仕",
    "dead": "亡",
}

# 皇帝当场对拟旨草稿的回应
_CONFIRM_WORDS = {"", "可", "准", "准奏", "yes", "y", "确认", "入档"}
_REJECT_WORDS = {"驳", "不准", "驳回", "no", "n"}


def _print_header(session: GameSession) -> None:
    from ming_sim.report import print_header
    print_header(session.state, session.db)


def choose_minister(session: GameSession) -> Optional[Character]:
    """列大臣，皇帝选一位。返回 None 表示退朝去审阅诏书。"""
    characters = session.content.characters
    names = list(characters.keys())
    print("\n可召见大臣：")
    for idx, name in enumerate(names, 1):
        c = characters[name]
        status, _ = session.db.get_character_status(name)
        tag = "" if status == "active" else f"  [{_STATUS_LABEL.get(status, status)}]"
        print(f"{idx}. {c.name}（{c.office}，{c.faction}）{tag}")
    while True:
        raw = input("召见谁？输入编号或姓名，skills 查看技能卡，quit 退朝审阅诏书，exit 退出游戏：").strip()
        if not raw:
            print("请输入编号或姓名。")
            continue
        lowered = raw.lower()
        if lowered in EXIT_COMMANDS:
            raise ExitGame
        if lowered in COURT_BREAK_COMMANDS:
            return None
        if lowered in {"skills", "skill", "技能", "技能卡", "查看技能"}:
            print_all_skill_cards(session.db)
            continue
        candidate: Optional[Character] = None
        if raw.isdigit() and 1 <= int(raw) <= len(names):
            candidate = characters[names[int(raw) - 1]]
        elif raw in characters:
            candidate = characters[raw]
        else:
            matches = [
                c for c in characters.values()
                if raw in c.name or raw in c.office or raw in c.office_type
                or raw in c.faction or raw in c.aliases
                or any(raw in skill for skill in c.personal_skills)
            ]
            if len(matches) == 1:
                candidate = matches[0]
            elif len(matches) > 1:
                print("这句话能对应多位大臣，请再说具体一点，或直接输编号。")
                continue
        if candidate is None:
            print("请输入有效编号或姓名。")
            continue
        status, reason = session.db.get_character_status(candidate.name)
        if status != "active":
            tag = _STATUS_LABEL.get(status, status)
            print(f"{candidate.name}已{tag}，无法召见。{reason}")
            continue
        return candidate


def _skill_ids_from_text(session: GameSession, text: str) -> List[str]:
    matched: List[str] = []
    for keyword, skill_ids in session.content.grant_keywords.items():
        if keyword in text:
            matched.extend(skill_ids)
    for skill_id, definition in session.content.skill_catalog.items():
        name = str(definition.get("name", ""))
        if skill_id in text or (name and name in text):
            matched.append(skill_id)
    unique: List[str] = []
    seen: set = set()
    for skill_id in matched:
        if skill_id not in seen:
            seen.add(skill_id)
            unique.append(skill_id)
    return unique


def _handle_court_command(
    session: GameSession, text: str, current: Character
) -> Optional[str]:
    """CLI 控制指令识别。返回：'dismiss' | 'court_break' | 'summon:<name>' |
    'handled'（技能等已处理）| None（非控制指令，交给 chat）。"""
    raw = text.strip()
    lowered = raw.lower()
    if lowered in EXIT_COMMANDS:
        raise ExitGame
    if lowered in COURT_BREAK_COMMANDS or raw in {"退朝", "下朝"}:
        return "court_break"

    # 技能卡查看
    if (lowered in {"skills", "skill", "技能", "技能卡", "查看技能", "查看skill"} or "技能" in raw) \
            and not any(w in raw for w in ("授权", "授予", "交给", "收回", "撤销", "取消授权", "命", "令", "着")):
        target = match_minister_from_text(raw, None) or current
        print_skill_card(target, session.db)
        print()
        return "handled"

    # 收回授权
    if "授权" in raw and any(w in raw for w in ("收回", "撤销", "取消", "停用", "夺回")):
        target = match_minister_from_text(raw, None) or current
        revoked = [
            skill_display_name(sid)
            for sid in _skill_ids_from_text(session, raw)
            if session.db.revoke_skill(target.name, sid)
        ]
        if revoked:
            print(f"已收回{target.name}：{'、'.join(revoked)}。\n")
            session.registry.refresh(target.name)
        else:
            print(f"{target.name}没有可收回的相关授权，或未识别要收回的 skill。\n")
        return "handled"

    # 退下（短句正则，不误伤长对话）
    if re.fullmatch(
        r"\s*(退下|退了|跪安|下去|done|dismiss|让[他其]退下|叫[他其]退下|让此人退下|叫此人退下)\s*",
        raw, re.I,
    ):
        return "dismiss"

    # 召见（传/召/宣/叫 开头）
    summon_m = re.match(r"^[传召宣叫](.{1,8?})[来到]$|^[传召宣叫](.{1,8?})(?:入殿|上殿|面圣)", raw)
    if summon_m:
        name_fragment = summon_m.group(1) or summon_m.group(2)
        target = match_minister_from_text(name_fragment, current)
        if target is not None:
            return f"summon:{target.name}"

    # 授予授权
    if any(w in raw for w in ("授权", "交给", "授予")):
        target = match_minister_from_text(raw, None) or current
        granted = [
            skill_display_name(sid)
            for sid in _skill_ids_from_text(session, raw)
            if session.db.grant_skill(session.state, target.name, sid)
        ]
        if granted:
            print(f"已授权{target.name}：{'、'.join(granted)}。\n")
            session.registry.refresh(target.name)
        else:
            print(f"{target.name}已有相关授权，或未识别要授权的 skill。\n")
        return "handled"

    return None


def _confirm_pending_directive(session: GameSession, draft, minister_name: str) -> None:
    """大臣拟旨后当场让皇帝核定：可/准→confirm，驳→reject，其它→原地重问。"""
    print(f"\n{minister_name}拟旨如下：\n")
    print("─" * 50)
    print(draft.text)
    print("─" * 50)
    while True:
        confirm_raw = input(
            "\n陛下：确认入档（回车/可/准）| 驳回（驳/不准）："
        ).strip()
        low = confirm_raw.lower()
        if low in _CONFIRM_WORDS:
            session.confirm_directive(draft.id)
            print(f"已入本{TURN_UNIT}草案 #{draft.id}。\n")
            return
        if low in _REJECT_WORDS:
            session.reject_directive(draft.id)
            print("驳回。卿重新拟议。\n")
            return
        print("未识别。请输 可/准 入档，或 驳/不准 驳回。\n")


def minister_chat(session: GameSession, character: Character) -> str:
    """与一位大臣对话。返回 'dismiss' | 'court_break' | 'summon:<name>'。"""
    other = next((n for n in session.content.characters if n != character.name), character.name)
    print(f"\n{character.name}入殿。可持续问话；done/退下 退下，“传{other}来”换人，quit 退朝审阅诏书，exit 退出游戏。")
    print(f"提示：陛下示意采纳后（如“准奏”），大臣会拟旨呈陛下核定。\n")
    while True:
        question = input("朕问：").strip()
        if not question:
            print("可继续问话；若要让其退下，请输入 done。")
            continue
        cmd = _handle_court_command(session, question, character)
        if cmd == "handled":
            continue
        if cmd == "dismiss":
            print(f"{character.name}退下。\n")
            return "dismiss"
        if cmd == "court_break":
            print(f"{character.name}退下。\n")
            return "court_break"
        if cmd and cmd.startswith("summon:"):
            target_name = cmd.split(":", 1)[1]
            print(f"{character.name}退下。\n传{target_name}入殿。\n")
            return cmd
        # 非控制指令 → 与 agent 对话
        result = session.chat(character.name, question)
        print(wrap(result.answer))
        print()
        if result.proposed_directive is not None:
            _confirm_pending_directive(session, result.proposed_directive, character.name)
        if result.court_action == "dismiss":
            print(f"{character.name}退下。\n")
            return "dismiss"
        if result.court_action == "summon" and result.next_minister:
            print(f"{character.name}退下。\n传{result.next_minister}入殿。\n")
            return f"summon:{result.next_minister}"


def review_directives(session: GameSession) -> str:
    """诏书草案审阅界面。返回 'issue' | 'back' | 'skip'。"""
    session.enter_review()
    while True:
        directives = session.list_directives(include_pending=True)
        pending = [d for d in directives if d.status == "pending"]
        drafts = [d for d in directives if d.status == "draft"]
        print(f"\n本{TURN_UNIT}诏书草案：")
        if pending:
            print(f"  ⚠ {len(pending)} 道大臣拟旨待核定（confirm N 准 / reject N 驳）：")
            for d in pending:
                print(f"  [待核定] #{d.id}  {wrap(d.text)}")
        if drafts:
            for idx, d in enumerate(drafts, 1):
                print(f"{idx}. #{d.id}")
                print(f"   {wrap(d.text)}")
        elif not pending:
            print("（暂无指令。back 继续召见，或 add 新增。）")
        print("\n操作：issue 颁布 | back 继续召见 | add 新增 | edit N 改 | del N 删 | "
              "confirm N 准拟旨 | reject N 驳拟旨 | skills 技能卡 | exit 退出")
        raw = input("诏书草案> ").strip()
        if not raw:
            continue
        lowered = raw.lower()
        if lowered in EXIT_COMMANDS:
            raise ExitGame
        if lowered in COURT_BREAK_COMMANDS:
            if drafts or pending:
                print(f"本{TURN_UNIT}尚有草案/待核定。请 issue 颁布，或 del/reject 清空后退朝。")
                continue
            return "skip"
        if lowered in {"back", "b", "返回", "继续召见"}:
            session.back_to_summoning()
            return "back"
        if lowered in {"skills", "skill", "技能", "技能卡", "查看技能"}:
            print_all_skill_cards(session.db)
            continue
        if lowered in {"issue", "颁布", "颁布诏书", "发布", "拟诏"}:
            if pending:
                print(f"尚有 {len(pending)} 道大臣拟旨待核定（confirm/reject），不能颁诏。")
                continue
            if not drafts:
                print("暂无指令，不能颁布空诏书。add 新增，或 back 继续召见。")
                continue
            try:
                decree = session.write_decree()
            except ValueError as e:
                print(f"拟诏失败：{e}")
                continue
            print("\n最终诏书：")
            print(decree)
            confirm = input("确认颁布？输入 yes/颁布 确认，其他返回修改：").strip().lower()
            if confirm in {"yes", "y", "颁布", "确认"}:
                return "issue"
            continue
        if lowered == "add" or raw == "新增":
            text = input("指令内容：").strip()
            if text:
                dv = session.add_directive(text)
                print(f"已新增草案 #{dv.id}。")
            else:
                print("指令为空，已取消。")
            continue
        parts = raw.split(maxsplit=1)
        verb = parts[0].lower()
        if len(parts) == 2 and parts[1].lstrip("#").isdigit():
            target_id = int(parts[1].lstrip("#"))
            if verb in {"confirm", "准"}:
                if any(d.id == target_id for d in pending):
                    session.confirm_directive(target_id)
                    print(f"已核定 #{target_id}，入颁诏候选。")
                else:
                    print("没有这条待核定拟旨。")
                continue
            if verb in {"reject", "驳"}:
                if any(d.id == target_id for d in pending):
                    session.reject_directive(target_id)
                    print(f"已驳回 #{target_id}。")
                else:
                    print("没有这条待核定拟旨。")
                continue
            if verb in {"edit", "改", "修改"}:
                if not any(d.id == target_id for d in drafts):
                    print("没有这条草案。")
                    continue
                new_text = input("新的指令内容：").strip()
                if new_text:
                    session.update_directive(target_id, new_text)
                    print("已修改。")
                continue
            if verb in {"del", "delete", "删", "删除"}:
                if any(d.id == target_id for d in drafts):
                    session.delete_directive(target_id)
                    print("已删除。")
                elif any(d.id == target_id for d in pending):
                    # pending 草案删掉 = 驳回大臣拟旨
                    session.reject_directive(target_id)
                    print(f"已驳回 #{target_id}（待核定拟旨）。")
                else:
                    print("没有这条草案。")
                continue
        print("未识别操作。")


def play_turn(session: GameSession) -> None:
    """一回合 CLI 驱动：召见 → 审阅 → 颁诏推演。"""
    snap = session.begin_turn()
    _print_header(session)
    if session.previous_summary:
        print(session.previous_summary)
        print()
    if snap.deaths_this_turn:
        names = "、".join(f"{d['name']}（{d['office']}）" for d in snap.deaths_this_turn)
        print(f"【讣闻】本{TURN_UNIT}卒：{names}\n")
    from ming_sim.issues import show_active_issues
    show_active_issues(session.db)

    pending_character: Optional[Character] = None
    while True:
        if session.current_phase() == TurnPhase.SUMMONING:
            character = pending_character or choose_minister(session)
            pending_character = None
            if character is None:
                action = review_directives(session)
            else:
                chat_action = minister_chat(session, character)
                if chat_action == "dismiss":
                    continue
                if chat_action.startswith("summon:"):
                    pending_character = session.content.characters[chat_action.split(":", 1)[1]]
                    continue
                # court_break 或对话结束 → 审阅
                action = review_directives(session)
        else:
            action = review_directives(session)

        if action == "back":
            continue
        if action == "skip":
            session.advance_without_decree()
            return
        if action == "issue":
            report = session.resolve_turn()
            print(report)
            session.end_turn()
            return


def run_cli(
    base_url: str,
    model: str,
    db_path: str,
    api_key: str = "",
    start_ym: str = "",
) -> None:
    """CLI 主循环：建 GameSession，逐回合 play_turn。"""
    from ming_sim.llm_config import load_llm_config
    from ming_sim.exceptions import LLMUnavailable, LLMContractError
    from ming_sim.token_stats import print_token_summary

    session: Optional[GameSession] = None
    try:
        llm_config = load_llm_config(base_url, model, api_key=api_key)
        import os
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        session = GameSession(db_path, llm_config, start_ym=start_ym)
        print("《明末力挽狂澜》文字 MVP")
        print(f"你是刚刚登基的崇祯。每回合一个{TURN_UNIT}：看奏报、召见大臣、下圣旨、听回奏。")
        print(f"手动玩法：quit/退朝 = 结束本{TURN_UNIT}进入下一{TURN_UNIT}；exit/退出游戏 = 退出程序。")
        print(f"当前 LLM：{model} @ {base_url}")
        print(f"数据库：{db_path}\n")
        while True:
            play_turn(session)
            raw = input(f"\n按回车继续下一{TURN_UNIT}，或输入 exit 退出游戏：").strip()
            if raw.lower() in EXIT_COMMANDS:
                break
    except ExitGame:
        print("\n退出游戏。")
    except LLMUnavailable as error:
        print(f"\n{error}")
    except LLMContractError as error:
        print(f"\n程序中止：{error}")
    except KeyboardInterrupt:
        print("\n退出游戏。")
    finally:
        if session is not None:
            session.close()
        print_token_summary()
