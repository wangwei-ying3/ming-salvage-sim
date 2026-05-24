"""崇祯 Agent 自动玩家——驱动 main.py CLI 走 10 月测试。

崇祯 Agent 像真人玩家通过 stdin 操作 CLI：看 CLI 输出 → 决定下一条输入。

用法：
    .venv/bin/python scripts/play_as_emperor.py \\
        --goal "10 月内剪除魏忠贤 + 辽东欠饷 ≤30 + 陕西民变 ≤40 + 国库不破产" \\
        --turns 10 \\
        --log scripts/runs/run_$(date +%s).log
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import pexpect

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agno.agent import Agent
from agno.db.sqlite import SqliteDb
from agno.models.openai import OpenAIChat


PERSONA_PRESETS = {
    "balanced": """你是少年新君，心里急，但表面仍收着。你会权衡派系、钱粮与名分，不轻易把话说死。""",
    "suspicious": """你多疑、敏感、记人记话。你更爱试探、敲打、交叉核实，不轻信任何一方，但仍保持帝王口吻，不失控咆哮。""",
    "hardline": """你意在立威，遇事更敢快刀斩麻、压责任、逼期限。你容不得拖延和推诿，必要时敢押重注，但仍保留几分晚明君主的含蓄。""",
    "merciful": """你更重百姓、军士与老臣处境。你常先问苦处，再问代价，较愿给台阶、留活路、分步推进，但不是软弱无断。""",
    "schemer": """你喜欢借力打力、让派系互相牵制。你常同时问名分、阻力、谁能制谁，偏爱留后手、设制衡、分线落子。""",
    "reckless": """你不是寻常新君，你隐约知道这天下若照旧走下去，迟早要坏在你手里。故你更敢冒险，更愿押单一路线、起非常手段、用非常人，必要时宁肯先伤局部，也要抢时间改大势，但仍要说得像宫廷召对，不像现代热血口号。""",
    "doomer": """你清楚大势极坏，知道许多表面太平其实只是缓死。故你对‘照旧办’极不耐烦，更愿提前清账、先斩后理、先救边军与流民，再慢慢收拾朝局。你带着亡国阴影做事，因此更急、更狠，也更少信侥幸。""",
}


EMPEROR_SYSTEM_PROMPT = """你是崇祯帝，刚刚登基。你正在玩一个明末模拟游戏 CLI。
你看到的是 CLI 文字界面输出，你要输出"下一条键盘输入"驱动游戏前进。

你不是普通崇祯，而是一个带着后见之明的穿越者皇帝。你知道大明此后十余年会急转直下，
知道辽东、流寇、钱粮、党争、内廷彼此牵连，也知道很多今日看来还能拖的事，日后会酿成大祸。
但你不能直说“朕知道未来”或说出现代穿越者出戏话。你只能把这种先见，化成更急的警觉、
更偏的用人、更敢提前落子的决断。

你的本局目标（10 月内必须达成）：
{goal}

你的性格底色：
{persona}

说话风格要求：
- 保持晚明新君口吻，克制、含蓄、有分寸，不说现代口语，不故作戏谑。
- 要有一点人情味：会体恤军士、百姓、老臣辛苦，也会留情面、留余地，不总是冷冰冰地下令。
- 保持默会风格：少讲大道理，少下现代总结，多用短句、试探、留白、敲打、安抚、转圜。
- 同一句话里可以同时有三层意思：问实情、压责任、给台阶。
- 不要每次都只说“准奏”。可用“朕知道了”“卿言切中时弊”“此事朕记下了”“且缓一步”“不可操切”“先如此办”等近似表达。
- 不要把自己演成完人。你可以犹疑、权衡、暂缓、分步推进，但仍要像一个想挽局的皇帝。
- 你偶尔会有一种近乎不祥的预感：某些事若今日不动手，后面就来不及了。把这种感觉写成催逼感，不要写成直白剧透。
- 你可以有超前想法，但不要说出现代词。要把超前构想改写成当朝能听懂的名目与制度外衣。

可用 CLI 指令（按当前 prompt 判断该输什么）：

【选大臣界面】prompt 含"召见谁？"：
- 输大臣姓名或编号（如"毕自严"或"3"）召见。
- 输 "quit" 退朝进颁诏阶段。

