import json
import os
import struct
import subprocess
import threading
from pathlib import Path


def send_to_fdm(url, filename):
    host = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / (
        "Softdeluxe/Free Download Manager/wenativehost.exe"
    )
    if not host.is_file():
        raise RuntimeError("未找到 FDM 浏览器组件，请安装桌面版 Free Download Manager。")
    origin = "chrome-extension://ahmpjcflkgiildlgicmcieglgoilbfdp/"
    with subprocess.Popen(
        [str(host), origin], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW,
    ) as process:
        timer = threading.Timer(15, process.kill)
        timer.start()
        try:
            def exchange(request):
                message = json.dumps(request).encode("utf-8")
                process.stdin.write(struct.pack("<I", len(message)) + message)
                process.stdin.flush()
                header = process.stdout.read(4)
                if len(header) != 4:
                    raise RuntimeError("FDM 浏览器组件没有响应。")
                length, = struct.unpack("<I", header)
                if not 0 < length <= 1048576:
                    raise RuntimeError("FDM 浏览器组件响应无效。")
                reply = json.loads(process.stdout.read(length))
                if reply.get("error") or reply.get("id") != request["id"]:
                    raise RuntimeError("FDM 浏览器组件拒绝了下载请求。")
                return reply

            exchange({"id": "1", "type": "handshake", "handshake": {"api_version": "1", "browser": "Chrome"}})
            reply = exchange({
                "id": "2", "type": "create_downloads",
                "create_downloads": {"downloads": [{"url": url, "originalUrl": url, "suggestedName": filename}]},
            })
            if reply.get("result") == "0":
                raise RuntimeError("FDM 未接收下载任务。")
        except (OSError, ValueError) as exc:
            raise RuntimeError("无法连接 FDM 浏览器组件。") from exc
        finally:
            timer.cancel()
            process.kill()
