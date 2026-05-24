#!/usr/bin/env python3
"""压缩 web/public/portraits/ 立绘：缩到 512 长边 + PNG 优化，原地覆盖，文件名不变。

前端只 160×213px 显示，1024 源图过大（70M）。缩 512 足够（含 retina）。
可重跑：已 ≤目标尺寸且体积合理的跳过。

用法：
  .venv/bin/python scripts/compress_portraits.py            # 压全部
  .venv/bin/python scripts/compress_portraits.py --size 512 # 指定长边
  .venv/bin/python scripts/compress_portraits.py --dry      # 只看预估，不写
"""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "web" / "public" / "portraits"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", type=int, default=512, help="长边目标像素")
    ap.add_argument("--dry", action="store_true", help="只预估不写盘")
    args = ap.parse_args()

    files = sorted(OUT.glob("*.png"))
    if not files:
        raise SystemExit(f"{OUT} 下无 png")

    before_total = after_total = 0
    for f in files:
        before = f.stat().st_size
        before_total += before
        with Image.open(f) as im:
            im = im.convert("RGBA") if im.mode in ("P", "LA") else im
            w, h = im.size
            scale = args.size / max(w, h)
            if scale < 1:
                nw, nh = round(w * scale), round(h * scale)
                im = im.resize((nw, nh), Image.LANCZOS)
            else:
                nw, nh = w, h  # 已小于目标，不放大，仅重存优化
            if args.dry:
                print(f"{f.name:34} {w}x{h} {before//1024}KB -> {nw}x{nh}（预估）")
                continue
            # RGBA→若无透明可转 RGB 省体积（立绘多为白底不透明）
            if im.mode == "RGBA":
                alpha = im.split()[-1]
                if alpha.getextrema() == (255, 255):
                    im = im.convert("RGB")
            im.save(f, format="PNG", optimize=True)
        after = f.stat().st_size
        after_total += after
        print(f"{f.name:34} {w}x{h} {before//1024}KB -> {nw}x{nh} {after//1024}KB  -{100*(before-after)//before}%")

    if not args.dry:
        print(f"\n总计：{before_total//1024//1024}MB -> {after_total//1024//1024}MB "
              f"（省 {100*(before_total-after_total)//before_total}%）")


if __name__ == "__main__":
    main()
