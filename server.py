import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

from fdm import send_to_fdm

ROOT = Path(__file__).resolve().parent
FFMPEG_DIR = ROOT / "tools" / "ffmpeg"
TOKEN = secrets.token_urlsafe(32)
LOCK = threading.Lock()
RESULTS = {}
DOWNLOADS = {}
DOWNLOAD_LOCK = threading.Lock()
MAX_BODY = 8192
PROXY = ""
HLS_PROTOCOLS = {"m3u8", "m3u8_native"}
HLS_FILTER = "[protocol^=m3u8]"


def normalize_url(value):
    if not isinstance(value, str) or len(value) > 4096:
        raise ValueError("请输入有效的视频链接。")
    url = urlsplit(value.strip())
    if url.scheme != "https" or url.netloc.lower() != url.hostname or not url.hostname:
        raise ValueError("请输入这三个平台的 HTTPS 视频链接。")
    host = url.hostname
    path = url.path.rstrip("/")
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}:
        if path == "/watch" and host != "youtu.be":
            video = parse_qs(url.query).get("v", [""])[0]
        else:
            pattern = r"/([\w-]+)" if host == "youtu.be" else r"/(?:shorts|live|embed)/([\w-]+)"
            match = re.fullmatch(pattern, path, re.ASCII)
            video = match[1] if match else ""
        if re.fullmatch(r"[A-Za-z0-9_-]{11}", video):
            return f"https://www.youtube.com/watch?v={video}"
    if host in {"x.com", "www.x.com", "twitter.com", "www.twitter.com", "mobile.twitter.com"}:
        match = re.fullmatch(r"/(?:[A-Za-z0-9_]+/status|i/(?:web/)?status)/\d+(?:/video/[1-9]\d*)?", path, re.ASCII)
        if match:
            return f"https://x.com{path}"
    if host in {"tiktok.com", "www.tiktok.com", "m.tiktok.com"}:
        if re.fullmatch(r"/@[A-Za-z0-9_.-]+/video/[0-9]+|/t/[A-Za-z0-9]+", path):
            return f"https://www.tiktok.com{path}"
    if host in {"vm.tiktok.com", "vt.tiktok.com"} and re.fullmatch(r"/[A-Za-z0-9]+", path):
        return f"https://{host}{path}"
    raise ValueError("只支持 YouTube、X 和 TikTok 的单条视频链接。")


def run_progress(command, timeout, update, env=None):
    with tempfile.TemporaryFile() as errors, subprocess.Popen(
        command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=errors,
        encoding="utf-8", errors="replace", env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ) as process:
        def read_progress():
            for line in process.stdout:
                update(line.strip())

        reader = threading.Thread(target=read_progress)
        reader.start()
        try:
            code = process.wait(timeout=timeout)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            reader.join()
        errors.seek(0)
        return subprocess.CompletedProcess(command, code, "", errors.read().decode("utf-8", errors="replace"))


