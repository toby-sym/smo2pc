# Windows builder app

The Windows builder is a React interface hosted inside a small C# WinForms application. Download the
`SMO2PC-<version>-windows-builder.zip` asset from a GitHub Release, extract the **entire folder** somewhere writable,
and launch `SMO2PC.Builder.exe`.

Choose your own:

- `prod.keys` from your console
- `title.keys` when the package does not contain a ticket with its title key
- the base game NSP/XCI
- the NSP/XCI containing the 1.4.1 update

Press **Choose output and build** and select where to save the executable. The app passes the selected file paths to
the bundled build payload, shows its output, and saves the completed executable at that location. Required runtime DLLs
and a small path configuration are saved beside it, so the game can read its locally cached game data from the selected
location. Save data is kept in a `userdata` folder beside the executable. Build intermediates stay in the app's local
build folder, and completed steps resume on later runs.
The app does not upload keys or game packages; the package importer and generated files stay on the PC.

The first launch creates a local `.venv` with Python 3.13 and installs the project's Python packages. The encrypted
NSP/XCI import path also needs the .NET 8 SDK or later. The game build needs LLVM/clang 9, Visual Studio C++ Build Tools,
the Windows SDK, Vulkan SDK and SDL3. These native tools are not bundled; if one is missing, the app displays the
setup script's diagnostic output. The desktop app requires the Microsoft Edge WebView2 Runtime (included with Windows
11; available separately for Windows 10).

The builder ZIP contains the self-contained Windows app and compiled interface. The build payload is embedded in the
app and extracted to `%LOCALAPPDATA%\SMO2PC\Builder\source` on first build. The download contains no separate source
tree, Nintendo files, keys or prebuilt game executable. For the command-line equivalent and supported dump formats, see
[`building.md`](building.md).
