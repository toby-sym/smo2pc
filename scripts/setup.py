#!/usr/bin/env python3
"""One entry point: from a fresh clone and your own dump to a runnable native build of the game.

    python scripts/setup.py --dump <folder> [--jobs N]

The game is Super Mario Odyssey 1.4.1. Give either --dump with a decrypted folder (docs/building.md), or --base and
--update with your own .nsp/.xci files and local Switch keys. The encrypted-container path uses upstream hactool and
Ryujinx LibHac to prepare the same decrypted folder locally before continuing. Where build files go: tools/target.py.

Nothing of the game is stored in the repository: every step derives its files from the dump into <repo>/build (ignored
by git). Steps run in this order and are resumable: a step whose output exists is skipped (--redo <step> repeats it,
--from <step> repeats it and everything after it); compile, tables and link always run, and only rebuild what changed.

    deps       check Python packages, native SDKs, and (for NSP/XCI) the .NET SDK
    decrypt    optional NSP/XCI pair -> build/import/dump using local hactool, LibHac, and keys
    import     your dump -> build/dump (modules as ELF, versions); RomFS and Help read in place  (tools/extract/extract.py)
    functions  every function of each module, with exact boundaries                            (tools/extract/functions.py)
    lift       AArch64 -> C for every function -> build/lift/main                              (tools/lifter/recomp.py)
    compile    the lifted C -> objects (clang -O1, incremental)                                (tools/lifter/build_main.py)
    tables     generated headers and tables                                     (tools/lifter/gen_tables.py, gen_sdktest.py)
    sdk        the system-library functions that are lifted too                                 (tools/lifter/sdklift.py)
    link       host runtime + game library -> build/bin/smo_native.exe                          (tools/lifter/build_exe.py)
    check      differential test against Unicorn and link check (only with --only check or --check; minutes)

Other commands:  --status (what is done)   --steps (list)   --only <step>
Tool locations: LLVM_ROOT (clang 9, default C:/llvm-9.0.0), VULKAN_HEADERS (default the Vulkan SDK's Include folder),
SDL3_ROOT (default deps/SDL3-*), WEBVIEW2_ROOT (default deps/webview2-*). Then start the game with scripts/run.py.
"""
import argparse
import glob
import importlib
import os
import re
import shutil
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, 'tools'))
import target as T  # noqa: E402

BUILD = T.BUILD
PY = sys.executable


def tool(*parts):
    return os.path.join(REPO, 'tools', *parts)


def exists(*parts):
    return os.path.exists(os.path.join(BUILD, *parts))


