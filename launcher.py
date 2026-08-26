"""Windows 一键启动入口：准备本机数据目录、启动服务并打开浏览器。"""

from __future__ import annotations

import os
import socket
import sys
import threading
import webbrowser
from pathlib import Path

# Windows 无控制台发布版需要可写的标准输出，第三方日志初始化会读取这些对象。
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

_trace_before_import = os.getenv("NOVEL_ATLAS_STARTUP_TRACE")
if _trace_before_import:
    Path(_trace_before_import).write_text("Python 入口已加载\n", encoding="utf-8")

import uvicorn


HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def _startup_trace(message: str) -> None:
    """只在明确开启诊断时记录启动位置，正常用户不会产生日志。"""

    trace_path = os.getenv("NOVEL_ATLAS_STARTUP_TRACE")
    if not trace_path:
        return
    with Path(trace_path).open("a", encoding="utf-8") as stream:
        stream.write(message + "\n")


def available_port(preferred: int = DEFAULT_PORT) -> int:
    """优先使用固定端口；被占用时选择一个本机空闲端口。"""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((HOST, preferred))
            return preferred
        except OSError:
            probe.bind((HOST, 0))
            return int(probe.getsockname()[1])


def configure_packaged_storage() -> None:
    """发布版把数据库放到用户数据目录，升级程序不会覆盖书库。"""

    if not getattr(sys, "frozen", False) or os.getenv("NOVEL_DB_PATH"):
        return
    local_data = Path(os.getenv("LOCALAPPDATA", Path.home())) / "NovelAtlas"
    local_data.mkdir(parents=True, exist_ok=True)
    os.environ["NOVEL_DB_PATH"] = str(local_data / "novel_atlas.db")


def main() -> None:
    """启动仅监听本机的网页服务。"""

    _startup_trace("进入启动入口")
    configure_packaged_storage()
    _startup_trace("数据目录准备完成")
    port = available_port()
    _startup_trace(f"服务端口：{port}")
    address = f"http://{HOST}:{port}"
    threading.Timer(1.0, lambda: webbrowser.open(address)).start()
    _startup_trace("开始加载网页服务")
    from app.main import app

    _startup_trace("网页服务加载完成")
    uvicorn.run(app, host=HOST, port=port, log_config=None, access_log=False)


if __name__ == "__main__":
    main()
