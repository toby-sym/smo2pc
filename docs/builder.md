# Windows builder app

The Windows builder is a React interface hosted inside a small C# WinForms application. Download the
`SMO2PC-<version>-windows-builder.zip` asset from a GitHub Release, extract the **entire folder** somewhere writable,
and launch `SMO2PC.Builder.exe`.

Choose your own:

- `prod.keys` from your console
- `title.keys` when the package does not contain a ticket with its title key
- the base game NSP/XCI
- the NSP/XCI containing the 1.4.1 update

Press **Build SMO2PC**. The app passes those file paths to the repository's existing `scripts/setup.py`, shows its
output, and creates `build/bin/smo_native.exe` under the extracted folder. Builds resume from completed steps. The app
does not upload keys or game packages; the package importer and generated files stay on the PC.

The first launch creates a local `.venv` with Python 3.13 and installs the project's Python packages. The encrypted
NSP/XCI import path also needs the .NET 8 SDK or later. The game build needs LLVM/clang 9, Visual Studio C++ Build Tools,
the Windows SDK, Vulkan SDK and SDL3. These native tools are not bundled; if one is missing, the app displays the
setup script's diagnostic output. The desktop app requires the Microsoft Edge WebView2 Runtime (included with Windows
11; available separately for Windows 10).

The builder ZIP contains the source tree plus a self-contained Windows app. It includes no Nintendo files, keys or
prebuilt game executable. For the command-line equivalent and the supported dump formats, see
[`building.md`](building.md).
