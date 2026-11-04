using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net;
using System.Threading;
using System.Windows.Forms;

internal static class Launcher
{
    private const string Url = "http://127.0.0.1:8765/";
    private static readonly string Root = Path.GetFullPath(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, ".."));

    [STAThread]
    private static void Main(string[] args)
    {
        bool watch = args.Contains("--watch");
        try
        {
            if (watch)
            {
                int session = Process.GetCurrentProcess().SessionId;
                using (var watcher = new Mutex(false, @"Local\VideoDownloaderUltra.Watcher." + session))
                {
                    if (!watcher.WaitOne(0)) return;
                    try
                    {
                        EnsureServer();
                        // Check only this Windows session and leave process auditing unchanged.
                        while (true)
                        {
                            Thread.Sleep(2000);
                            var chrome = Process.GetProcessesByName("chrome");
                            bool active = chrome.Any(process => IsInSession(process, session));
                            foreach (var process in chrome) process.Dispose();
                            if (active) EnsureServer();
                        }
                    }
                    finally { watcher.ReleaseMutex(); }
                }
            }
            else
            {
                EnsureServer();
                Process.Start(new ProcessStartInfo(Url) { UseShellExecute = true });
            }
        }
        catch (Exception error)
        {
            if (!watch)
                MessageBox.Show(error.Message, "Video Downloader Ultra", MessageBoxButtons.OK, MessageBoxIcon.Error);
            Environment.ExitCode = 1;
        }
    }

    private static bool IsInSession(Process process, int session)
    {
        try { return process.SessionId == session; }
        catch (InvalidOperationException) { return false; }
    }

    private static void EnsureServer()
    {
        using (var gate = new Mutex(false, @"Local\VideoDownloaderUltra.Server"))
        {
            try { gate.WaitOne(); }
            catch (AbandonedMutexException) { }
            try
            {
                if (Ready()) return;
                string python = Path.Combine(Root, @".venv\Scripts\pythonw.exe");
                if (!File.Exists(python)) throw new FileNotFoundException("Create the project Python environment first. See README.md.");
                using (var server = Process.Start(new ProcessStartInfo(python, "-B server.py")
                {
                    WorkingDirectory = Root,
                    UseShellExecute = false,
                    CreateNoWindow = true
                }))
                {
                    for (int i = 0; i < 40; i++)
                    {
                        if (Ready()) return;
                        if (server.HasExited) break;
                        Thread.Sleep(250);
                    }
                }
                throw new InvalidOperationException("Could not start Video Downloader Ultra on port 8765.");
            }
            finally { gate.ReleaseMutex(); }
        }
    }

    private static bool Ready()
    {
        try
        {
            var request = (HttpWebRequest)WebRequest.Create(Url + "healthz");
            request.Proxy = null;
            request.Timeout = 500;
            using (var response = request.GetResponse())
            using (var reader = new StreamReader(response.GetResponseStream()))
                return reader.ReadToEnd() == "video-downloader-ultra";
        }
        catch (WebException) { return false; }
    }
}