def check_deps(a):
    ok = True
    for mod, pip in (('lz4', 'lz4'), ('capstone', 'capstone'), ('unicorn', 'unicorn'), ('numpy', 'numpy'), ('PIL', 'Pillow')):
        try:
            importlib.import_module(mod)
        except ImportError:
            print('  missing Python package %s (pip install %s)' % (mod, pip))
            ok = False
    clang = os.path.join(os.environ.get('LLVM_ROOT', 'C:/llvm-9.0.0'), 'bin', 'clang.exe')
    vkh = T.vulkan_headers()
    sdl = os.environ.get('SDL3_ROOT') or next(iter(sorted(glob.glob(os.path.join(REPO, 'deps', 'SDL3-*')))), '')
    wv2 = os.environ.get('WEBVIEW2_ROOT') or next(iter(sorted(glob.glob(os.path.join(REPO, 'deps', 'webview2-*')))[-1:]), '')
    wv2 = os.path.join(wv2, 'build', 'native', 'include', 'WebView2.h') if wv2 else ''
    for what, path in (('clang 9 (LLVM_ROOT)', clang), ('Vulkan SDK headers (VULKAN_HEADERS)', vkh), ('SDL3 (SDL3_ROOT or deps/SDL3-*)', sdl),
                       ('WebView2 SDK (WEBVIEW2_ROOT)', wv2)):
        found = bool(path) and os.path.exists(path)
        print('  %-38s %s' % (what, path if found else 'NOT FOUND'))
        ok &= found
    vswhere = os.path.join(os.environ.get('ProgramFiles(x86)', 'C:/Program Files (x86)'),
                           'Microsoft Visual Studio', 'Installer', 'vswhere.exe')
    vs_install = ''
    if os.path.isfile(vswhere):
        result = subprocess.run([vswhere, '-latest', '-products', '*', '-requires',
                                 'Microsoft.VisualStudio.Component.VC.Tools.x86.x64',
                                 '-property', 'installationPath'], capture_output=True, text=True)
        if result.returncode == 0:
            vs_install = result.stdout.strip()
    link = ''
    if vs_install:
        versions = sorted(glob.glob(os.path.join(vs_install, 'VC', 'Tools', 'MSVC', '*')), reverse=True)
        for version in versions:
            candidate = os.path.join(version, 'bin', 'Hostx64', 'x64', 'link.exe')
            if os.path.isfile(candidate):
                link = candidate
                break
    link = link or shutil.which('link.exe') or ''
    print('  %-38s %s' % ('Visual Studio C++ linker (link.exe)', link or 'NOT FOUND'))
    ok &= bool(link)

    sdk_root = os.environ.get('WindowsSdkDir') or os.path.join(
        os.environ.get('ProgramFiles(x86)', 'C:/Program Files (x86)'), 'Windows Kits', '10')
    sdk_versions = sorted(glob.glob(os.path.join(sdk_root, 'Include', '*')), reverse=True)
    sdk = ''
    for include in sdk_versions:
        version = os.path.basename(include)
        required = (os.path.join(include, 'um', 'Windows.h'),
                    os.path.join(sdk_root, 'Lib', version, 'um', 'x64', 'user32.lib'),
                    os.path.join(sdk_root, 'Lib', version, 'ucrt', 'x64', 'ucrt.lib'))
        if all(os.path.isfile(path) for path in required):
            sdk = include
            break
    print('  %-38s %s' % ('Windows SDK headers and x64 libraries', sdk or 'NOT FOUND'))
    ok &= bool(sdk)
    if a.dump and not os.path.isdir(a.dump):
        print('  dump folder %s does not exist' % a.dump)
        ok = False
    for label, path in (('base game', a.base), ('game update', a.update)):
        if path and not os.path.isfile(path):
            print('  %s package %s does not exist' % (label, path))
            ok = False
    if a.base:
        dotnet = shutil.which('dotnet')
        sdk_versions = []
        if dotnet:
            result = subprocess.run([dotnet, '--list-sdks'], capture_output=True, text=True)
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    match = re.match(r'\s*(\d+)\.', line)
                    if match:
                        sdk_versions.append(int(match.group(1)))
        found = bool(sdk_versions and max(sdk_versions) >= 8)
        print('  %-38s %s' % ('.NET SDK 8 or later (NSP/XCI import)', dotnet if found else 'NOT FOUND'))
        ok &= found
    return ok


def need_dump(a):
    if not a.dump:
        raise SystemExit('this step reads your dump: give --dump <folder of your decrypted dump> (docs/building.md)')
    return ['--dump', a.dump]


def report_zero(path, pattern, sources=()):
    try:
        report_mtime = os.path.getmtime(path)
        with open(path, encoding='utf-8') as report:
            text = report.read()
    except OSError:
        return False
    for source in sources:
        try:
            if os.path.getmtime(source) > report_mtime:
                return False
        except OSError:
            return False
    match = re.search(pattern, text, re.MULTILINE)
    return bool(match and int(match.group(1)) == 0)


def cpu_lift_complete():
    return (exists('lift', 'main', 'table.c') and
            report_zero(T.build('lift', 'main', 'cpu_coverage.txt'), r'^Untranslated: (\d+)$',
                        [tool('lifter', 'recomp.py'), T.elf('main'), T.build('functions', 'main_functions.tsv')]))


def sdk_lift_complete():
    return (exists('gen', 'sdklift_gen.c') and
            report_zero(T.build('gen', 'sdklift_report.txt'), r'^roots .*? untranslated (\d+),',
                        [tool('lifter', 'sdklift.py'), tool('lifter', 'recomp.py'),
                         tool('lifter', 'sdklift_rules.txt'), T.elf('sdk'),
                         T.build('functions', 'sdk_functions.tsv')]))


