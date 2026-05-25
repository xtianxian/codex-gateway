using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Management;
using System.ServiceProcess;
using System.Threading;

internal static class Program
{
    private static void Main(string[] args)
    {
        var service = new CodexGatewayService();
        if (Environment.UserInteractive || (args.Length > 0 && args[0].Equals("--console", StringComparison.OrdinalIgnoreCase)))
        {
            service.RunConsole();
            return;
        }

        ServiceBase.Run(service);
    }
}

internal sealed class CodexGatewayService : ServiceBase
{
    private const string ServiceId = "CodexGateway";
    private const string UvCommand = "uv.exe";

    private readonly ManualResetEvent stopRequested = new ManualResetEvent(false);
    private readonly object childLock = new object();
    private Process child;
    private Thread worker;
    private string repoRoot;
    private string logDir;

    public CodexGatewayService()
    {
        ServiceName = ServiceId;
        CanStop = true;
        CanShutdown = true;
    }

    public void RunConsole()
    {
        OnStart(new string[0]);
        Console.WriteLine("Codex Gateway service host running. Press Enter to stop.");
        Console.ReadLine();
        OnStop();
    }

    protected override void OnStart(string[] args)
    {
        repoRoot = ResolveRepoRoot();
        logDir = Path.Combine(repoRoot, ".codex-gateway", "logs", "service");
        Directory.CreateDirectory(logDir);
        Log("Starting Codex Gateway service host.");

        stopRequested.Reset();
        worker = new Thread(WorkerLoop);
        worker.IsBackground = true;
        worker.Start();
    }

    protected override void OnStop()
    {
        Log("Stopping Codex Gateway service host.");
        stopRequested.Set();
        StopCurrentChild();

        if (worker != null && !worker.Join(TimeSpan.FromSeconds(20)))
        {
            Log("Worker did not stop within timeout.");
        }
    }

    protected override void OnShutdown()
    {
        OnStop();
    }

    private void WorkerLoop()
    {
        StopExistingGatewayRuns();

        while (!stopRequested.WaitOne(0))
        {
            var process = StartGateway();
            var exited = process.WaitForExit(1000);
            while (!exited && !stopRequested.WaitOne(0))
            {
                exited = process.WaitForExit(1000);
            }

            lock (childLock)
            {
                if (ReferenceEquals(child, process))
                {
                    child = null;
                }
            }

            if (stopRequested.WaitOne(0))
            {
                StopProcessTree(process);
                break;
            }

            Log("Gateway child exited with code " + process.ExitCode + "; restarting in 5 seconds.");
            stopRequested.WaitOne(TimeSpan.FromSeconds(5));
        }
    }

    private Process StartGateway()
    {
        var uv = ResolveCommand(UvCommand);
        var stdoutPath = Path.Combine(logDir, "telegram-gateway.out.log");
        var stderrPath = Path.Combine(logDir, "telegram-gateway.err.log");

        var psi = new ProcessStartInfo
        {
            FileName = uv,
            Arguments = "run codex-gateway telegram run",
            WorkingDirectory = repoRoot,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true
        };

        psi.EnvironmentVariables["PYTHONUTF8"] = "1";
        psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
        psi.EnvironmentVariables["CODEX_GATEWAY_SERVICE"] = "1";

        var process = new Process { StartInfo = psi, EnableRaisingEvents = true };
        process.OutputDataReceived += delegate(object sender, DataReceivedEventArgs args)
        {
            if (args.Data != null)
            {
                AppendLine(stdoutPath, args.Data);
            }
        };
        process.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs args)
        {
            if (args.Data != null)
            {
                AppendLine(stderrPath, args.Data);
            }
        };
        process.Exited += delegate
        {
            Log("Gateway child exited: pid=" + process.Id);
        };

        process.Start();
        process.BeginOutputReadLine();
        process.BeginErrorReadLine();

        lock (childLock)
        {
            child = process;
        }

