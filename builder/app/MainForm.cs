using System.Diagnostics;
using System.Text.Json;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace Smo2pc.Builder;

internal sealed class MainForm : Form
{
    private readonly WebView2 _web = new() { Dock = DockStyle.Fill };
    private string? _repoRoot;
    private bool _buildRunning;

    public MainForm()
    {
        Text = "SMO2PC Builder";
        Width = 1000;
        Height = 900;
        MinimumSize = new Size(780, 700);
        BackColor = Color.FromArgb(17, 21, 27);
        Controls.Add(_web);
        Shown += InitializeWebView;
    }

    private async void InitializeWebView(object? sender, EventArgs e)
    {
        try
        {
            await _web.EnsureCoreWebView2Async();
            _web.CoreWebView2.Settings.AreDefaultContextMenusEnabled = false;
            _web.CoreWebView2.Settings.AreDevToolsEnabled = false;
            _web.CoreWebView2.WebMessageReceived += OnWebMessage;
            var uiRoot = Path.Combine(AppContext.BaseDirectory, "ui", "dist");
            if (!File.Exists(Path.Combine(uiRoot, "index.html")))
                throw new FileNotFoundException("The bundled React interface is missing.", uiRoot);
            _web.CoreWebView2.SetVirtualHostNameToFolderMapping(
                "smo2pc.local", uiRoot, CoreWebView2HostResourceAccessKind.DenyCors);
            _web.Source = new Uri("https://smo2pc.local/index.html");
        }
        catch (Exception ex)
        {
            MessageBox.Show(this,
                $"The builder could not start its interface. Install the Microsoft Edge WebView2 Runtime and try again.\n\n{ex.Message}",
                "SMO2PC Builder", MessageBoxButtons.OK, MessageBoxIcon.Error);
            Close();
        }
    }

    private async void OnWebMessage(object? sender, CoreWebView2WebMessageReceivedEventArgs e)
    {
        try
        {
            using var json = JsonDocument.Parse(e.WebMessageAsJson);
            var action = json.RootElement.GetProperty("action").GetString();
            if (action == "pick")
            {
                ChooseFile(json.RootElement.GetProperty("field").GetString() ?? "");
                return;
            }
            if (action == "build")
                await BuildAsync(json.RootElement.GetProperty("files"));
        }
        catch (Exception ex)
        {
            _buildRunning = false;
            Send(new { type = "error", text = ex.Message });
        }
    }

    private void ChooseFile(string field)
    {
        var (title, filter) = field switch
        {
            "prodKeys" => ("Choose your prod.keys file", "prod.keys|prod.keys|Key files|*.keys|All files|*.*"),
            "titleKeys" => ("Choose your title.keys file", "title.keys|title.keys|Key files|*.keys|All files|*.*"),
            "base" => ("Choose your base game package", "Nintendo packages|*.nsp;*.xci|NSP files|*.nsp|XCI files|*.xci"),
            "update" => ("Choose your 1.4.1 update package", "Nintendo packages|*.nsp;*.xci|NSP files|*.nsp|XCI files|*.xci"),
            _ => throw new InvalidOperationException("Unknown file selection.")
        };
        using var dialog = new OpenFileDialog { Title = title, Filter = filter, CheckFileExists = true };
        if (dialog.ShowDialog(this) == DialogResult.OK)
            Send(new { type = "picked", field, path = dialog.FileName });
    }

