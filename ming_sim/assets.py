"""资源加载与 JSON 校验辅助。L0 叶子模块。

只读 content/ 下设定文件；不持有任何全局态。
"""

from __future__ import annotations

import json
import os
import re
import textwrap
from typing import Dict, List

from ming_sim.constants import CONTENT_DIR, MONEY_UNIT, TURN_UNIT, WRAP


def wrap(text: str) -> str:
    return "\n".join(textwrap.wrap(text, width=WRAP, replace_whitespace=False))


def load_text_asset(relative_path: str) -> str:
    path = os.path.join(CONTENT_DIR, relative_path)
    try:
        with open(path, "r", encoding="utf-8") as file:
            text = file.read().strip()
    except OSError as error:
        raise SystemExit(f"设定文件缺失或不可读：{path} ({error})") from error
    return text.replace("{{TURN_UNIT}}", TURN_UNIT)


def load_json_asset(relative_path: str) -> object:
    path = os.path.join(CONTENT_DIR, relative_path)
    try:
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)
    except OSError as error:
        raise SystemExit(f"设定文件缺失或不可读：{path} ({error})") from error
    except json.JSONDecodeError as error:
        raise SystemExit(f"设定文件 JSON 格式错误：{path} ({error})") from error


def strip_json_fence(text: str) -> str:
    match = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if match:
        return match.group(1).strip()
    return text.strip()


def format_money(value: int) -> str:
    return f"{value}{MONEY_UNIT}"


def format_money_delta(value: int) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{format_money(value)}"


def require_dict(data: object, path: str) -> Dict[str, object]:
    if not isinstance(data, dict):
        raise SystemExit(f"设定文件应为 JSON object：content/{path}")
    return data


def require_list(data: object, path: str) -> List[object]:
    if not isinstance(data, list):
        raise SystemExit(f"设定文件应为 JSON array：content/{path}")
    return data


def string_list(value: object, path: str) -> List[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise SystemExit(f"设定字段应为字符串数组：{path}")
    return [str(item) for item in value]


def int_field(data: Dict[str, object], key: str, path: str) -> int:
    try:
        return int(data[key])
    except (KeyError, TypeError, ValueError) as error:
        raise SystemExit(f"设定字段应为整数：{path}.{key}") from error


def str_field(data: Dict[str, object], key: str, path: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SystemExit(f"设定字段应为非空字符串：{path}.{key}")
    return value.strip()
