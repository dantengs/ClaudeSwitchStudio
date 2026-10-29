using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Threading;
using System.Windows.Forms;

[assembly: AssemblyTitle("Claude Switch Studio")]
[assembly: AssemblyDescription("Local account and conversation workspace")]
[assembly: AssemblyProduct("Claude Switch Studio")]
[assembly: AssemblyVersion("0.4.0.0")]
[assembly: AssemblyFileVersion("0.4.0.0")]
[assembly: AssemblyInformationalVersion("0.4.0-preview")]
[assembly: AssemblyCopyright("Copyright (c) 2026 Claude Switch Studio contributors")]

internal static class Program
{
    [STAThread]
    private static int Main(string[] args)
    {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        if (args.Length == 1 && args[0] == "--self-check") return SelfCheck(root);
        if (args.Length == 1 && args[0] == "--exit") return RequestExit();
        // --tray starts hidden in the notification area; Windows autostart uses it.
        string mode = args.Length == 1 ? args[0] : "";
        if (args.Length > 1 || (args.Length == 1 && mode != "--read-only" && mode != "--tray"))
        {
            MessageBox.Show("不支持此启动参数。请直接打开程序。", "Claude Switch Studio");
            return 2;
        }
        bool created;
        using (Mutex instance = new Mutex(true, "ClaudeSwitchStudio.Runtime", out created))
        {
            if (!created)
            {
                // The running window listens for this event and brings itself to the front.
                EventWaitHandle show;
                if (EventWaitHandle.TryOpenExisting("ClaudeSwitchStudio.Show", out show))
                {
                    using (show) { if (mode != "--tray") show.Set(); }
                    return 0;
                }
                MessageBox.Show("程序已在运行，请在任务栏或托盘中打开。", "Claude Switch Studio");
                return 0;
            }
            try
            {
                string script = Path.Combine(root, "Studio.ps1");
                string powershell = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Windows),
                    @"System32\WindowsPowerShell\v1.0\powershell.exe");
                if (!File.Exists(script) || !File.Exists(powershell)) throw new FileNotFoundException("程序文件不完整，请重新安装。");
                ProcessStartInfo start = new ProcessStartInfo(powershell);
                start.Arguments = "-NoProfile -ExecutionPolicy Bypass -STA -WindowStyle Hidden -File \"" + script + "\"" +
                    (mode == "--read-only" ? " -ReadOnly" : mode == "--tray" ? " -Tray" : "");
                start.WorkingDirectory = root;
                start.UseShellExecute = false;
                start.CreateNoWindow = true;
                start.WindowStyle = ProcessWindowStyle.Hidden;
                using (Process child = Process.Start(start))
                {
                    child.WaitForExit();
                    if (child.ExitCode != 0)
                        MessageBox.Show("启动未完成。请查看程序目录 data 中的启动记录，或重新安装。", "Claude Switch Studio");
                    return child.ExitCode;
                }
            }
            catch (Exception error)
            {
                MessageBox.Show(error.Message, "Claude Switch Studio", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return 1;
            }
            finally { instance.ReleaseMutex(); }
        }
    }

    // Asks a running window to quit the same way as the tray's exit item (it refuses while busy).
    // Returns 0 once no instance holds the runtime lock, 3 if it is still running after 15 seconds.
    private static int RequestExit()
    {
        EventWaitHandle exit;
        if (!EventWaitHandle.TryOpenExisting("ClaudeSwitchStudio.Exit", out exit)) return 0;
        using (exit) exit.Set();
        for (int i = 0; i < 60; i++)
        {
            Mutex running;
            if (!Mutex.TryOpenExisting("ClaudeSwitchStudio.Runtime", out running)) return 0;
            running.Dispose();
            Thread.Sleep(250);
        }
        return 3;
    }

    // Side-effect-free packaging check; never starts the UI, Claude, or an auth action.
    private static int SelfCheck(string root)
    {
        foreach (string relative in new string[] {"Studio.ps1", "Studio.xaml", "studio-core.ps1",
            "claude-switch-core.exe", "local_adapter.py", @"runtime\python\python.exe", @"assets\app.ico", @"assets\app.png"})
            if (!File.Exists(Path.Combine(root, relative))) return 10;
        return 0;
    }
}
