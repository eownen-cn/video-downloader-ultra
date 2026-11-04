# 三平台视频下载器 Ultra

**本软件仅供技术学习与交流，禁止商用或公开推广。请仅处理你有权下载的内容，并遵守相应平台规则。**

安装后双击 `start.vbs`，或在 `Win+R` 输入 `svd` 或 `"start video downloader"`。启动器会打开 [本机页面](http://127.0.0.1:8765/)。搜索 YouTube、X 或 TikTok 的单条点播视频后，仅显示高度至少 1080 像素、帧率至少 30 fps 的 HLS 视频。独立音频入口已移除，符合条件的视频仍会配上可用的 HLS 音轨。

搜索提示会在解析结束后移除。进度条前 90% 表示分轨下载，按实际字节或分片进度估算，两轨等权。后 10% 按 FFmpeg 输出的媒体处理时间估算。对于已经含音轨的视频，下载阶段占全部进度。文件检查通过且提交给 Windows FDM 后显示 100%。FDM 的落盘进度和保存位置在 FDM 内查看。

## 重新安装

需要 Python 3.11+、当前受 yt-dlp 支持的 Node.js、FFmpeg、FFprobe，以及安装在 Program Files 下的 Windows 版 FDM。程序优先使用项目内 `tools/ffmpeg/` 中的两个 EXE，其次使用 PATH。Windows 可从 [Gyan Windows 构建页](https://www.gyan.dev/ffmpeg/builds/) 下载 Essentials ZIP 并核对 SHA-256，再将其中的 `ffmpeg.exe` 和 `ffprobe.exe` 放入该目录。

当前电脑已创建项目专用的 `.venv`。换电脑时在项目目录运行以下命令，PyPI 下载使用清华大学 TUNA 镜像。

```powershell
python -m venv .venv
& '.\.venv\Scripts\python.exe' -m pip install --index-url https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple 'yt-dlp[default,curl-cffi]==2026.10.26'
```

如果平台改版导致提取失败，可查阅 [yt-dlp 官方文档](https://github.com/yt-dlp/yt-dlp#dependencies) 核对版本与运行时要求。

## 安装或移除静默启动

在项目目录执行以下命令。安装脚本会编译 Windows 启动器，创建当前用户的两个应用别名，并注册名为 `Video Downloader Ultra` 的计划任务。登录后静默启动服务，此后每两秒检查当前会话是否有 Chrome，发现 Chrome 正在运行且服务未就绪时尝试启动服务。安装前请退出占用 8765 端口的旧版下载器。

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install-startup.ps1
```

卸载启动项和别名时执行下列命令。已启动的后台服务会继续运行，可在任务管理器结束该项目的 Python 进程。

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install-startup.ps1 -Uninstall
```

## 隐私和代理

- 网页和接口只在本机回环地址提供服务。页面无广告、统计脚本、CDN 或持久化搜索记录。应用不导入浏览器 Cookie，并移除输入链接中的分享参数。
- 最近一轮格式选项只在进程内存中，30 分钟后不能继续使用。下载先写入系统临时目录，准备完成后通过有效期 10 分钟的本机临时链接交给 FDM。传输中到期时会等连接结束再清理。强制结束进程或断电可能留下临时文件。
- 应用无痕模式仅指应用自身的记录策略。浏览器可能记录本机页面历史，FDM 可能保留下载任务和保存位置，已保存的文件会留在磁盘。
- 默认直连，平台能看到公网出口 IP。若已有可信代理，可在 PowerShell 为本次启动设置以下变量，将示例地址换成实际地址。平台会看到代理出口，代理服务商可能保留日志。

```powershell
$env:VIDEO_DOWNLOADER_PROXY = 'http://127.0.0.1:7890'
& '.\.venv\Scripts\pythonw.exe' -B server.py
```

## 本地测试

```powershell
& '.\.venv\Scripts\python.exe' -B -m unittest -v
```

测试使用替代的平台响应与真实的本地 HTTP 请求。平台解析和下载需使用可访问且允许下载的实际链接单独验证。
