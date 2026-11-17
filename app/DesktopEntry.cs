using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

internal static class DesktopEntry
{
    [STAThread]
    private static void Main()
    {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string script = Path.Combine(root, "app", "desktop.pyw");
        try
        {
            if (!File.Exists(script))
                throw new FileNotFoundException("找不到启动器文件。请从软件原目录打开。", script);
            var start = new ProcessStartInfo
            {
                FileName = Environment.GetEnvironmentVariable("STARCRAFT_PYTHONW") ?? "pythonw.exe",
                Arguments = "-B \"" + script + "\"",
                WorkingDirectory = root,
                UseShellExecute = false,
                CreateNoWindow = true
            };
            using (Process child = Process.Start(start)) { }
        }
        catch (Exception error)
        {
            MessageBox.Show(error.Message, "StarCraft 指挥台", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }
}
