using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Threading;
using System.Windows.Forms;

[assembly: AssemblyTitle("Claude Switch Studio")]
[assembly: AssemblyDescription("Local account and conversation workspace")]
[assembly: AssemblyProduct("Claude Switch Studio")]
[assembly: AssemblyVersion("0.3.1.0")]
[assembly: AssemblyFileVersion("0.3.1.0")]
[assembly: AssemblyCopyright("Copyright (c) 2026 Claude Switch Studio contributors")]

internal static class Program
{
    [STAThread]
    private static int Main(string[] args)
    {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        if (args.Length == 1 && args[0] == "--self-check") return SelfCheck(root);
        if (args.Length > 0 && !(args.Length == 1 && args[0] == "--read-only"))
        {
            MessageBox.Show("不支持此启动参数。请直接打开程序。", "Claude Switch Studio");
            return 2;
        }
        bool created;
        using (Mutex instance = new Mutex(true, "ClaudeSwitchStudio.Runtime", out created))
        {
            if (!created)
            {
                MessageBox.Show("程序已在运行，请使用现有窗口。", "Claude Switch Studio");
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
                    (args.Length == 1 ? " -ReadOnly" : "");
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

    // Side-effect-free packaging check; never starts the UI, Claude, or an auth action.
    private static int SelfCheck(string root)
    {
        foreach (string relative in new string[] {"Studio.ps1", "Studio.xaml", "studio-core.ps1",
            "claude-switch-core.exe", "local_adapter.py", @"runtime\python\python.exe", @"assets\app.ico", @"assets\app.png"})
            if (!File.Exists(Path.Combine(root, relative))) return 10;
        return 0;
    }
}
