import shutil
import uuid
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import web_app


def _image_bytes(fmt: str, color=(255, 0, 0)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (1, 1), color).save(buf, format=fmt)
    return buf.getvalue()


PNG_BYTES = _image_bytes("PNG")
CORRUPT_PNG_BYTES = b"\x89PNG\r\n\x1a\nnot a real png"
SVG_BYTES = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


class FakeGame:
    def __init__(self, names):
        self.characters = {name: SimpleNamespace(name=name, portrait_id="") for name in names}
        self.portrait_updates = []
        self.fail_set = False

    def find_character(self, name):
        return self.characters.get(name)

    def set_custom_portrait(self, name, portrait_id):
        if self.fail_set:
            raise RuntimeError("db update failed")
        self.portrait_updates.append((name, portrait_id))
        self.characters[name].portrait_id = portrait_id


@pytest.fixture
def isolated_dir():
    base = Path("test_avatar_upload_tmp")
    base.mkdir(parents=True, exist_ok=True)
    path = base / uuid.uuid4().hex
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def avatar_client(isolated_dir, monkeypatch):
    upload_dir = isolated_dir / "uploads" / "portraits"
    names = [
        "正常",
        "..\\..\\evil",
        "../../evil",
        "a\\b",
        "a:b",
        "CON",
        "ℌｅｌｌｏ",
    ]
    fake_game = FakeGame(names)
    monkeypatch.setattr(web_app, "UPLOAD_PORTRAIT_DIR", str(upload_dir))
    monkeypatch.setattr(web_app, "get_game", lambda: fake_game)
    monkeypatch.setattr(web_app.os, "replace", _test_replace)
    return TestClient(web_app.app), fake_game, upload_dir


def _upload(client, name, content, content_type="image/png"):
    return client.post(
        f"/api/consorts/{name}/portrait",
        files={"file": ("portrait.bin", content, content_type)},
    )


def _assert_all_files_under(upload_dir: Path):
    root = upload_dir.resolve()
    for path in upload_dir.rglob("*"):
        if path.is_file():
            path.resolve().relative_to(root)


def _test_replace(src, dst):
    source = Path(src)
    target = Path(dst)
    target.write_bytes(source.read_bytes())
    try:
        source.unlink()
    except OSError:
        pass


def test_storage_key_is_deterministic_hash_and_paths_are_contained(isolated_dir, monkeypatch):
    monkeypatch.setattr(web_app, "UPLOAD_PORTRAIT_DIR", str(isolated_dir))

    for name in ("..\\..\\evil", "../../evil", "a\\b", "a:b", "CON", "ℌｅｌｌｏ"):
        key = web_app._portrait_storage_key(name)
        assert len(key) == 64
        assert all(ch in "0123456789abcdef" for ch in key)
        assert name not in key
        path = web_app._safe_portrait_path(key, "png")
        assert path.name == f"{key}.png"
        path.resolve().relative_to(isolated_dir.resolve())


def test_malicious_character_names_do_not_escape_upload_dir(avatar_client):
    client, _fake_game, upload_dir = avatar_client

    for name in ("..\\..\\evil", "a\\b", "a:b", "CON", "ℌｅｌｌｏ"):
        response = _upload(client, name, PNG_BYTES)
        assert response.status_code == 200, response.text

    _assert_all_files_under(upload_dir)
    assert all(".." not in path.name and "\\" not in path.name and ":" not in path.name for path in upload_dir.iterdir())


def test_non_images_svg_and_corrupt_images_are_rejected(avatar_client):
    client, _fake_game, upload_dir = avatar_client

    cases = [
        (b"plain text", "image/png"),
        (SVG_BYTES, "image/png"),
        (CORRUPT_PNG_BYTES, "image/png"),
    ]
    for payload, content_type in cases:
        response = _upload(client, "正常", payload, content_type)
        assert response.status_code == 400, response.text

    assert not any(upload_dir.rglob("*"))


def test_valid_png_jpeg_and_webp_uploads_are_accepted(avatar_client):
    client, _fake_game, upload_dir = avatar_client

    response = _upload(client, "正常", PNG_BYTES, "image/png")
    assert response.status_code == 200, response.text
    assert web_app._find_custom_portrait_file(web_app._portrait_storage_key("正常")).suffix == ".png"

    response = _upload(client, "正常", _image_bytes("JPEG"), "image/jpeg")
    assert response.status_code == 200, response.text
    assert web_app._find_custom_portrait_file(web_app._portrait_storage_key("正常")).suffix == ".jpg"

    features = pytest.importorskip("PIL.features")
    if not features.check("webp"):
        pytest.skip("Pillow build lacks WebP support")
    response = _upload(client, "正常", _image_bytes("WEBP", (0, 255, 0)), "image/webp")
    assert response.status_code == 200, response.text
    assert web_app._find_custom_portrait_file(web_app._portrait_storage_key("正常")).suffix == ".webp"
    _assert_all_files_under(upload_dir)


def test_oversized_upload_is_rejected_before_validation(avatar_client):
    client, _fake_game, upload_dir = avatar_client

    payload = b"x" * (web_app.MAX_PORTRAIT_BYTES + 1)
    response = _upload(client, "正常", payload, "image/png")

    assert response.status_code == 413
    assert not any(upload_dir.rglob("*"))


def test_upload_db_failure_keeps_old_avatar_and_no_db_reference(avatar_client):
    client, fake_game, upload_dir = avatar_client
    response = _upload(client, "正常", PNG_BYTES)
    assert response.status_code == 200, response.text
    old_path = web_app._find_custom_portrait_file(web_app._portrait_storage_key("正常"))
    assert old_path is not None and old_path.exists()
    old_bytes = old_path.read_bytes()

    fake_game.fail_set = True
    response = _upload(client, "正常", _image_bytes("PNG", (0, 0, 255)))

    assert response.status_code == 500
    assert fake_game.characters["正常"].portrait_id == "custom:正常"
    current = web_app._find_custom_portrait_file(web_app._portrait_storage_key("正常"))
    assert current == old_path
    assert current.read_bytes() == old_bytes
    _assert_all_files_under(upload_dir)


def test_replace_failure_does_not_change_db(avatar_client, monkeypatch):
    client, fake_game, _upload_dir = avatar_client

    def fail_replace(src, dst):
        raise OSError("replace failed")

    monkeypatch.setattr(web_app.os, "replace", fail_replace)
    response = _upload(client, "正常", PNG_BYTES)

    assert response.status_code == 500
    assert fake_game.characters["正常"].portrait_id == ""


def test_delete_clears_db_and_custom_portrait_is_not_served(avatar_client):
    client, fake_game, _upload_dir = avatar_client
    response = _upload(client, "正常", PNG_BYTES)
    assert response.status_code == 200, response.text
    assert client.get("/portraits/custom/正常").status_code == 200

    response = client.delete("/api/consorts/正常/portrait")

    assert response.status_code == 200, response.text
    assert fake_game.characters["正常"].portrait_id == ""
    assert client.get("/portraits/custom/正常").status_code == 404


def test_delete_file_failure_clears_db_without_pointing_to_missing_file(avatar_client, monkeypatch):
    client, fake_game, _upload_dir = avatar_client
    response = _upload(client, "正常", PNG_BYTES)
    assert response.status_code == 200, response.text

    def fail_unlink(self, missing_ok=False):
        raise OSError("unlink failed")

    monkeypatch.setattr(Path, "unlink", fail_unlink)
    response = client.delete("/api/consorts/正常/portrait")

    assert response.status_code == 200, response.text
    assert fake_game.characters["正常"].portrait_id == ""