    private async Task BuildAsync(JsonElement files)
    {
        if (_buildRunning)
            return;
        var prodKeys = GetPath(files, "prodKeys");
        var titleKeys = GetPath(files, "titleKeys");
        var basePackage = GetPath(files, "base");
        var updatePackage = GetPath(files, "update");
        if (string.IsNullOrWhiteSpace(prodKeys) || string.IsNullOrWhiteSpace(basePackage) || string.IsNullOrWhiteSpace(updatePackage))
            throw new InvalidOperationException("Choose prod.keys, the base game package, and the 1.4.1 update package first.");
        if (!File.Exists(prodKeys) || !File.Exists(basePackage) || !File.Exists(updatePackage) ||
            (!string.IsNullOrEmpty(titleKeys) && !File.Exists(titleKeys)))
            throw new FileNotFoundException("One or more selected files can no longer be found. Choose them again.");
        if (!IsPackage(basePackage) || !IsPackage(updatePackage))
            throw new InvalidOperationException("The base game and update must each be an NSP or XCI file.");

        _repoRoot = FindRepositoryRoot();
        if (_repoRoot is null)
            throw new DirectoryNotFoundException("Could not find scripts/setup.py beside this builder. Extract the complete Windows builder package before launching it.");

        _buildRunning = true;
        Send(new { type = "log", text = "Checking the local Python environment…" });
        var python = await EnsurePythonAsync(_repoRoot);
        if (python is null)
        {
            _buildRunning = false;
            Send(new { type = "buildFinished", success = false, text = "Python 3.13 was not found. Install Python 3.13 and the Python launcher, then try again." });
            return;
        }

        var arguments = new List<string>
        {
            Path.Combine(_repoRoot, "scripts", "setup.py"),
            "--base", basePackage,
            "--update", updatePackage,
            "--keys", prodKeys
        };
        if (!string.IsNullOrWhiteSpace(titleKeys))
        {
            arguments.Add("--title-keys");
            arguments.Add(titleKeys);
        }

        var exitCode = await RunProcessAsync(python, arguments, _repoRoot);
        var exePath = Path.Combine(_repoRoot, "build", "bin", "smo_native.exe");
        var success = exitCode == 0 && File.Exists(exePath);
        if (success)
            Send(new { type = "buildFinished", success = true, exePath, text = $"Build complete: {exePath}" });
        else
            Send(new { type = "buildFinished", success = false, exePath = (string?)null, text = $"Build stopped with exit code {exitCode}. Review the output above, fix the missing requirement, and run Build again; completed steps resume." });
        _buildRunning = false;
    }

    private static string GetPath(JsonElement files, string name) => files.TryGetProperty(name, out var value) ? value.GetString() ?? "" : "";

    private static bool IsPackage(string path) =>
        Path.GetExtension(path).Equals(".nsp", StringComparison.OrdinalIgnoreCase) ||
        Path.GetExtension(path).Equals(".xci", StringComparison.OrdinalIgnoreCase);

    private static string? FindRepositoryRoot()
    {
        var configured = Environment.GetEnvironmentVariable("SMO2PC_REPO_ROOT");
        if (!string.IsNullOrWhiteSpace(configured) && File.Exists(Path.Combine(configured, "scripts", "setup.py")))
            return Path.GetFullPath(configured);
        for (var directory = new DirectoryInfo(AppContext.BaseDirectory); directory is not null; directory = directory.Parent)
            if (File.Exists(Path.Combine(directory.FullName, "scripts", "setup.py")))
                return directory.FullName;
        return null;
    }

    private async Task<string?> EnsurePythonAsync(string root)
    {
        var venvPython = Path.Combine(root, ".venv", "Scripts", "python.exe");
        if (File.Exists(venvPython))
            return venvPython;

        var venv = Path.Combine(root, ".venv");
        Send(new { type = "log", text = "Creating the project’s Python 3.13 environment…" });
        var createExit = await RunProcessAsync("py.exe", new[] { "-3.13", "-m", "venv", venv }, root, reportOutput: true);
        if (createExit != 0 || !File.Exists(venvPython))
            return null;

        Send(new { type = "log", text = "Installing Python build packages into .venv…" });
        var installExit = await RunProcessAsync(venvPython,
            new[] { "-m", "pip", "install", "lz4", "capstone", "unicorn", "numpy", "Pillow" }, root);
        return installExit == 0 ? venvPython : null;
    }

    private async Task<int> RunProcessAsync(string executable, IEnumerable<string> arguments, string workingDirectory, bool reportOutput = true)
    {
        var start = new ProcessStartInfo(executable)
        {
            WorkingDirectory = workingDirectory,
            UseShellExecute = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            CreateNoWindow = true
        };
        foreach (var argument in arguments)
            start.ArgumentList.Add(argument);
        using var process = new Process { StartInfo = start, EnableRaisingEvents = true };
        try
        {
            process.Start();
        }
        catch (Exception ex) when (ex is System.ComponentModel.Win32Exception or FileNotFoundException)
        {
            if (reportOutput)
                Send(new { type = "log", text = ex.Message });
            return -1;
        }

        var outputTask = PumpLines(process.StandardOutput, reportOutput);
        var errorTask = PumpLines(process.StandardError, reportOutput);
        await Task.WhenAll(outputTask, errorTask, process.WaitForExitAsync());
        return process.ExitCode;
    }

    private async Task PumpLines(StreamReader reader, bool reportOutput)
    {
        while (await reader.ReadLineAsync() is { } line)
            if (reportOutput && !string.IsNullOrWhiteSpace(line))
                Send(new { type = "log", text = line });
    }

    private void Send(object message)
    {
        if (_web.CoreWebView2 is null)
            return;
        _web.CoreWebView2.PostWebMessageAsJson(JsonSerializer.Serialize(message));
    }
}
