# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Ming Salvage Sim 桌面包。

打 onedir（首选）：
    pyinstaller MingSalvageSim.spec

打 onefile（单文件，启动慢）：
    pyinstaller --onefile MingSalvageSim.spec
    （或改下方 EXE/COLLECT 段；spec 默认走 onedir）

产物：dist/MingSalvageSim/
- macOS：.app bundle 由 BUNDLE 段生成（dist/MingSalvageSim.app）。
- Windows/Linux：dist/MingSalvageSim/ 整目录可分发。
"""

from PyInstaller.utils.hooks import collect_all, collect_submodules

# agno / openai / agno-sqlite 大量动态导入，全收集。
_agno_data, _agno_bin, _agno_hidden = collect_all("agno")
_openai_data, _openai_bin, _openai_hidden = collect_all("openai")
_tiktoken_data, _tiktoken_bin, _tiktoken_hidden = collect_all("tiktoken")
# pywebview + Mac WKWebView (pyobjc)
_webview_data, _webview_bin, _webview_hidden = collect_all("webview")

# FastAPI/uvicorn 系列
hiddenimports = (
    _agno_hidden
    + _openai_hidden
    + _tiktoken_hidden
    + _webview_hidden
    + collect_submodules("uvicorn")
    + collect_submodules("fastapi")
    + collect_submodules("anyio")
    + collect_submodules("starlette")
    + [
        "ming_sim",
        "ming_sim.cli.terminal",
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
    ]
)

datas = (
    _agno_data
    + _openai_data
    + _tiktoken_data
    + _webview_data
    + [
        ("content", "content"),
        ("web/dist", "web/dist"),
        ("web/public", "web/public"),
    ]
)

binaries = _agno_bin + _openai_bin + _tiktoken_bin + _webview_bin


block_cipher = None


a = Analysis(
    ["launcher.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 不打包测试/构建工具/CLI 第三方
        # 注意：tkinter 保留——launcher 缺 key 时用其弹窗收 key（GUI 模式无 stdin）
        "pytest",
        "PyQt5",
        "PyQt6",
        "PySide2",
        "PySide6",
        "matplotlib",
        "pandas",
        "numpy.tests",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MingSalvageSim",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # pywebview 套壳无需终端窗口；debug 需看日志时改 True 或跑 dist/.../MingSalvageSim 二进制
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="MingSalvageSim",
)

# macOS .app bundle
import sys
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="MingSalvageSim.app",
        icon=None,
        bundle_identifier="com.local.mingsalvagesim",
        info_plist={
            "NSHighResolutionCapable": True,
            "LSBackgroundOnly": False,
            "CFBundleShortVersionString": "0.1.0",
            "CFBundleVersion": "0.1.0",
            # WKWebView 默认禁明文 HTTP；允许 127.0.0.1 + localhost 本地 server
            "NSAppTransportSecurity": {
                "NSAllowsLocalNetworking": True,
                "NSExceptionDomains": {
                    "localhost": {
                        "NSExceptionAllowsInsecureHTTPLoads": True,
                        "NSIncludesSubdomains": True,
                    },
                    "127.0.0.1": {
                        "NSExceptionAllowsInsecureHTTPLoads": True,
                    },
                },
            },
        },
    )
