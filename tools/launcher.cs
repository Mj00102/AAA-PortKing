// ============================================================
//  AAA端口大王 单文件自解压启动器
// ============================================================
//  原理: 程序包 (zip) 被追加在本 exe 文件末尾 (标记 PAYLOAD_MARKER 之后)。
//        启动时解压到可写目录, 之后直接启动, 不再重复解压。
//
//  为什么不用 PyInstaller 的 --onefile:
//        --onefile 每次启动都要把自己解包到 %TEMP%, 在临时目录受限的机器上
//        会直接弹 "Could not create temporary directory!" 而打不开
//        (实测连 20 行的 hello-world onefile 都同样报错)。
//        本启动器解压到 exe 同目录, 完全不碰 %TEMP%, 彻底绕开该问题。
//
//  解压目录按优先级实测可写性: 用户 AppData -> exe 所在目录 -> 当前目录
// ============================================================
using System;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Windows.Forms;

static class PortKingLauncher
{
    // 必须与 tools/make_onefile.py 里的标记完全一致
    const string PAYLOAD_MARKER = "PORT_KING_PAYLOAD_V1:";

    [STAThread]
    static int Main(string[] args)
    {
        // 调试日志: 只在用 /define:PPLOG 编译时生效 (正式版不写任何文件)
        string logPath = Path.Combine(
            Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location) ?? ".",
            "launcher.log");
        try
        {
            Log(logPath, "=== start ===");
            string exe = Assembly.GetExecutingAssembly().Location;
            Log(logPath, "exe = " + exe);

            long offset;
            if (!FindPayload(exe, out offset))
            {
                Log(logPath, "FindPayload FAILED");
                Fail("程序包损坏: 找不到内嵌数据。\r\n请重新下载完整的 exe。");
                return 2;
            }
            Log(logPath, "payload offset = " + offset);

            string target = PickTarget(exe);
            Log(logPath, "target = " + target);
            if (target == null)
            {
                Fail("找不到可写的解压目录。\r\n已尝试: 用户 AppData、exe 所在目录、当前目录。\r\n"
                     + "请把本程序放到有写入权限的位置重试。");
                return 3;
            }

            string stamp = Path.Combine(target, ".version");
            string want = PayloadHash(exe, offset);    // 用负载哈希当版本指纹
            string app = Path.Combine(target, "PortKing.exe");
            Log(logPath, "hash = " + want);

            bool need = !File.Exists(app) || !File.Exists(stamp) || ReadText(stamp) != want;
            Log(logPath, "needExtract = " + need);
            if (need)
            {
                if (Directory.Exists(target)) TryDelete(target);
                Directory.CreateDirectory(target);
                Extract(exe, offset, target);
                File.WriteAllText(stamp, want);
                Log(logPath, "extract done");
            }

            var psi = new ProcessStartInfo(app)
            {
                UseShellExecute = true,
                WorkingDirectory = target,
            };
            if (args.Length > 0) psi.Arguments = string.Join(" ", args);
            Process.Start(psi);
            Log(logPath, "launched ok");
            return 0;
        }
        catch (Exception ex)
        {
            Log(logPath, "EXCEPTION: " + ex);
            Fail("启动失败:\r\n" + ex.Message);
            return 1;
        }
    }

    /// <summary>
    /// 挑一个真正可写的解压目录: 用户 AppData -> exe 所在目录 -> 当前目录。
    /// 不能假设 AppData 一定可写 (受限账户/沙箱/只读介质都可能被拒),
    /// 所以每个候选都实测一次"建目录 + 写文件"。
    /// </summary>
    static string PickTarget(string exe)
    {
        var cands = new System.Collections.Generic.List<string>();
        try
        {
            cands.Add(Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "PortKing"));
        }
        catch { }
        try { cands.Add(Path.Combine(Path.GetDirectoryName(exe), "PortKing-data")); }
        catch { }
        try { cands.Add(Path.Combine(Directory.GetCurrentDirectory(), "PortKing-data")); }
        catch { }

        foreach (string dir in cands)
        {
            if (string.IsNullOrEmpty(dir)) continue;
            try
            {
                Directory.CreateDirectory(dir);
                string probe = Path.Combine(dir, ".write-probe");
                File.WriteAllText(probe, "ok");
                File.Delete(probe);
                return dir;
            }
            catch { }
        }
        return null;
    }

    static string ReadText(string path)
    {
        try { return File.ReadAllText(path).Trim(); }
        catch { return ""; }
    }

    static void TryDelete(string dir)
    {
        for (int i = 0; i < 3; i++)
        {
            try { Directory.Delete(dir, true); return; }
            catch { Thread.Sleep(300); }
        }
    }

    static string PayloadHash(string exe, long offset)
    {
        try
        {
            using (var fs = new FileStream(exe, FileMode.Open, FileAccess.Read, FileShare.Read))
            using (var sha = SHA1.Create())
            {
                fs.Seek(offset, SeekOrigin.Begin);
                byte[] buf = new byte[1 << 16];
                int n;
                while ((n = fs.Read(buf, 0, buf.Length)) > 0) sha.TransformBlock(buf, 0, n, null, 0);
                sha.TransformFinalBlock(new byte[0], 0, 0);
                return BitConverter.ToString(sha.Hash).Replace("-", "");
            }
        }
        catch { return offset.ToString(); }
    }

    /// <summary>
    /// 找到内嵌 zip 的起始偏移: 先定位标记字符串, 再从标记之后找 ZIP 魔数 (PK\3\4)。
    /// 不能直接用"标记位置之后的那个字节" —— 标记字符串在编译后的 exe 里
    /// 未必正好落在文件末尾, 所以必须用魔数精确定位。
    /// 另外 C# 字符串字面量在 exe 里是 UTF-16LE 存储, 必须用 Unicode 编码去搜。
    /// </summary>
    static bool FindPayload(string exe, out long offset)
    {
        offset = -1;
        byte[] marker = Encoding.Unicode.GetBytes(PAYLOAD_MARKER);
        using (var fs = new FileStream(exe, FileMode.Open, FileAccess.Read, FileShare.Read))
        {
            long len = fs.Length;
            int window = (int)Math.Min(len, 8 * 1024 * 1024);
            byte[] buf = new byte[window];
            int got = 0;
            while (got < window)
            {
                int n = fs.Read(buf, got, window - got);
                if (n <= 0) break;
                got += n;
            }

            int markAt = -1;
            for (int i = 0; i <= got - marker.Length; i++)
            {
                bool hit = true;
                for (int j = 0; j < marker.Length; j++)
                {
                    if (buf[i + j] != marker[j]) { hit = false; break; }
                }
                if (hit) { markAt = i; break; }
            }
            if (markAt < 0) return false;

            for (int i = markAt + marker.Length; i + 4 <= got; i++)
            {
                if (buf[i] == 0x50 && buf[i + 1] == 0x4B && buf[i + 2] == 0x03 && buf[i + 3] == 0x04)
                {
                    offset = i;
                    return true;
                }
            }
        }
        return false;
    }

    static void Extract(string exe, long offset, string target)
    {
        using (var fs = new FileStream(exe, FileMode.Open, FileAccess.Read, FileShare.Read))
        {
            fs.Seek(offset, SeekOrigin.Begin);
            long zipLen = fs.Length - offset;
            byte[] payload = new byte[zipLen];
            int read = 0;
            while (read < payload.Length)
            {
                int n = fs.Read(payload, read, payload.Length - read);
                if (n <= 0) break;
                read += n;
            }

            using (var ms = new MemoryStream(payload, 0, read))
            using (var zip = new ZipArchive(ms, ZipArchiveMode.Read))
            {
                foreach (var e in zip.Entries)
                {
                    string outPath = Path.Combine(target, e.FullName.Replace('/', Path.DirectorySeparatorChar));
                    if (e.FullName.EndsWith("/"))          // 目录项
                    {
                        Directory.CreateDirectory(outPath);
                        continue;
                    }
                    Directory.CreateDirectory(Path.GetDirectoryName(outPath));
                    // 手动拷贝: .NET Framework 的 ZipArchive 没有 ExtractToFile
                    using (var es = e.Open())
                    using (var os = new FileStream(outPath, FileMode.Create, FileAccess.Write))
                    {
                        byte[] buf = new byte[1 << 16];
                        int n;
                        while ((n = es.Read(buf, 0, buf.Length)) > 0) os.Write(buf, 0, n);
                    }
                }
            }
        }
    }

    static void Log(string path, string msg)
    {
#if PPLOG
        try
        {
            File.AppendAllText(path,
                DateTime.Now.ToString("HH:mm:ss.fff") + "  " + msg + "\r\n", Encoding.UTF8);
        }
        catch { }
#endif
    }

    static void Fail(string msg)
    {
        MessageBox.Show(msg, "AAA端口大王", MessageBoxButtons.OK, MessageBoxIcon.Error);
    }
}
