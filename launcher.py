"""桌面打包入口：起 uvicorn 子线程 → 用 pywebview 套壳渲染 React UI。

PyInstaller 入口（spec 里的 entry_script）即此文件。

退出策略：webview 关窗 → webview.start() 返回 → 进程结束 → uvicorn daemon 线程随之销毁。

调试用 env：
  MING_DEBUG=1            打开 webview devtools + uvicorn info 日志
  MING_USE_BROWSER=1      不开 webview，回退到系统浏览器（pywebview 行为异常时用）
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser

# Windows windowed (--noconsole) 下 sys.stdout/stderr = None。uvicorn 的
# DefaultFormatter.__init__ 调 sys.stdout.isatty() 判颜色 → AttributeError 崩。
# 先给 None 的标准流兜一个假写流（带 isatty()→False），再 reconfigure。
import io


class _NullStream(io.TextIOBase):
    def isatty(self) -> bool:
        return False

    def write(self, s: str) -> int:
        return len(s)

    def flush(self) -> None:
        pass


if sys.stdout is None:
    sys.stdout = _NullStream()  # type: ignore[assignment]
if sys.stderr is None:
    sys.stderr = _NullStream()  # type: ignore[assignment]

# 强制 unbuffered，PyInstaller 包内 print 否则可能丢
try:
    sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
    sys.stderr.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
except Exception:
    pass


def _log(msg: str) -> None:
    """同时写 stdout + ~/.ming_sim/launcher.log，方便 .app 双击模式 debug。"""
    line = f"[launcher] {msg}"
    print(line, flush=True)
    try:
        from ming_sim.paths import user_data_dir
        log_path = user_data_dir() / "launcher.log"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


WINDOW_TITLE = "明末力挽狂澜"
WINDOW_WIDTH = 1366
WINDOW_HEIGHT = 880
WINDOW_MIN_WIDTH = 1024
WINDOW_MIN_HEIGHT = 720


def _find_free_port(preferred: int = 8010) -> int:
    """优先 8010，被占则系统分配。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        try:
            s.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]
    finally:
        s.close()


def _wait_server_ready(url: str, timeout: float = 15.0) -> bool:
    """轮询 / 直到返回任意 HTTP 状态（包括 404）。
    只要 server 有响应说明 uvicorn 已 listen；urlopen 对 4xx/5xx 会抛 HTTPError，
    那也算就绪——区分是 connection error 还是 HTTP 响应。"""
    import urllib.error
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=0.5)
            return True
        except urllib.error.HTTPError:
            # 有 HTTP 响应（如 404）= server 已就绪
            return True
        except Exception:
            time.sleep(0.15)
    return False


_server_error: str = ""


def _start_server(port: int, debug: bool) -> None:
    """uvicorn 子线程入口。listen 全 loopback：localhost 解析可能走 IPv6 ::1。
    子线程内崩溃会被 daemon 吞掉，主线程只见超时——故全程兜异常写日志，
    并把错误存 _server_error 供主线程展示。"""
    global _server_error
    try:
        import uvicorn
        from web_app import app
        _log("web_app 导入成功，uvicorn.run 启动中...")
        uvicorn.run(
            app,
            host="127.0.0.1",
            port=port,
            log_level="info",  # 强制 info 确保看到 GET 行
        )
    except Exception as e:
        import traceback
        _server_error = f"{e!r}\n{traceback.format_exc()}"
        _log(f"uvicorn 子线程崩溃：{_server_error}")


def _open_browser_fallback(url: str) -> None:
    """pywebview 不可用 / 用户主动选浏览器模式时走这条。"""
    print(f"[launcher] 浏览器模式：{url}")
    print("[launcher] 关闭本窗口即停服。")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    # 阻塞主线程让 uvicorn daemon 不退
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


def main() -> None:
    debug = bool(os.environ.get("MING_DEBUG"))
    use_browser = bool(os.environ.get("MING_USE_BROWSER"))

    _log(f"启动：frozen={getattr(sys,'frozen',False)} platform={sys.platform} py={sys.version.split()[0]}")

    port = _find_free_port(8010)
    # WKWebView 对 IP 偶有 bug；用 localhost 域名走 DNS 解析更稳
    url = f"http://localhost:{port}"
    _log(f"分配端口 {port} url={url}")

    # uvicorn 跑 daemon 线程，主线程留给 pywebview / 浏览器 fallback
    server_thread = threading.Thread(target=_start_server, args=(port, debug), daemon=True)
    server_thread.start()
    _log("uvicorn 子线程已启动，等待就绪...")

    if not _wait_server_ready(url):
        if _server_error:
            _log(f"服务启动超时（>15s）——子线程已崩，根因：\n{_server_error}")
        else:
            _log("服务启动超时（>15s）——子线程未崩但 15s 内未 listen（可能首次冷启过慢/被防火墙拦）。")
        sys.exit(1)
    _log(f"服务已就绪：{url}")

    if use_browser:
        _log("MING_USE_BROWSER=1，走系统浏览器。")
        _open_browser_fallback(url)
        return

    try:
        import webview  # pywebview
        _log(f"pywebview 导入成功")
    except Exception as e:
        _log(f"pywebview 导入失败：{e!r}，回退浏览器。")
        _open_browser_fallback(url)
        return

    try:
        webview.create_window(
            WINDOW_TITLE,
            url,
            width=WINDOW_WIDTH,
            height=WINDOW_HEIGHT,
            min_size=(WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT),
            confirm_close=False,
        )
        _log("create_window 完成，调用 webview.start()...")
        # debug=True 打开 devtools（Mac 右键 Inspect Element）；http_server=False 因 server 已自起
        webview.start(debug=debug, http_server=False)
        _log("webview 已关闭，正常退出。")
    except Exception as e:
        _log(f"webview 启动崩溃：{e!r}")
        import traceback
        tb = traceback.format_exc()
        _log(tb)
        _log("回退浏览器模式。")
        _open_browser_fallback(url)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[launcher] 已停。")
    except Exception:
        import traceback
        traceback.print_exc()
        try:
            input("\n出错了，按 Enter 关闭窗口…")
        except Exception:
            pass
        sys.exit(1)
