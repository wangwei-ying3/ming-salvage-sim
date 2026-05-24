#!/usr/bin/env python3
"""明末力挽狂澜 文字 MVP —— CLI 入口。

游戏内核已模块化进 ming_sim/ 包。本文件只做 argparse + 启动 CLI 驱动。
CLI 与 Web（web_app.py）共用 ming_sim.session.GameSession 流转层。
"""

from __future__ import annotations

import argparse
import os

from ming_sim.cli.terminal import run_cli


def main() -> None:
    parser = argparse.ArgumentParser(description="Ming Salvage Sim text MVP")
    parser.add_argument("--seed", type=int, default=int(os.environ.get("MING_SIM_SEED", "7")),
                        help="random seed（影响事件抽样）")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("CLI_BASE_URL") or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        help="OpenAI-compatible base URL（默认读 CLI_BASE_URL，回退 OPENAI_BASE_URL）",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("CLI_MODEL") or os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        help="OpenAI-compatible model name（默认读 CLI_MODEL，回退 OPENAI_MODEL）",
    )
    parser.add_argument(
        "--db",
        default=os.environ.get("MING_SIM_DB", "data/ming_sim.db"),
        help="SQLite database path",
    )
    parser.add_argument(
        "--start-ym",
        default=os.environ.get("MING_SIM_START_YM", ""),
        help="调试用：跳到指定年月起手，格式 YYYY.MM（如 1629.04）。仅对新建 DB 生效。",
    )
    args = parser.parse_args()

    import random
    random.seed(args.seed)

    cli_api_key = os.environ.get("CLI_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
    run_cli(
        base_url=args.base_url,
        model=args.model,
        db_path=args.db,
        api_key=cli_api_key,
        start_ym=args.start_ym,
    )


if __name__ == "__main__":
    main()