def yt_dlp(*args, timeout, update=None):
    command = [
        sys.executable, "-m", "yt_dlp", "--ignore-config", "--no-cache-dir",
        "--no-playlist", "--no-progress", "--no-warnings", "--no-colors",
        "--use-extractors", "youtube.*,twitter.*,tiktok.*,vm[.]tiktok", "--proxy", PROXY,
        "--socket-timeout", "20", "--retries", "2", "--js-runtimes", "node",
    ]
    if (FFMPEG_DIR / "ffmpeg.exe").is_file() and (FFMPEG_DIR / "ffprobe.exe").is_file():
        command.extend(("--ffmpeg-location", str(FFMPEG_DIR)))
    command.extend(args)
    # Explicitly select direct/proxy routing and avoid inheriting browser or tool config.
    env = {key: value for key, value in os.environ.items() if not key.lower().endswith("_proxy")}
    env["PYTHONIOENCODING"] = "utf-8"
    if update is not None:
        return run_progress(command, timeout, update, env)
    return subprocess.run(
        command, stdin=subprocess.DEVNULL, capture_output=True, encoding="utf-8",
        errors="replace", timeout=timeout, env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def format_options(info):
    available = [
        fmt for fmt in reversed(info.get("formats") or [])
        if fmt.get("protocol") in HLS_PROTOCOLS
        and not fmt.get("has_drm")
        and re.fullmatch(r"[A-Za-z0-9_-]+", str(fmt.get("format_id", "")))
    ]
    video = []
    audio_track = next((
        fmt for fmt in available if fmt.get("vcodec") == "none" and fmt.get("acodec") != "none"
    ), None)
    for fmt in available:
        if fmt.get("vcodec") in (None, "none") or (fmt.get("height") or 0) < 1080 or (fmt.get("fps") or 0) < 30:
            continue
        ext = fmt.get("ext")
        if not isinstance(ext, str) or not re.fullmatch(r"[a-z0-9]{1,8}", ext):
            continue
        choice = {"tracks": [fmt["format_id"]], "ext": ext, "group": "视频", "duration": info.get("duration")}
        quality = fmt.get("resolution") or f'{fmt["height"]}p'
        if (fmt.get("width") or 0) >= 3840:
            quality = f"4K · {quality}"
        codec = fmt["vcodec"].split(".")[0].upper()
        quality = f'{quality} · {fmt["fps"]:g} fps · {codec} · HLS'
        if fmt.get("acodec") == "none" and audio_track:
            choice.update(tracks=[fmt["format_id"], audio_track["format_id"]], ext="mkv")
            note = " · 合并音频"
        else:
            note = {None: " · 音轨待确认", "none": " · 无音轨"}.get(fmt.get("acodec"), "")
        video.append({**choice, "label": f'{quality} · {choice["ext"].upper()}{note}'})
    return video


def inspect_video(url):
    result = yt_dlp("--skip-download", "--dump-single-json", "--", url, timeout=90)
    if result.returncode:
        raise RuntimeError(error_message(result))
    info = json.loads(result.stdout)
    if info.get("_type") in {"playlist", "multi_video"}:
        raise ValueError("此链接包含多个视频，请提供单条视频链接。")
    if info.get("live_status") in {"is_live", "is_upcoming"}:
        raise ValueError("请选择已发布的点播视频。")
    formats = format_options(info)
    if not formats:
        raise ValueError("该链接未提供达到 1080p、30 fps 的 HLS 视频。")
    return info.get("title") or "视频", formats


def error_message(result):
    lines = result.stderr.strip().splitlines()
    message = lines[-1][:650] if lines else "没有返回错误详情。"
    if PROXY:
        message = message.replace(PROXY, "[代理]")
    message = re.sub(r"https?://[^\s]+", "[链接]", message)
    return f"平台请求失败。{message}"


def prepare_download(ticket, url, fmt, title):
    directory = None
    try:
        directory = tempfile.TemporaryDirectory(prefix="local-video-")
        root = Path(directory.name)
        tracks = fmt["tracks"]
        merge = len(tracks) == 2
        template = root / ("track.%(format_id)s.%(ext)s" if merge else "media.%(ext)s")
        output = root / f'media.{fmt["ext"]}'
        progress = {}

        def report(phase, percent):
            with DOWNLOAD_LOCK:
                job = DOWNLOADS[ticket]
                previous = job.get("percent")
                job.update(phase=phase, percent=max(previous or 0, percent) if percent is not None else previous)

        def download_progress(line):
            fields = line.split()
            if len(fields) != 8 or fields[0] != "VD" or fields[1] not in tracks:
                return
            _, track, state, count, total, estimate, index, fragments = fields
            denominator = total if total.isdecimal() else estimate
            if state == "finished":
                progress[track] = 1
            elif count.isdecimal() and denominator.isdecimal() and int(denominator) > 0:
                progress[track] = min(int(count) / int(denominator), .99)
            elif index.isdecimal() and fragments.isdecimal() and int(fragments) > 0:
                progress[track] = min(int(index) / int(fragments), .99)
            else:
                return
            report("download", min(89 if merge else 99, int(sum(progress.values()) * (90 if merge else 100) / len(tracks))))

        result = yt_dlp(
            "--no-simulate", "--no-mtime", "--http-chunk-size", "8M",
            "--concurrent-fragments", "6", "--abort-on-unavailable-fragments",
            "--progress", "--newline", "--progress-delta", "0.5",
            "--progress-template",
            "download:VD %(info.format_id)s %(progress.status)s %(progress.downloaded_bytes)s "
            "%(progress.total_bytes)s %(progress.total_bytes_estimate)s %(progress.fragment_index)s %(progress.fragment_count)s",
            "-f", ",".join(track + HLS_FILTER for track in tracks), "-o", str(template),
            "--", url, timeout=1800, update=download_progress,
        )
        if result.returncode:
            raise RuntimeError(error_message(result))
        if merge:
            inputs = [list(root.glob(f"track.{track}.*")) for track in tracks]
            if any(len(paths) != 1 or not paths[0].stat().st_size for paths in inputs):
                raise RuntimeError("HLS 视频轨或音轨下载不完整。")
            report("merge", 90)

            def merge_progress(line):
                name, _, value = line.partition("=")
                duration = fmt["duration"]
                if name == "out_time_us" and value.isdecimal() and duration and duration > 0:
                    report("merge", min(99, 90 + int(int(value) / (duration * 100000))))

            ffmpeg = FFMPEG_DIR / "ffmpeg.exe"
            result = run_progress([
                str(ffmpeg) if ffmpeg.is_file() else "ffmpeg", "-nostdin", "-v", "error",
                "-progress", "pipe:1", "-i", str(inputs[0][0]), "-i", str(inputs[1][0]),
                "-map", "0:v:0", "-map", "1:a:0", "-c", "copy", str(output),
            ], 600, merge_progress)
            if result.returncode:
                raise RuntimeError("FFmpeg 无法合并视频轨和音轨。")
        if not output.is_file() or not output.stat().st_size:
            raise RuntimeError("下载组件没有生成完整的媒体文件，请重试并检查 FFmpeg。")
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title)[:96].strip(" .") or "视频"
        if re.fullmatch(r"CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³]", name.split(".")[0], re.IGNORECASE):
            name = "_" + name
        with DOWNLOAD_LOCK:
            DOWNLOADS[ticket] = {"output": output, "name": f'{name}.{fmt["ext"]}', "directory": directory}
        directory = None
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        message = str(exc) if isinstance(exc, RuntimeError) else "本地下载组件不可用或下载超时。"
        with DOWNLOAD_LOCK:
            DOWNLOADS[ticket] = {"error": message}
    finally:
        if directory is not None:
            directory.cleanup()
    expiry = threading.Timer(600, discard_download, args=(ticket,))
    expiry.daemon = True
    expiry.start()