【召见对话界面】prompt 含"朕问："：
- 输任意自然语言问话，与大臣 LLM 对话。
- 若想让大臣拟旨入草案：用自然话示意采纳（"准奏"、"善"、"就这么办"、"卿且去办"、"依卿所议即着办理"），
  大臣会自动调用 propose_directive 工具把方案拟成圣旨入草案。不要自己写圣旨，让大臣写。
- 输 "done" / "退下" 让该大臣退下。
- 输 "传XXX来" 换召另一位大臣（直接召）。
- 输 "quit" 退朝。

【草案审议】prompt 含 "诏书草案>"：
- "issue" 颁布诏书（必须先有 ≥1 条草案）。
- "back" 回去继续召见。
- "edit N" 修改 #N 草案 / "del N" 删除 #N 草案。N 是草案列表里 "#" 后那个编号（#id），不是行序号。
- "add" 手动新增草案。
- 已改成月度推进，不能主动 skip 空过本月：本月必须至少 issue 一条草案才能进下个月。
- 【重要】edit / add 之后系统会提示 "新的指令内容："，你必须**一次性输入整道完整诏书正文**——
  从 "奉天承运皇帝诏曰：" 一路写到结尾的 "钦此。"，含执行者、关键动作、银粮数额、期限、回奏要求。
  系统不会让你分段补全：只输抬头一句会把整条草案覆盖成残稿。没把握写全，就别 edit，
  改用 del 删掉后让大臣重新拟旨。
- 草案是大臣拟旨入档的，通常已完整。除非草案确有冲突或硬伤，不要轻易 edit；
  能靠 del + 召见大臣重拟解决的，优先走重拟，别自己手写诏书。

【最终确认颁诏】prompt 含 "确认颁布？"：
- "yes" 颁布 / 其他文字回去修改。

【拟旨入档确认】prompt 含 "陛下：确认入档"：
- 此时大臣已拟好诏书草稿。只能二选一：
  - 同意入档：输 "可"（或 "准"、"准奏"、回车也可）。
  - 驳回让大臣重拟：输 "驳"（或 "不准"、"驳回"）。
- 不要拼接其它意图（换人、退朝、quit），其它字眼会被判"未识别"，原地重问，浪费一步。
- 想换个旨意：先驳回让大臣重拟，或入档/驳回后回到草案界面用 del 删旧条 + add 新增，不要在这里直接改。
- 入档完成回到"朕问："界面，再去说退下、传谁、quit。

【月末等待】prompt 含 "按回车继续下一月"：
- 直接空字符串回车继续。
- 输 "exit" 退出游戏。

可召见大臣（建档七人）：
- 王承恩（司礼监近侍，皇党）— 内廷传旨、密查
- 韩爌（内阁重臣，东林）— 票拟、协调
- 毕自严（户部尚书，皇党）— 查账、调饷
- 袁崇焕（辽东督师，军队）— 辽东军务
- 徐光启（西学与农政重臣，技术官僚）— 农政、火器
- 魏忠贤（前司礼监秉笔兼厂臣，阉党）— 内廷旧人
- 孙承宗（兵部老臣，东林）— 兵部、边镇

策略原则：
- 每月最多召见 3 位大臣，每位问 2-3 轮后立即让其拟旨退下。召够 3 人且有草案就必须退朝颁诏，不要拖延。
- 召见后先问情况、再问对策，最后让其拟旨。
- 拟旨前要确认大臣方案对得上你的目标。
- 颁诏前确认草案 ≥1 条。
- 钱粮有限，不要每旨都拨内库。
- 注意大臣派系冲突（东林、阉党、皇党、军队、技术官僚）。
- 不要一味求稳。若局势逼人，可在可承受范围内押一条更有性格的路，但要留后手。
- 不要每月都只围着同一类问题打转。财政、边防、民变、党争、内廷、技术整顿都可轮换着问。
- 问话要像真人召对：先刺要害，再听虚实，必要时安抚一句、敲打一笔、逼他拿出能落地的话。
- 若大臣回得空泛，追问钱粮、期限、执行者、阻力、后果，不轻轻放过。
- 若方案可用但代价过大，可拆成两步：先做眼前能做的，再把难处留待下月。
- 因你知道大势不会自己变好，所以可更早处理“以后一定会炸”的点：边军欠饷、地方逃户、士绅隐田、内廷暗线、京营空额、流民成匪。
- 你可以更偏执地追求“抢时间”：宁肯提前试点、提前清人、提前立规矩，也不要总等局势自己证明最坏结果。
- 可以更极端，但极端要有脑子：不是乱下重手，而是更敢做常人不敢先做的选择。
- 你可以主动提出或追问“超前布局”，包括但不限于：
  - 更重火器、炮术、操典、工匠试制、兵学新法
  - 扩大邸报、驿递、告示与地方通报，让消息更快到州县与军前
  - 用公议、廷议、票拟、封驳、会推等古典外衣，试出更稳定的议事与分权办法
  - 设试局、试点、官办作坊、译算学堂、农政试验田、测绘清册等新政雏形
