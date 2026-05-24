#!/usr/bin/env python3
"""扫 web/public/portraits/ + docs/portrait-prompts.md，生成立绘进度表 docs/portrait-status.md。

可反复跑：生图进度变了重跑刷新表。
状态判定：
  已生成      —— 存在 clean 文件名 minister_X.png / consort_X.png
  jimeng待整理 —— 仅有即梦导出的乱名文件（名内嵌 `X.png`），需重命名才可用
  待生成      —— 两者皆无
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from gen_portraits import parse_entries  # noqa: E402

OUT = ROOT / "web" / "public" / "portraits"
DOC = ROOT / "docs" / "portrait-status.md"
MD = ROOT / "docs" / "portrait-prompts.md"

HDR = re.compile(r"^#{2,4}\s+(.+?)\s+`((?:minister|consort)_[a-z0-9_]+\.png)`", re.M)


def main() -> None:
    name_of = {m.group(2): m.group(1).strip() for m in HDR.finditer(MD.read_text("utf-8"))}
    entries = parse_entries()

    clean = {p.name for p in OUT.glob("*.png")} if OUT.exists() else set()

    # 前端按中文名取 minister 专属图：minister_<人名>.png。
    # md 标题给出 中文名 + pinyin 文件名；人名取 · 前、去括号注。
    def cn_name(raw: str) -> str:
        import re as _re
        nm = raw.split("·")[0]
        return _re.sub(r"[（(].*?[)）]", "", nm).strip()

    ministers = []  # (中文名, 中文文件名)
    for f, _ in entries:
        if f.startswith("minister_"):
            cn = cn_name(name_of.get(f, ""))
            ministers.append((cn, f"minister_{cn}.png"))

    m_rows = ["| 人物 | 文件 | 状态 |", "|---|---|---|"]
    m_done = 0
    for cn, fn in ministers:
        st = "已生成" if fn in clean else "待生成"
        if st == "已生成":
            m_done += 1
        m_rows.append(f"| {cn} | `{fn}` | {st} |")
    m_n = len(ministers)

    # 后宫预设图池：consort_pool_1..20
    POOL_N = 20
    pool_have = sorted(
        int(p.name[len("consort_pool_"):-4])
        for p in OUT.glob("consort_pool_*.png")
        if p.name[len("consort_pool_"):-4].isdigit()
    ) if OUT.exists() else []
    c_done = len(pool_have)

    out = [
        "# 立绘生成进度",
        "",
        "> 自动生成：`.venv/bin/python scripts/portrait_status.py`。改图后重跑刷新。",
        "> 大臣 = 专属图 `minister_<中文名>.png`；后宫 = 预设图池 `consort_pool_<N>.png`（不绑人）。",
        "",
        f"## 大臣专属图（{m_done}/{m_n} 已生成）",
        "",
        "\n".join(m_rows),
        "",
        f"## 后宫预设图池（{c_done}/{POOL_N} 槽已出图）",
        "",
        f"已出图槽位：{pool_have}",
        f"待补槽位：{[n for n in range(1, POOL_N + 1) if n not in pool_have]}",
        "",
    ]
    DOC.write_text("\n".join(out), encoding="utf-8")
    print(f"写 {DOC}  大臣 {m_done}/{m_n}  后宫池 {c_done}/{POOL_N}")


if __name__ == "__main__":
    main()