        Log("Started gateway child pid=" + process.Id + ": " + uv + " " + psi.Arguments);
        return process;
    }

    private void StopCurrentChild()
    {
        lock (childLock)
        {
            if (child != null)
            {
                StopProcessTree(child);
            }
        }
    }

    private void StopExistingGatewayRuns()
    {
        try
        {
            var allProcesses = LoadProcesses();
            var currentProcessId = Process.GetCurrentProcess().Id;
            var targetIds = new HashSet<int>();

            foreach (var item in allProcesses)
            {
                if (item.ProcessId == currentProcessId || string.IsNullOrEmpty(item.CommandLine))
                {
                    continue;
                }

                if (IsGatewayCommandLine(item.CommandLine))
                {
                    targetIds.Add(item.ProcessId);
                    AddDescendants(allProcesses, item.ProcessId, targetIds);
                }
            }

            foreach (var processId in targetIds)
            {
                KillProcess(processId);
            }

            if (targetIds.Count > 0)
            {
                Log("Stopped stale gateway process ids: " + string.Join(",", targetIds));
            }
        }
        catch (Exception ex)
        {
            Log("Failed to stop stale gateway processes: " + ex.Message);
        }
    }

    private bool IsGatewayCommandLine(string commandLine)
    {
        if (commandLine.IndexOf(repoRoot, StringComparison.OrdinalIgnoreCase) < 0)
        {
            return false;
        }

        if (commandLine.IndexOf("start-gateway.ps1", StringComparison.OrdinalIgnoreCase) >= 0 ||
            commandLine.IndexOf("start-gateway.vbs", StringComparison.OrdinalIgnoreCase) >= 0)
        {
            return true;
        }

        return commandLine.IndexOf("codex-gateway", StringComparison.OrdinalIgnoreCase) >= 0 &&
            commandLine.IndexOf("telegram", StringComparison.OrdinalIgnoreCase) >= 0 &&
            commandLine.IndexOf("run", StringComparison.OrdinalIgnoreCase) >= 0;
    }

    private static List<ProcessInfo> LoadProcesses()
    {
        var result = new List<ProcessInfo>();
        using (var searcher = new ManagementObjectSearcher("SELECT ProcessId, ParentProcessId, CommandLine FROM Win32_Process"))
        {
            foreach (ManagementObject process in searcher.Get())
            {
                result.Add(new ProcessInfo
                {
                    ProcessId = Convert.ToInt32(process["ProcessId"]),
                    ParentProcessId = Convert.ToInt32(process["ParentProcessId"]),
                    CommandLine = process["CommandLine"] as string
                });
            }
        }

        return result;
    }

    private static void AddDescendants(IEnumerable<ProcessInfo> processes, int rootProcessId, HashSet<int> targetIds)
    {
        foreach (var childProcess in processes)
        {
            if (childProcess.ParentProcessId == rootProcessId && targetIds.Add(childProcess.ProcessId))
            {
                AddDescendants(processes, childProcess.ProcessId, targetIds);
            }
        }
    }

    private static void KillProcess(int processId)
    {
        try
        {
            var process = Process.GetProcessById(processId);
            process.Kill();
        }
        catch
        {
        }
    }

    private void StopProcessTree(Process process)
    {
        try
        {
            if (process.HasExited)
            {
                return;
            }

            var taskkill = new ProcessStartInfo
            {
                FileName = "taskkill.exe",
                Arguments = "/PID " + process.Id + " /T /F",
                UseShellExecute = false,
                CreateNoWindow = true
            };

            using (var killer = Process.Start(taskkill))
            {
                if (killer != null)
                {
                    killer.WaitForExit(10000);
                }
            }
        }
        catch (Exception ex)
        {
            Log("Failed to stop gateway child process pid=" + process.Id + ": " + ex.Message);
        }
    }

    private static string ResolveRepoRoot()
    {
        var serviceDir = AppDomain.CurrentDomain.BaseDirectory;
        return Path.GetFullPath(Path.Combine(serviceDir, "..", ".."));
    }

    private static string ResolveCommand(string command)
    {
        if (Path.IsPathRooted(command))
        {
            if (!File.Exists(command))
            {
                throw new FileNotFoundException("Required executable was not found.", command);
            }

            return command;
        }

        var pathValue = Environment.GetEnvironmentVariable("PATH") ?? string.Empty;
        foreach (var rawDirectory in pathValue.Split(Path.PathSeparator))
        {
            var directory = (rawDirectory ?? string.Empty).Trim();
            if (directory.Length == 0)
            {
                continue;
            }

            var expandedDirectory = Environment.ExpandEnvironmentVariables(directory.Trim('"'));
            var candidate = Path.Combine(expandedDirectory, command);
            if (File.Exists(candidate))
            {
                return candidate;
            }
        }

        throw new FileNotFoundException("Required command was not found on PATH.", command);
    }

    private void Log(string message)
    {
        AppendLine(Path.Combine(logDir ?? Path.Combine(ResolveRepoRoot(), ".codex-gateway", "logs", "service"), "codex-gateway-service.log"), DateTime.Now.ToString("o") + " " + message);
    }

    private static void AppendLine(string path, string line)
    {
        var directory = Path.GetDirectoryName(path);
        if (!string.IsNullOrEmpty(directory))
        {
            Directory.CreateDirectory(directory);
        }

        File.AppendAllText(path, line + Environment.NewLine);
    }

    private sealed class ProcessInfo
    {
        public int ProcessId;
        public int ParentProcessId;
        public string CommandLine;
    }
}