# name, what it makes (for --status; in the build folder), done test (None: always runs), commands
STEPS = [
    ('deps', 'tools found', None, None),
    ('decrypt', 'import/dump', None, None),
    ('import', 'dump, romfs', lambda: os.path.exists(T.elf('main')) and exists('romfs'),
     lambda a: [[PY, tool('extract', 'extract.py')] + need_dump(a)]),
    ('functions', 'functions', lambda: exists('functions', 'main_functions.tsv'),
     lambda a: [[PY, tool('extract', 'functions.py')]]),
    ('lift', 'lift/main', cpu_lift_complete,
     lambda a: [[PY, tool('lifter', 'recomp.py'), 'full']]),
    ('compile', 'obj/main', None, lambda a: [[PY, tool('lifter', 'build_main.py'), str(a.jobs)]]),
    ('tables', 'gen', None,
     lambda a: [[PY, tool('lifter', 'gen_tables.py')], [PY, tool('lifter', 'gen_sdktest.py')]]),
    ('sdk', 'gen/sdklift_gen.c', sdk_lift_complete,
     lambda a: [[PY, tool('lifter', 'sdklift.py'), 'gen']]),
    ('link', 'bin/smo_native.exe', None, lambda a: [[PY, tool('lifter', 'build_exe.py')]]),
    ('check', 'difftest + linkcheck', None,
     lambda a: [[PY, tool('lifter', 'difftest.py'), 'all'], [PY, tool('lifter', 'linkcheck.py')]]),
]
NAMES = [s[0] for s in STEPS]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--dump', help='folder of your decrypted dump (docs/building.md)')
    ap.add_argument('--base', help='your base game .nsp or .xci (use with --update)')
    ap.add_argument('--update', help='your 1.4.1 update .nsp or .xci (use with --base)')
    ap.add_argument('--keys', help='local prod.keys file (defaults to the standard Switch key directory)')
    ap.add_argument('--title-keys', help='local title.keys file for rights-ID encrypted NCAs, if needed')
    ap.add_argument('--hactool', help='hactool executable (defaults to the pinned upstream Windows release)')
    ap.add_argument('--jobs', type=int, default=max(1, (os.cpu_count() or 2) - 2))
    ap.add_argument('--status', action='store_true')
    ap.add_argument('--steps', action='store_true')
    ap.add_argument('--only', choices=NAMES)
    ap.add_argument('--redo', choices=NAMES)
    ap.add_argument('--from', dest='from_step', choices=NAMES)
    ap.add_argument('--check', action='store_true', help='also run the check step at the end')
    a = ap.parse_args()

    if a.steps:
        print('\n'.join(NAMES))
        return
    if a.status:
        print('Super Mario Odyssey %s: %s' % (T.VERSION, os.path.relpath(BUILD, REPO)))
        for name, made, done, _ in STEPS:
            state = 'uses --base/--update' if name == 'decrypt' else ('runs every time (incremental)' if done is None else ('done' if done() else 'to do'))
            print('  %-10s %-28s %s' % (name, made, state if name not in ('deps', 'check') else 'on demand'))
        return

    if (a.base or a.update) and (not a.base or not a.update):
        raise SystemExit('give both --base and --update for the 1.4.1 build')
    if a.dump and (a.base or a.update):
        raise SystemExit('use either --dump or the --base/--update package pair')

    if a.only:
        todo = [a.only]
    else:
        todo = [n for n in NAMES if n != 'check' or a.check]
    if a.base and 'import' in todo and 'decrypt' not in todo:
        todo.insert(todo.index('import'), 'decrypt')
    if a.only == 'decrypt' and not a.base:
        raise SystemExit('--only decrypt needs both --base and --update')
    first = NAMES.index(a.from_step) if a.from_step else None
    for name, made, done, cmds in STEPS:
        if name not in todo:
            continue
        forced = (a.only == name or a.redo == name or
                  (first is not None and NAMES.index(name) >= first) or
                  (name == 'import' and bool(a.base)))
        if name == 'deps':
            print('[deps]')
            if not check_deps(a):
                raise SystemExit('some tools are missing (see docs/building.md)')
            continue
        if name == 'decrypt':
            if not a.base:
                print('[decrypt] skipped (using --dump)')
                continue
            cmd = [PY, tool('extract', 'containers.py'), '--base', a.base, '--update', a.update,
                   '--build', BUILD]
            if a.keys:
                cmd.extend(('--keys', a.keys))
            if a.title_keys:
                cmd.extend(('--title-keys', a.title_keys))
            if a.hactool:
                cmd.extend(('--hactool', a.hactool))
            print('[decrypt] extracting local NSP/XCI inputs into build/import/dump', flush=True)
            result = subprocess.run(cmd, cwd=REPO)
            if result.returncode != 0:
                raise SystemExit('[decrypt] failed (exit code %d)' % result.returncode)
            a.dump = T.build('import', 'dump')
            continue
        if done is not None and done() and not forced:
            print('[%s] done (%s)' % (name, made))
        else:
            for cmd in cmds(a):
                print('[%s] %s' % (name, ' '.join(os.path.relpath(c, REPO) if c.startswith(REPO) else c for c in cmd[1:])), flush=True)
                r = subprocess.run(cmd, cwd=REPO)
                if r.returncode != 0:
                    raise SystemExit('[%s] failed (exit code %d); fix the cause and run setup again, it resumes here' % (name, r.returncode))
    if 'link' in todo:
        print('\nBuilt: %s. Start the game with: python scripts/run.py <run name> --seconds 300'
              % os.path.relpath(T.build('bin', 'smo_native.exe'), REPO))


if __name__ == '__main__':
    main()
