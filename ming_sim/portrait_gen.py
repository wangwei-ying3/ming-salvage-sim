"""官员立绘自动生成：Prompt 装配 + 多提供商 API 适配 + 池图分配。

L1：不依赖游戏状态，纯函数。集成点在 web_app.py。
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import time
import urllib.request
from typing import Any, Dict, Optional

from ming_sim.paths import bundled_path

PORTRAIT_DIR = bundled_path("web", "public", "portraits")

# ── 池图分派系子池 ──
FACTION_POOLS: Dict[str, range] = {
    "皇党":  range(1, 11),    # minister_pool_1 ~ 10
    "阉党":  range(11, 21),   # minister_pool_11 ~ 20
    "东林":  range(21, 31),   # minister_pool_21 ~ 30
    "军队":  range(31, 41),   # minister_pool_31 ~ 40
    "宗室":  range(41, 47),   # minister_pool_41 ~ 46
    "中立":  range(47, 52),   # minister_pool_47 ~ 51
    "西学":  range(52, 56),   # minister_pool_52 ~ 55
}
GENERAL_POOL = range(56, 61)  # minister_pool_56 ~ 60 通用兜底
TOTAL_POOL_SLOTS = 60

# ── 图像 API 预设 ──
PROVIDER_PRESETS: Dict[str, Dict[str, str]] = {
    "dashscope": {
        "label": "通义万相 (DashScope)",
        "base_url": "https://dashscope.aliyuncs.com/api/v1",
        "model": "wanx-v1",
    },
    "openai": {
        "label": "OpenAI DALL·E 3",
        "base_url": "https://api.openai.com/v1",
        "model": "dall-e-3",
    },
    "openai_compat": {
        "label": "OpenAI 兼容（自定义）",
        "base_url": "",
        "model": "",
    },
}


# ═══════════════════════════════════════════════════════════════════
# Prompt 装配引擎
# ═══════════════════════════════════════════════════════════════════

def build_portrait_prompt(name: str, office: str, office_type: str,
                          faction: str, style: str,
                          loyalty: int, integrity: int, courage: int,
                          birth_year: int, current_year: int) -> str:
    """根据人物属性组装英文图像 prompt。英文比中文在 AI 生图更稳定。"""

    faction_desc = _FACTION_DESC.get(faction, "Ming dynasty official, neutral bearing")
    face_desc = _face_desc(integrity, courage)
    age_desc = _age_desc(birth_year, current_year)
    expr_desc = _expression_desc(style)
    robe_desc = _robe_desc(office_type, faction)
    posture_desc = _posture_desc(courage, loyalty)

    return (
        f"{name}, {office}, {faction_desc}, "
        f"{face_desc}, {age_desc}, {expr_desc}, "
        f"{robe_desc}, {posture_desc}. "
        f"Ming dynasty official full-body portrait, standing pose, hands folded in sleeve, "
        f"traditional Chinese court painting style, ink wash with light color, "
        f"white background, vertical composition 3:4, detailed robes and official hat, "
        f"high detail, masterwork quality"
    )


_FACTION_DESC: Dict[str, str] = {
    "皇党": "loyal court official, dignified upright bearing, respectful formal presence",
    "阉党": "palace eunuch official, shrewd watchful presence, soft plump features",
    "东林": "Confucian scholar-official, upright moral bearing, lean ascetic appearance",
    "军队": "military commander, battle-hardened authoritative presence, weathered complexion",
    "宗室": "imperial clan nobleman, aristocratic refined bearing, richly dressed",
    "中立": "career bureaucrat, neutral measured demeanor, unremarkable balanced features",
    "西学": "reform-minded scholar, curious intellectual bearing, subtle Western influence in dress",
}


def _face_desc(integrity: int, courage: int) -> str:
    if integrity >= 70 and courage >= 60:
        return "sharp angular features, piercing honest eyes, thin determined lips"
    if integrity >= 70:
        return "thin scholarly face, worried furrowed brow, cautious sincere eyes"
    if integrity <= 30:
        return "plump oily complexion, small calculating eyes, fleshy jowls, smug mouth"
    return "balanced regular features, composed neutral expression"


def _age_desc(birth_year: int, current_year: int) -> str:
    age = current_year - birth_year if birth_year else 45
    if age < 35:
        return "young man, smooth face, clean-shaven, bright fresh complexion"
    if age <= 50:
        return "middle-aged, thin beard, mature face with fine lines"
    return "elderly, grey beard, weathered wise face with deep wrinkles"


def _expression_desc(style: str) -> str:
    mapping: Dict[str, str] = {
        "持重守法": "calm steady gaze, slight reserved frown, composed dignified manner",
        "刚正不阿": "stern righteous expression, unflinching direct stare, unbending posture",
        "深沉权变": "subtle knowing half-smile, hooded calculating eyes, tilted head",
        "敢任事": "energetic determined look, forward-leaning intensity, bold confident gaze",
        "圆融周到": "pleasant diplomatic smile, warm approachable eyes, smooth affable manner",
        "狠辣务实": "cold ruthless stare, thin cruel mouth, intimidating calculating presence",
        "刚烈忠勇": "fierce loyal glare, muscular jaw, battle-ready intensity",
        "老成持重": "experienced steady gaze, deep thoughtful eyes, slow deliberate bearing",
        "谨慎忠诚": "watchful cautious eyes, slightly hunched protective posture, earnest face",
        "务实算账": "focused meticulous expression, furrowed concentrating brow, precise careful eyes",
        "稳健试办": "measured experimental look, curious but guarded, pragmatic thoughtful expression",
        "筹谋深远": "far-seeing contemplative gaze, strategic distant look, patient calculating",
        "酷烈狠辣": "merciless piercing stare, cruel twisted smile, gaunt intimidating features",
        "阴柔工心": "soft effeminate features, knowing manipulative smile, hooded watchful eyes",
    }
    return mapping.get(style, "dignified composed expression, formal court manner")


def _robe_desc(office_type: str, faction: str) -> str:
    if faction == "阉党":
        return "ornate embroidered eunuch palace robe, dark purple-grey, round collar, no official hat"
    if office_type in ("兵部",):
        return "dark military surcoat over armor plates, crimson sash, martial bearing"
    if office_type in ("户部",):
        return "blue-grey official robe, holding ledger scroll, ink-stained fingers"
    if office_type in ("内阁",):
        return "deep red senior grand secretary robe, gold rank badge, elaborate official hat"
    if office_type in ("司礼监",):
        return "ornate embroidered eunuch robe with dragon-motif trim, high rank insignia"
    if office_type in ("东厂",):
        return "black embroidered secret police robe, silver insignia, dark imposing presence"
    return "standard Ming dynasty official robe in appropriate color, formal court attire"


def _posture_desc(courage: int, loyalty: int) -> str:
    if courage >= 70 and loyalty >= 70:
        return "standing tall, chest out, one hand resting on sword hilt, commanding stance"
    if courage < 40 and loyalty >= 70:
        return "humble bowing posture, hands clasped submissively, obedient deferential stance"
    if courage >= 70:
        return "arms crossed, chin raised slightly defiantly, confident unyielding stance"
    return "standing pose, hands folded in sleeve, formal court manner, neutral stance"


# ═══════════════════════════════════════════════════════════════════
# 池图分配
# ═══════════════════════════════════════════════════════════════════

def assign_pool_portrait(db, faction: str) -> Optional[str]:
    """根据派系从对应子池中随机选一张未占用的池图。

    同派系角色可能分配到同一张池图（池图是风格模板）。
    子池用完 → 通用池兜底 → None（回退占位符）。
    """
    pool_range = list(FACTION_POOLS.get(faction, GENERAL_POOL))

    # 优先选该派系子池
    used_ids = _used_pool_ids(db)
    available = [i for i in pool_range if f"minister_pool_{i}" not in used_ids]
    if not available:
        # 回退通用池
        available = [i for i in GENERAL_POOL if f"minister_pool_{i}" not in used_ids]
    if not available:
        return None

    # 随机选一张，但有同名文件才分配（可能还没生成）
    chosen = random.choice(available)
    path = os.path.join(PORTRAIT_DIR, f"minister_pool_{chosen}.png")
    if not os.path.isfile(path):
        # 尝试找任意存在的池图
        all_ids = list(range(1, TOTAL_POOL_SLOTS + 1))
        existing = [i for i in all_ids if os.path.isfile(os.path.join(PORTRAIT_DIR, f"minister_pool_{i}.png"))]
        if existing:
            chosen = random.choice(existing)
        else:
            return None  # 没有池图文件

    return f"minister_pool_{chosen}"


def _used_pool_ids(db) -> set:
    """查询已被角色占用的池图 ID 集合。"""
    rows = db.conn.execute(
        "SELECT DISTINCT portrait_id FROM characters WHERE portrait_id LIKE 'minister_pool_%'"
    ).fetchall()
    return {r["portrait_id"].replace("minister_pool_", "") for r in rows if r["portrait_id"]}


# ═══════════════════════════════════════════════════════════════════
# 图像生成 API 适配层
# ═══════════════════════════════════════════════════════════════════

class ImageConfig:
    """图像生成配置。"""
    def __init__(self, provider: str = "", base_url: str = "",
                 model: str = "", api_key: str = ""):
        self.provider = provider
        self.base_url = base_url
        self.model = model
        self.api_key = api_key

    @property
    def enabled(self) -> bool:
        return bool(self.provider and self.api_key.strip())


def load_image_config() -> ImageConfig:
    """从 runtime_llm.json 加载图像配置。"""
    from ming_sim.llm_config import load_runtime_llm
    cfg = load_runtime_llm()
    return ImageConfig(
        provider=cfg.get("image_provider", ""),
        base_url=cfg.get("image_base_url", ""),
        model=cfg.get("image_model", ""),
        api_key=cfg.get("image_api_key", ""),
    )


def generate_portrait(name: str, prompt: str, config: ImageConfig) -> Optional[str]:
    """统一入口：调用图像 API 生成立绘，保存为 PNG 返回路径。

    失败（无 Key、API 错误、超时）返回 None，调用方回退池图。
    """
    if not config.enabled:
        return None

    # 已有专属图 → 不重复生成
    existing = os.path.join(PORTRAIT_DIR, f"minister_{name}.png")
    if os.path.isfile(existing):
        return existing

    img_bytes: Optional[bytes] = None

    try:
        if config.provider == "dashscope":
            img_bytes = _gen_dashscope(prompt, config)
        elif config.provider == "openai":
            img_bytes = _gen_openai_images(prompt, config)
        elif config.provider == "openai_compat":
            img_bytes = _gen_openai_images(prompt, config)
    except Exception:
        pass

    if not img_bytes:
        return None

    os.makedirs(PORTRAIT_DIR, exist_ok=True)
    with open(existing, "wb") as f:
        f.write(img_bytes)
    return existing


def test_image_connection(config: ImageConfig) -> Dict[str, Any]:
    """测试图像 API 连接。返回 {ok, message, cost_hint}。"""
    if not config.enabled:
        return {"ok": False, "message": "未配置图像 API Key", "cost_hint": ""}
    try:
        result = generate_portrait("_test_conn_",
                                   "A simple red circle on white background, minimalist",
                                   config)
        if result:
            os.remove(result)
        return {"ok": True, "message": "连接成功", "cost_hint": "约 0.04 元/张"}
    except Exception as e:
        return {"ok": False, "message": f"连接失败: {e}", "cost_hint": ""}


# ── 通义万相适配 ──

def _gen_dashscope(prompt: str, config: ImageConfig) -> Optional[bytes]:
    """通义万相：异步入队 → 轮询 task → 下载结果 URL。"""
    import urllib.error

    api_url = config.base_url.rstrip("/")
    if not api_url.endswith("/services/aigc/text2image/image-synthesis"):
        api_url = f"{api_url}/services/aigc/text2image/image-synthesis"

    body = json.dumps({
        "model": config.model or "wanx-v1",
        "input": {"prompt": prompt},
        "parameters": {"size": "512*512", "n": 1},
    }).encode("utf-8")

    req = urllib.request.Request(api_url, data=body, method="POST")
    req.add_header("Authorization", f"Bearer {config.api_key}")
    req.add_header("Content-Type", "application/json")

    try:
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")[:300]
        print(f"[portrait_gen] DashScope HTTP {e.code}: {body_text}")
        return None
    except Exception as e:
        print(f"[portrait_gen] DashScope request failed: {e}")
        return None

    task_id = (data.get("output", {}) or {}).get("task_id", "")
    if not task_id:
        print(f"[portrait_gen] DashScope: no task_id in response")
        return None

    # 轮询 task（最长 30s）
    task_url = f"{api_url}/tasks/{task_id}"
    deadline = time.time() + 30
    while time.time() < deadline:
        time.sleep(1.5)
        try:
            req2 = urllib.request.Request(task_url)
            req2.add_header("Authorization", f"Bearer {config.api_key}")
            resp2 = urllib.request.urlopen(req2, timeout=10)
            task_data = json.loads(resp2.read().decode("utf-8"))
        except Exception:
            continue

        status = (task_data.get("output", {}) or {}).get("task_status", "")
        if status == "SUCCEEDED":
            results = (task_data.get("output", {}) or {}).get("results", [])
            if results and results[0].get("url"):
                return _download_image(results[0]["url"])
            return None
        if status == "FAILED":
            print(f"[portrait_gen] DashScope task failed: {task_data}")
            return None

    print("[portrait_gen] DashScope task timed out")
    return None


# ── OpenAI Images API 适配（覆盖 DALL·E 3 + 所有兼容厂商） ──

def _gen_openai_images(prompt: str, config: ImageConfig) -> Optional[bytes]:
    """POST {base_url}/images/generations → {data[0].url 或 data[0].b64_json}"""
    import urllib.error

    api_url = config.base_url.rstrip("/")
    if not api_url.endswith("/images/generations"):
        api_url = f"{api_url}/images/generations"

    body = json.dumps({
        "model": config.model or "dall-e-3",
        "prompt": prompt,
        "size": "1024x1024",
        "n": 1,
        "response_format": "url",
    }).encode("utf-8")

    req = urllib.request.Request(api_url, data=body, method="POST")
    req.add_header("Authorization", f"Bearer {config.api_key}")
    req.add_header("Content-Type", "application/json")

    try:
        resp = urllib.request.urlopen(req, timeout=30)
        data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")[:300]
        print(f"[portrait_gen] OpenAI Images HTTP {e.code}: {body_text}")
        return None
    except Exception as e:
        print(f"[portrait_gen] OpenAI Images request failed: {e}")
        return None

    results = data.get("data", [])
    if not results:
        return None

    item = results[0]
    url = item.get("url", "")
    b64 = item.get("b64_json", "")

    if url:
        return _download_image(url)
    if b64:
        import base64
        return base64.b64decode(b64)

    return None


def _download_image(url: str) -> Optional[bytes]:
    try:
        req = urllib.request.Request(url)
        resp = urllib.request.urlopen(req, timeout=30)
        return resp.read()
    except Exception as e:
        print(f"[portrait_gen] download failed: {e}")
        return None
