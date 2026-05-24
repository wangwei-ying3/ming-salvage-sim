#!/usr/bin/env python3
"""批量生人物立绘。从 docs/portrait-prompts.md 解析 (文件名, prompt)，调 gpt-image-2 落盘。

key 走环境变量 OPENAI_IMAGE_KEY，绝不写文件。可重跑：已存在的 png 跳过。

用法：
  export OPENAI_IMAGE_KEY=sk-xxx
  .venv/bin/python scripts/gen_portraits.py            # 跑全部缺失
  .venv/bin/python scripts/gen_portraits.py --only wang_chengen   # 只跑文件名含此串的
  .venv/bin/python scripts/gen_portraits.py --limit 3  # 只跑前 N 张（试水）
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MD = ROOT / "docs" / "portrait-prompts.md"
OUT = ROOT / "web" / "public" / "portraits"
BASE_URL = "https://vip.auto-code.net/v1"
MODEL = "gpt-image-2"
SIZE = "1024x1024"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# 标题行：#### 名字 `minister_xxx.png`，下一段围栏代码块是 prompt
HEADER_RE = re.compile(r"^#{2,4}\s+\S.*`((?:minister|consort)_[a-z0-9_]+\.png)`")


def parse_entries() -> list[tuple[str, str]]:
    """返回 [(filename, prompt), ...]，按 md 出现顺序。"""
    lines = MD.read_text(encoding="utf-8").splitlines()
    entries: list[tuple[str, str]] = []
    i = 0
    while i < len(lines):
        m = HEADER_RE.match(lines[i])
        if not m:
            i += 1
            continue
        fname = m.group(1)
        # 向下找下一个 ``` 围栏
        j = i + 1
        while j < len(lines) and lines[j].strip() != "```":
            j += 1
        if j >= len(lines):
            raise SystemExit(f"{fname}: 标题后无代码块围栏")
        body: list[str] = []
        j += 1
        while j < len(lines) and lines[j].strip() != "```":
            body.append(lines[j])
            j += 1
        prompt = " ".join(x.strip() for x in body if x.strip())
        if not prompt:
            raise SystemExit(f"{fname}: 代码块为空")
        entries.append((fname, prompt))
        i = j + 1
    return entries


def gen_one(key: str, prompt: str, timeout: int = 180) -> bytes:
    body = json.dumps({
        "model": MODEL, "prompt": prompt, "size": SIZE, "n": 1,
    }).encode("utf-8")
    # 中转网关会自动塞超长 X-Client-Request-Id（>512）导致 400；显式传短串覆盖之。
    req_id = hashlib.md5(prompt.encode("utf-8")).hexdigest()
    req = urllib.request.Request(
        f"{BASE_URL}/images/generations", data=body, method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": UA,
            "X-Client-Request-Id": req_id,
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    b64 = data["data"][0]["b64_json"]
    return base64.b64decode(b64)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="只跑文件名含此子串的")
    ap.add_argument("--limit", type=int, default=0, help="最多跑 N 张")
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--retries", type=int, default=2, help="单张失败重试次数")
    ap.add_argument("--workers", type=int, default=1, help="并发线程数")
    args = ap.parse_args()

    key = os.environ.get("OPENAI_IMAGE_KEY", "").strip()
    if not key:
        raise SystemExit("缺 OPENAI_IMAGE_KEY 环境变量")

    OUT.mkdir(parents=True, exist_ok=True)
    entries = parse_entries()
    if args.only:
        entries = [e for e in entries if args.only in e[0]]

    todo = [(f, p) for f, p in entries if not (OUT / f).exists()]
    print(f"解析 {len(entries)} 条，缺 {len(todo)} 张待生（已存在跳过）")
    if args.limit:
        todo = todo[: args.limit]
        print(f"--limit {args.limit}：本次只跑 {len(todo)} 张")

    n = len(todo)
    done_ct = [0]
    lock = threading.Lock()

    def work(item: tuple[str, str]) -> bool:
        fname, prompt = item
        out = OUT / fname
        for attempt in range(1, args.retries + 2):
            t0 = time.time()
            try:
                png = gen_one(key, prompt, args.timeout)
                out.write_bytes(png)
                dt = time.time() - t0
                with lock:
                    done_ct[0] += 1
                    print(f"[{done_ct[0]}/{n}] {fname}  {len(png)//1024}KB  {dt:.0f}s  OK", flush=True)
                return True
            except Exception as e:
                dt = time.time() - t0
                msg = str(e)[:160]
                with lock:
                    if attempt <= args.retries:
                        print(f"[{fname}] {dt:.0f}s  FAIL({attempt}) {msg} — 重试", flush=True)
                    else:
                        print(f"[{fname}] {dt:.0f}s  FAIL final {msg}", flush=True)
                if attempt <= args.retries:
                    time.sleep(3)
        return False

    if args.workers <= 1:
        results = [work(it) for it in todo]
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            results = list(ex.map(work, todo))

    ok = sum(results)
    print(f"\n完成：成功 {ok}，失败 {n - ok}，剩余缺 {n - ok} 张可重跑", flush=True)


if __name__ == "__main__":
    main()