def discard_download(ticket):
    with DOWNLOAD_LOCK:
        job = DOWNLOADS.get(ticket, {})
        if job.get("sending") or job.get("dispatching"):
            DOWNLOADS[ticket]["expired"] = True
            return
        job = DOWNLOADS.pop(ticket, {})
        if "directory" in job:
            job["directory"].cleanup()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def write_headers(self, status, kind, length, attachment=None, content_range=None):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("X-Content-Type-Options", "nosniff")
        if attachment:
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(attachment)}")
            self.send_header("Accept-Ranges", "bytes")
        if content_range:
            self.send_header("Content-Range", content_range)
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
        )
        self.end_headers()

    def send_json(self, status, obj):
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.write_headers(status, "application/json; charset=utf-8", len(data))
        self.wfile.write(data)

    def allowed_host(self):
        return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

    def do_GET(self):
        if self.allowed_host() and self.path == "/healthz":
            data = b"video-downloader-ultra"
            self.write_headers(200, "text/plain", len(data))
            self.wfile.write(data)
            return
        files = {
            "/": ("index.html", "text/html"),
            "/app.js": ("app.js", "text/javascript"),
            "/style.css": ("style.css", "text/css"),
        }
        if self.allowed_host() and self.path.startswith("/file/"):
            self.send_download(self.path.split("/")[2])
            return
        if not self.allowed_host() or self.path not in files:
            self.send_json(404, {"error": "页面不存在。"})
            return
        file, kind = files[self.path]
        data = (ROOT / file).read_bytes()
        if self.path == "/":
            data = data.replace(b"__LOCAL_TOKEN__", TOKEN.encode())
        self.write_headers(200, f"{kind}; charset=utf-8", len(data))
        self.wfile.write(data)

    def do_HEAD(self):
        if self.allowed_host() and self.path.startswith("/file/"):
            self.send_download(self.path.split("/")[2])
        else:
            self.write_headers(404, "text/plain", 0)

    def do_POST(self):
        origin = f"http://127.0.0.1:{self.server.server_port}"
        if (not self.allowed_host() or self.headers.get("Origin") != origin
                or self.headers.get("X-Local-Token") != TOKEN
                or self.headers.get_content_type() != "application/json"):
            self.send_json(403, {"error": "请求来源无效，请从本机页面操作。"})
            return
        serialized = self.path != "/api/download-status"
        if serialized and not LOCK.acquire(blocking=False):
            self.send_json(409, {"error": "已有任务正在运行，请稍后再试。"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BODY:
                raise ValueError("请求大小无效。")
            self.connection.settimeout(15)
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("请求内容无效。")
            if self.path == "/api/search":
                self.search(body)
            elif self.path == "/api/download":
                self.download(body)
            elif self.path == "/api/download-status":
                self.download_status(body)
            else:
                self.send_json(404, {"error": "接口不存在。"})
        except (ConnectionError, TimeoutError):
            pass
        except subprocess.TimeoutExpired:
            self.send_json(504, {"error": "平台响应或下载超时。"})
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.send_json(400, {"error": "无法读取请求或平台响应。"})
        except ValueError as exc:
            self.send_json(400, {"error": str(exc)})
        except RuntimeError as exc:
            self.send_json(502, {"error": str(exc)})
        except OSError:
            self.send_json(500, {"error": "本地下载组件或临时目录不可用。"})
        finally:
            if serialized:
                LOCK.release()

    def search(self, body):
        RESULTS.clear()
        url = normalize_url(body.get("url"))
        title, formats = inspect_video(url)
        public = []
        for fmt in formats:
            key = secrets.token_urlsafe(16)
            RESULTS[key] = (time.monotonic() + 1800, url, fmt, title)
            public.append({key: fmt[key] for key in ("group", "label", "ext")} | {"id": key})
        self.send_json(200, {"title": title, "formats": public})

    def download(self, body):
        key = body.get("id")
        item = RESULTS.get(key) if isinstance(key, str) else None
        if not item or item[0] <= time.monotonic():
            raise ValueError("格式列表已失效，请重新搜索。")
        _, url, fmt, title = item
        with DOWNLOAD_LOCK:
            if any(job.get("phase") for job in DOWNLOADS.values()):
                self.send_json(409, {"error": "已有视频正在准备，请稍后再试。"})
                return
            if len(DOWNLOADS) >= 4:
                self.send_json(409, {"error": "待保存文件过多，请先保存已有文件，或等待它们过期。"})
                return
            ticket = secrets.token_urlsafe(24)
            DOWNLOADS[ticket] = {"phase": "download", "percent": None}
        threading.Thread(target=prepare_download, args=(ticket, url, fmt, title), daemon=True).start()
        self.send_json(202, {"ticket": ticket})

    def download_status(self, body):
        ticket = body.get("ticket")
        with DOWNLOAD_LOCK:
            job = DOWNLOADS.get(ticket) if isinstance(ticket, str) else None
        if job is None:
            self.send_json(404, {"error": "下载已过期，请重新下载。"})
        elif "error" in job:
            discard_download(ticket)
            self.send_json(502, {"error": job["error"]})
        elif "output" in job:
            with DOWNLOAD_LOCK:
                if job.get("dispatching"):
                    self.send_json(200, {})
                    return
                sent = job.get("sent")
                if not sent:
                    job["dispatching"] = True
            if not sent:
                try:
                    name = job["name"]
                    url = f"http://127.0.0.1:{self.server.server_port}/file/{ticket}/{quote(name)}"
                    send_to_fdm(url, name)
                    job["sent"] = True
                finally:
                    with DOWNLOAD_LOCK:
                        job.pop("dispatching")
                        expired = job.get("expired")
                    if expired:
                        discard_download(ticket)
            self.send_json(200, {"sent": True, "percent": 100})
        else:
            self.send_json(200, {"phase": job["phase"], "percent": job["percent"]})

    def send_download(self, ticket):
        if self.headers.get("Sec-Fetch-Site") in {"cross-site", "same-site"}:
            self.write_headers(403, "text/plain", 0)
            return
        with DOWNLOAD_LOCK:
            job = DOWNLOADS.get(ticket, {})
            if "output" not in job or job.get("expired"):
                self.write_headers(404, "text/plain", 0)
                return
            job["sending"] = job.get("sending", 0) + 1
        try:
            with job["output"].open("rb") as source:
                size = os.fstat(source.fileno()).st_size
                start, end = 0, size - 1
                byte_range = self.headers.get("Range")
                if byte_range:
                    match = re.fullmatch(r"bytes=(\d{0,20})-(\d{0,20})", byte_range)
                    if match and (match[1] or match[2]):
                        if match[1]:
                            start = int(match[1])
                            end = min(end, int(match[2])) if match[2] else end
                        else:
                            start = max(0, size - int(match[2]))
                    if not match or not (match[1] or match[2]) or start > end:
                        self.write_headers(416, "text/plain", 0, content_range=f"bytes */{size}")
                        return
                length = end - start + 1
                self.connection.settimeout(120)
                self.write_headers(
                    206 if byte_range else 200, "application/octet-stream", length, job["name"],
                    f"bytes {start}-{end}/{size}" if byte_range else None,
                )
                if self.command == "GET":
                    source.seek(start)
                    if not byte_range:
                        shutil.copyfileobj(source, self.wfile, length=256 * 1024)
                        return
                    while length:
                        block = source.read(min(length, 256 * 1024))
                        if not block:
                            break
                        self.wfile.write(block)
                        length -= len(block)
        except OSError:
            # The response may already contain media bytes. Close it without appending JSON.
            self.close_connection = True
        finally:
            with DOWNLOAD_LOCK:
                job["sending"] -= 1
                if not job["sending"] and job.get("expired") and not job.get("dispatching"):
                    DOWNLOADS.pop(ticket)
                    job["directory"].cleanup()


def main():
    global PROXY
    parser = argparse.ArgumentParser(description="YouTube、X、TikTok 本地下载器")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    PROXY = os.environ.get("VIDEO_DOWNLOADER_PROXY", "").strip()
    if PROXY and not PROXY.startswith(("http://", "https://", "socks5://", "socks5h://")):
        parser.error("VIDEO_DOWNLOADER_PROXY 必须是 HTTP(S) 或 SOCKS5 代理地址。")
    with ThreadingHTTPServer(("127.0.0.1", args.port), Handler) as server:
        if sys.stdout is not None:
            print(f"请在 Chrome 或 Edge 打开 http://127.0.0.1:{server.server_port}/", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
