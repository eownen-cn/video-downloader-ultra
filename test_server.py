import http.client
import json
import re
import subprocess
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class DownloaderTests(unittest.TestCase):
    def test_normalizes_supported_links(self):
        examples = {
            "https://youtu.be/aqz-KE-bpKQ?si=tracking": "https://www.youtube.com/watch?v=aqz-KE-bpKQ",
            "https://youtube.com/shorts/aqz-KE-bpKQ?feature=share": "https://www.youtube.com/watch?v=aqz-KE-bpKQ",
            "https://twitter.com/example/status/123/video/2?s=20": "https://x.com/example/status/123/video/2",
            "https://www.tiktok.com/@example/video/123?_r=1": "https://www.tiktok.com/@example/video/123",
            "https://vm.tiktok.com/ZMabc/?share=1": "https://vm.tiktok.com/ZMabc",
        }
        for original, normalized in examples.items():
            with self.subTest(original=original):
                self.assertEqual(server.normalize_url(original), normalized)

    def test_rejects_non_video_urls(self):
        for url in (
            "https://youtube.com.evil.test/watch?v=aqz-KE-bpKQ",
            "https://user@x.com/example/status/123",
            "http://x.com/example/status/123",
            "https://www.youtube.com/playlist?list=PL123",
            "https://www.tiktok.com/@example",
            "https://127.0.0.1:1234/",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                server.normalize_url(url)

    def test_filters_video_by_height_frame_rate_and_hls(self):
        base = {"ext": "mp4", "vcodec": "h264", "acodec": "aac", "height": 1080, "fps": 30, "protocol": "m3u8_native"}
        options = server.format_options({"formats": [
            base | {"format_id": "at-limit"},
            base | {"format_id": "crop", "width": 1920, "height": 1012},
            base | {"format_id": "low-fps", "fps": 29.97},
            base | {"format_id": "unknown-fps", "fps": None},
            base | {"format_id": "direct", "protocol": "https"},
            base | {"format_id": "audio", "vcodec": "none"},
        ]})
        self.assertEqual([item["tracks"] for item in options], [["at-limit"]])

    def test_unknown_audio_is_distinct_from_silent_video(self):
        format_info = {"format_id": "hls-1080", "ext": "mp4", "vcodec": "h264", "height": 1080, "fps": 30, "protocol": "m3u8_native"}
        unknown = server.format_options({"formats": [format_info]})
        self.assertEqual([item["group"] for item in unknown], ["视频"])
        self.assertIn("音轨待确认", unknown[0]["label"])
        silent = server.format_options({"formats": [format_info | {"acodec": "none"}]})
        self.assertEqual(len(silent), 1)
        self.assertIn("无音轨", silent[0]["label"])

    def test_http_search_download_and_cross_origin_rejection(self):
        video = b"sample download bytes"
        output_dirs = []
        download_reported = threading.Event()
        continue_download = threading.Event()
        merge_reported = threading.Event()
        continue_merge = threading.Event()
        info = {"title": "示例视频", "duration": 10, "formats": [
            {"format_id": "audio", "ext": "m4a", "vcodec": "none", "protocol": "m3u8_native", "abr": 128},
            {"format_id": "hd", "ext": "mp4", "vcodec": "h264", "acodec": "none", "height": 1080, "fps": 30, "protocol": "m3u8_native"},
            {"format_id": "direct", "ext": "mp4", "vcodec": "av01", "acodec": "aac", "height": 2160, "protocol": "https"},
            {"format_id": "locked", "ext": "mp4", "has_drm": True, "vcodec": "h264"},
        ]}

        def fake_yt_dlp(*args, timeout, update=None):
            self.assertEqual(args[-1], "https://www.youtube.com/watch?v=aqz-KE-bpKQ")
            if "--dump-single-json" in args:
                return subprocess.CompletedProcess(args, 0, json.dumps(info), "")
            self.assertIn("hd[protocol^=m3u8],audio[protocol^=m3u8]", args)
            update("VD hd downloading 250 500 NA NA NA")
            download_reported.set()
            self.assertTrue(continue_download.wait(5))
            template = Path(args[args.index("-o") + 1])
            output_dirs.append(template.parent)
            (template.parent / "track.hd.mp4").write_bytes(video)
            (template.parent / "track.audio.m4a").write_bytes(video)
            return subprocess.CompletedProcess(args, 0, "", "")

        def fake_ffmpeg(command, timeout, update):
            update("out_time_us=5000000")
            merge_reported.set()
            self.assertTrue(continue_merge.wait(5))
            Path(command[-1]).write_bytes(video)
            return subprocess.CompletedProcess(command, 0, "", "")

        with (
            patch.object(server, "yt_dlp", side_effect=fake_yt_dlp),
            patch.object(server, "run_progress", side_effect=fake_ffmpeg),
            patch.object(server, "send_to_fdm") as fdm,
        ):
            with server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler) as httpd:
                thread = threading.Thread(target=httpd.serve_forever)
                thread.start()
                conn = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=5)
                try:
                    conn.request("GET", "/")
                    response = conn.getresponse()
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.headers["Cache-Control"], "no-store")
                    page = response.read().decode()
                    token = re.search(r'name="local-token" content="([^"]+)"', page)[1]
                    headers = {
                        "Content-Type": "application/json", "X-Local-Token": token,
                        "Origin": f"http://127.0.0.1:{httpd.server_port}",
                    }
                    query = json.dumps({"url": "https://youtu.be/aqz-KE-bpKQ?si=test"})
                    conn.request("POST", "/api/search", query, headers | {"Origin": "https://example.com"})
                    response = conn.getresponse()
                    self.assertEqual(response.status, 403)
                    response.read()

                    conn.request("POST", "/api/search", query, headers)
                    response = conn.getresponse()
                    self.assertEqual(response.status, 200)
                    data = json.loads(response.read())
                    self.assertEqual(data["title"], "示例视频")
                    self.assertEqual([item["ext"] for item in data["formats"]], ["mkv"])
                    self.assertNotIn("tracks", data["formats"][0])

                    conn.request("POST", "/api/download", json.dumps({"id": data["formats"][0]["id"]}), headers)
                    response = conn.getresponse()
                    self.assertEqual(response.status, 202)
                    job = json.loads(response.read())
                    for signal, release, expected in (
                        (download_reported, continue_download, {"phase": "download", "percent": 22}),
                        (merge_reported, continue_merge, {"phase": "merge", "percent": 95}),
                    ):
                        self.assertTrue(signal.wait(5))
                        conn.request("POST", "/api/download-status", json.dumps(job), headers)
                        response = conn.getresponse()
                        self.assertEqual(json.loads(response.read()), expected)
                        release.set()
                    for _ in range(100):
                        conn.request("POST", "/api/download-status", json.dumps(job), headers)
                        response = conn.getresponse()
                        self.assertEqual(response.status, 200)
                        if json.loads(response.read()).get("sent"):
                            break
                        time.sleep(0.01)
                    fdm.assert_called_once()
                    url = "/" + fdm.call_args.args[0].split("/", 3)[3]
                    conn.request("POST", "/api/download-status", json.dumps(job), headers)
                    response = conn.getresponse()
                    self.assertEqual(json.loads(response.read()), {"sent": True, "percent": 100})
                    fdm.assert_called_once()

                    conn.request("HEAD", url)
                    response = conn.getresponse()
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.headers["Accept-Ranges"], "bytes")
                    self.assertEqual(response.read(), b"")

                    conn.request("GET", url, headers={"Range": "bytes=2-5"})
                    response = conn.getresponse()
                    self.assertEqual(response.status, 206)
                    self.assertEqual(response.headers["Content-Range"], f"bytes 2-5/{len(video)}")
                    self.assertEqual(response.read(), video[2:6])

                    conn.request("GET", url, headers={"Sec-Fetch-Site": "cross-site"})
                    response = conn.getresponse()
                    self.assertEqual(response.status, 403)
                    response.read()

                    browser = {"Sec-Fetch-Site": "same-origin"}
                    with patch.object(server.shutil, "copyfileobj", side_effect=ConnectionAbortedError):
                        conn.request("GET", url, headers=browser)
                        response = conn.getresponse()
                        self.assertEqual(response.status, 200)
                        with self.assertRaises(http.client.IncompleteRead) as interrupted:
                            response.read()
                        self.assertEqual(interrupted.exception.partial, b"")

                    conn.request("GET", url, headers=browser)
                    response = conn.getresponse()
                    self.assertEqual(response.status, 200)
                    self.assertEqual(int(response.headers["Content-Length"]), len(video))
                    self.assertTrue(response.headers["Content-Disposition"].endswith(".mkv"))
                    self.assertEqual(response.read(), video)
                finally:
                    if "job" in locals():
                        server.discard_download(job["ticket"])
                    conn.close()
                    httpd.shutdown()
                    thread.join()
        self.assertTrue(output_dirs)
        self.assertFalse(output_dirs[0].exists())


if __name__ == "__main__":
    unittest.main()