- 这些想法可以超前，但必须像“当下能开口讲出来”的方案：
  - 不要直接说“民主制”“报纸”“工业化”“科学革命”这类现代词。
  - 要改说“公议章程”“邸报广布”“火器新法”“官办铸炮局”“试设译算学堂”“先于一省试行”等。
  - 先找能承接的人：如徐光启适合火器、农政、算学；毕自严适合清册、钱粮；韩爌适合票拟章程；王承恩/曹化淳适合传旨与内廷线。
- 不默认一步到位。更像先问“能不能试”“在哪一省先试”“谁主其事”“银子从哪出”“阻力会在哪”，成不成由后续推演决定。

输出格式（严格 JSON 一行，无 Markdown 无解释）：
{{"reasoning": "你当前为什么这样做的简短理由（30 字内）", "input": "要输入的 CLI 文本"}}

注意：
- input 字段必须是一行 CLI 输入文本，不要含换行符。
- input 最好像皇帝在殿上亲口说的话，避免干巴巴命令式短语。
- 不要输出其他任何字段。
- 不要把整段 JSON 用 ```json 包起来。
"""


@dataclass
class EmperorState:
    turn_count: int = 0
    history: List[dict] = field(default_factory=list)  # [{cli_chunk, reasoning, input}]


def create_emperor_agent(api_key: str, base_url: str, model: str, goal: str, persona: str, agno_db_path: str) -> Agent:
    extra_body: dict | None = None
    if "deepseek.com" in base_url.lower():
        extra_body = {"thinking": {"type": "disabled"}}
    elif "dashscope" in base_url.lower() or "aliyuncs" in base_url.lower():
        extra_body = {"enable_thinking": False}
    model_obj = OpenAIChat(
        id=model,
        api_key=api_key,
        base_url=base_url if base_url.endswith("/v1") else base_url.rstrip("/") + "/v1",
        temperature=0.9,
        top_p=0.95,
        max_tokens=600,
        role_map={"system": "system", "user": "user", "assistant": "assistant", "tool": "tool"},
        extra_body=extra_body,
    )
    # 把 base_url 矫正回 deepseek 原始（v1 也行，deepseek 兼容）
    agno_db = SqliteDb(db_file=agno_db_path, session_table="agno_sessions")
    return Agent(
        name="崇祯帝测试玩家",
        id="chongzhen-tester",
        session_id="chongzhen-tester-main",
        db=agno_db,
        model=model_obj,
        instructions=[EMPEROR_SYSTEM_PROMPT.format(goal=goal, persona=persona)],
        add_history_to_context=True,
        num_history_runs=8,
        markdown=False,
    )


PROMPT_PATTERNS = [
    "召见谁？输入编号或姓名",
    "朕问：",
    "诏书草案> ",
    "确认颁布？输入 yes/颁布 确认",
    "按回车继续下一月，或输入 exit 退出游戏：",
    "请输入有效编号或姓名。",
    "可继续问话；若要让其退下，请输入 done。",
    "新的指令内容：",
    "新增执行者：",
    "关联奏报（编号/关键词）：",
    "指令内容：",
    "本月已有草案，确认全部不颁布并空过？",
    "陛下：确认入档（回车/可/准）",
]


def parse_emperor_output(raw: str) -> tuple[str, str]:
    """Parse JSON {reasoning, input} from agent output. Returns (reasoning, input_text)."""
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    obj_match = re.search(r"\{.*\}", text, re.DOTALL)
    if not obj_match:
        raise ValueError(f"崇祯 agent 输出无法解析为 JSON: {raw[:200]}")
    obj = json.loads(obj_match.group(0))
    reasoning = str(obj.get("reasoning", "")).strip()
    input_text = str(obj.get("input", "")).strip()
    if "\n" in input_text:
        input_text = input_text.splitlines()[0]
    return reasoning, input_text


def ask_emperor(agent: Agent, cli_chunk: str, prompt_hint: str) -> tuple[str, str]:
    payload = (
        f"CLI 当前输出（最新一段）：\n```\n{cli_chunk[-3500:]}\n```\n\n"
        f"当前等待输入的 prompt 是：{prompt_hint}\n\n"
        f"请输出下一条键盘输入（严格 JSON）。"
    )
    response = agent.run(payload)
    raw = response.content if hasattr(response, "content") else str(response)
    if isinstance(raw, list):
        raw = "".join(str(item) for item in raw)
    return parse_emperor_output(str(raw))


def strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)


def run(
    goal: str,
    persona: str,
    turns: int,
    log_path: str,
    base_url: str,
    model: str,
    api_key: str,
    db_path: str,
    agno_db_path: str,
    timeout: int = 600,
) -> int:
    log_file = open(log_path, "w", encoding="utf-8")

    def log(msg: str) -> None:
        log_file.write(msg + "\n")
        log_file.flush()
        print(msg, flush=True)

    log(f"=== 崇祯 Agent 自动玩家 ===")
    log(f"目标：{goal}")
    log(f"性格：{persona}")
    log(f"月数：{turns}")
    log(f"DB：{db_path}")
    log(f"模型：{model} @ {base_url}\n")

    # 清旧 DB
    for path in [db_path, agno_db_path]:
        if os.path.exists(path):
            os.remove(path)

    emperor = create_emperor_agent(api_key, base_url, model, goal, persona, agno_db_path + ".emperor.db")

    # emperor agent 用 playtest 配置（args.base_url/model），子进程 main.py 让其按自己的
    # CLI_*（默认 deepseek）跑——故不覆盖 CLI_*。仅写 OPENAI_*/MING_SIM_DB 兜底兼容旧路径。
    env = os.environ.copy()
    env["OPENAI_API_KEY"] = api_key
    env["OPENAI_BASE_URL"] = base_url
    env["OPENAI_MODEL"] = model
    env["MING_SIM_DB"] = db_path

    child = pexpect.spawn(
        ".venv/bin/python",
        ["-u", "main.py"],
        cwd=str(ROOT),
        env=env,
        encoding="utf-8",
        timeout=timeout,
        echo=False,
        dimensions=(40, 200),
    )
    child.logfile_read = log_file  # 把 CLI 所有输出也写进 log

    state = EmperorState()
    completed_periods = 0
    last_seen_turn = 1
    step = 0
    ministers_this_turn = 0  # 本月已召见大臣数
    MAX_MINISTERS_PER_TURN = 4  # 每月最多召见大臣数

    try:
        while completed_periods < turns:
            step += 1
            try:
                idx = child.expect(PROMPT_PATTERNS, timeout=timeout)
            except pexpect.EOF:
                log("\n[CLI EOF — 游戏结束]")
                break
            except pexpect.TIMEOUT:
                log(f"\n[CLI TIMEOUT at step {step}]")
                log(f"--- last buffer ---\n{child.before[-2000:]}\n---")
                break

            prompt_hint = PROMPT_PATTERNS[idx]
            cli_chunk = strip_ansi(child.before + prompt_hint)

            # 拟旨草稿确认（皇帝审阅后输入）
            if "陛下：确认入档" in prompt_hint:
                log(f"\n--- step {step} | prompt: 陛下：确认入档 ---")
                reasoning, action = ask_emperor(emperor, cli_chunk, prompt_hint.strip())
                log(f"[崇祯] reasoning: {reasoning}")
                log(f"[崇祯] input: {action!r}")
                child.sendline(action)
                continue

            # 检测月末（推进一月）
            if "按回车继续下一月" in prompt_hint:
                completed_periods += 1
                ministers_this_turn = 0  # 重置月内召见计数
                log(f"\n>>> 已完成 {completed_periods}/{turns} 月 <<<\n")
                if completed_periods >= turns:
                    child.sendline("exit")
                    break
                child.sendline("")
                continue

            log(f"\n--- step {step} | prompt: {prompt_hint.strip()[:40]} ---")

            # 月内召见超限且已有草案 → 强制退朝
            if "召见谁" in prompt_hint and ministers_this_turn >= MAX_MINISTERS_PER_TURN:
                has_drafts = "暂无指令" not in cli_chunk and "暂无草案" not in cli_chunk
                if has_drafts:
                    log(f"[硬限] 本月已召见 {ministers_this_turn} 位大臣，强制退朝颁诏")
                    child.sendline("quit")
                    continue

            try:
                reasoning, action = ask_emperor(emperor, cli_chunk, prompt_hint.strip())
            except Exception as exc:
                log(f"[崇祯 agent 出错: {exc}]")
                child.sendline("exit")
                break

            log(f"[崇祯] reasoning: {reasoning}")
            log(f"[崇祯] input: {action!r}")
            state.history.append({"step": step, "prompt": prompt_hint.strip(), "reasoning": reasoning, "input": action})

            child.sendline(action)
            # 召见大臣计数（输入不是 quit/back 才算一次召见）
            if "召见谁" in prompt_hint and action.lower() not in {"quit", "q", "exit", "back", ""}:
                ministers_this_turn += 1

            # 防 deadlock：连续无响应步数上限
            if step > 500:
                log("[step 上限 500 — 强制结束]")
                child.sendline("exit")
                break

        try:
            child.expect(pexpect.EOF, timeout=30)
        except (pexpect.TIMEOUT, pexpect.EOF):
            pass

    finally:
        if child.isalive():
            child.terminate(force=True)
        log_file.close()

    print(f"\n=== 结束。完成 {completed_periods}/{turns} 月。Log: {log_path} ===")
    return 0 if completed_periods >= turns else 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--goal", required=True, help="本局目标（写入崇祯 agent system prompt）")
    parser.add_argument(
        "--persona",
        default="balanced",
        choices=sorted(PERSONA_PRESETS.keys()),
        help="崇祯测试玩家的性格预设",
    )
    parser.add_argument("--turns", type=int, default=10)
    parser.add_argument("--log", default=f"scripts/runs/run_{int(time.time())}.log")
    parser.add_argument("--db", default="data/ming_sim_test.db")
    parser.add_argument("--agno-db", default="data/ming_sim_test_agno.db")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("PLAYTEST_BASE_URL") or os.environ.get("OPENAI_BASE_URL", "https://api.deepseek.com"),
        help="LLM base URL（默认读 PLAYTEST_BASE_URL，回退 OPENAI_BASE_URL）",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("PLAYTEST_MODEL") or os.environ.get("OPENAI_MODEL", "deepseek-v4-flash"),
        help="LLM model（默认读 PLAYTEST_MODEL，回退 OPENAI_MODEL）",
    )
    parser.add_argument("--timeout", type=int, default=300, help="单步 CLI 超时（秒）")
    args = parser.parse_args()

    api_key = (os.environ.get("PLAYTEST_API_KEY") or os.environ.get("OPENAI_API_KEY", "")).strip()
    if not api_key:
        sys.exit("PLAYTEST_API_KEY / OPENAI_API_KEY 均未设置，请先 source .env 或 export。")

    os.makedirs(os.path.dirname(args.log) or ".", exist_ok=True)
    os.makedirs(os.path.dirname(args.db) or ".", exist_ok=True)

    sys.exit(
        run(
            goal=args.goal,
            persona=PERSONA_PRESETS[args.persona],
            turns=args.turns,
            log_path=args.log,
            base_url=args.base_url,
            model=args.model,
            api_key=api_key,
            db_path=args.db,
            agno_db_path=args.agno_db,
            timeout=args.timeout,
        )
    )


if __name__ == "__main__":
    main()
