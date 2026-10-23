#!/usr/bin/env python3
"""Decrypt a base/update NSP or XCI pair into the folder consumed by extract.py.

Upstream hactool extracts package containers; Ryujinx LibHac reads NCAs and applies the BKTR update. Local keys and
extracted game data stay under the user's machine and build directory.
"""
import argparse
import contextlib
import os
import pathlib
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import urllib.error
import zipfile

TOOLS = pathlib.Path(__file__).resolve().parents[1]
REPO = TOOLS.parent
sys.path.insert(0, str(TOOLS))
import target as T  # noqa: E402

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import nso  # noqa: E402

PROGRAM_ID = 0x0100000000010000
BASE_SDK = '3.5.1.0'
UPDATE_SDK = '7.3.2.0'
VERSION = '1.4.1'


class ContainerError(Exception):
    pass


def default_key_paths():
    candidates = []
    for home in (os.environ.get('USERPROFILE'), os.environ.get('HOME')):
        if home:
            candidates.extend((pathlib.Path(home) / '.switch' / 'prod.keys',
                               pathlib.Path(home) / '.config' / 'switch' / 'prod.keys'))
    xdg = os.environ.get('XDG_CONFIG_HOME')
    if xdg:
        candidates.append(pathlib.Path(xdg) / 'switch' / 'prod.keys')
    appdata = os.environ.get('APPDATA')
    if appdata:
        for emulator in ('Ryujinx', 'Ryubing'):
            candidates.append(pathlib.Path(appdata) / emulator / 'system' / 'prod.keys')
    unique = []
    for path in candidates:
        if path not in unique:
            unique.append(path)
    return unique


def resolve_keys(explicit):
    if explicit:
        path = pathlib.Path(explicit).expanduser().resolve()
        if not path.is_file():
            raise ContainerError('prod.keys was not found at %s' % path)
        return path
    for path in default_key_paths():
        if path.is_file():
            return path.resolve()
    raise ContainerError('prod.keys was not found; use --keys <your local prod.keys> or place it in %%USERPROFILE%%\\.switch\\prod.keys')


def resolve_title_keys(explicit, prod_keys):
    if explicit:
        path = pathlib.Path(explicit).expanduser().resolve()
        if not path.is_file():
            raise ContainerError('title.keys was not found at %s' % path)
        return path
    adjacent = prod_keys.parent / 'title.keys'
    if adjacent.is_file():
        return adjacent.resolve()
    for path in default_key_paths():
        title = path.parent / 'title.keys'
        if title.is_file():
            return title.resolve()
    return None


def resolve_hactool(explicit):
    if explicit:
        candidate = pathlib.Path(explicit).expanduser()
        if candidate.is_file():
            return candidate.resolve()
        found = shutil.which(explicit)
        if found:
            return pathlib.Path(found).resolve()
        raise ContainerError('hactool executable was not found at %s' % explicit)

    from importlib.util import spec_from_file_location, module_from_spec
    installer_path = REPO / 'scripts' / 'install_hactool.py'
    spec = spec_from_file_location('smo2pc_install_hactool', installer_path)
    installer = module_from_spec(spec)
    spec.loader.exec_module(installer)
    try:
        return pathlib.Path(installer.install())
    except (OSError, RuntimeError, urllib.error.URLError, zipfile.BadZipFile) as exc:
        raise ContainerError('could not install pinned hactool: %s' % exc)


def resolve_libhac_installer():
    from importlib.util import spec_from_file_location, module_from_spec
    installer_path = REPO / 'scripts' / 'install_libhac.py'
    spec = spec_from_file_location('smo2pc_install_libhac', installer_path)
    installer = module_from_spec(spec)
    spec.loader.exec_module(installer)
    try:
        return pathlib.Path(installer.install())
    except (OSError, RuntimeError, urllib.error.URLError, zipfile.BadZipFile, KeyError) as exc:
        raise ContainerError('could not install pinned Ryujinx LibHac: %s' % exc)


@contextlib.contextmanager
def hactool_environment(root, title_keys):
    """Make a user-selected title.keys visible through hactool's standard key lookup, then remove the copy."""
    env = os.environ.copy()
    if title_keys:
        home = root / 'key-home'
        switch = home / '.switch'
        switch.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(title_keys, switch / 'title.keys')
        env['HOME'] = str(home)
        env['USERPROFILE'] = str(home)
        env.pop('XDG_CONFIG_HOME', None)
    yield env


def sanitized_failure(output):
    text = re.sub(r'(?i)\b[0-9a-f]{32,}\b', '[redacted]', output)
    if 'Unable to match rights id to titlekey' in text or 'title.keys' in text:
        return 'a rights-ID encrypted NCA needs its matching local title.keys entry'
    if 'key' in text.lower() and ('missing' in text.lower() or 'not found' in text.lower()):
        return 'the local prod.keys keyset is missing or does not match this dump'
    return 'the input is not readable with the selected key files (check the dump and key versions)'


def run_hactool(tool, keys, env, args, context):
    command = [str(tool)]
    if keys:
        command.extend(('-k', str(keys)))
    command.extend(args)
    completed = subprocess.run(command, cwd=str(REPO), env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, errors='replace')
    output = completed.stdout or ''
    if completed.returncode:
        raise ContainerError('%s failed: %s' % (context, sanitized_failure(output)))
    return output


def package_files(tool, keys, env, source, destination):
    source = pathlib.Path(source).expanduser().resolve()
    if not source.is_file():
        raise ContainerError('input package was not found: %s' % source)
    suffix = source.suffix.lower()
    destination.mkdir(parents=True, exist_ok=True)
    if suffix == '.nsp':
        args = ['-t', 'pfs0', '--pfs0dir=' + str(destination), '--suppresskeys', str(source)]
    elif suffix == '.xci':
        args = ['-t', 'xci', '--outdir=' + str(destination), '--suppresskeys', str(source)]
    else:
        raise ContainerError('%s must be an .nsp or .xci' % source.name)
    run_hactool(tool, keys, env, args, 'extracting %s' % source.name)
    entries = sorted(destination.rglob('*.nca'))
    if not entries:
        raise ContainerError('%s contained no readable NCA files' % source.name)
    print('  %s: found %d NCA files' % (source.name, len(entries)))
    return entries


def run_libhac(keys, title_keys, base_root, update_root, destination):
    dotnet = shutil.which('dotnet')
    if not dotnet:
        raise ContainerError('the NSP/XCI path needs the .NET 8 SDK or later to run the Ryujinx LibHac NCA reader')
    resolve_libhac_installer()

    project = TOOLS / 'extract' / 'libhac' / 'LibHacExtractor.csproj'
    build = subprocess.run([dotnet, 'build', str(project), '--configuration', 'Release', '--nologo'],
                           cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace')
    if build.returncode:
        details = re.sub(r'(?i)\b[0-9a-f]{32,}\b', '[redacted]', build.stdout or '')
        raise ContainerError('building the LibHac NCA reader failed:\n%s' % details[-3000:])

    executable = TOOLS / 'extract' / 'libhac' / 'bin' / 'Release' / 'net8.0' / 'LibHacExtractor.dll'
    command = [dotnet, executable, str(keys), str(title_keys) if title_keys else '-',
               str(base_root), str(update_root), str(destination)]
    result = subprocess.run(command, cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, errors='replace')
    output = result.stdout or ''
    if output:
        print(output.rstrip())
    if result.returncode:
        details = re.sub(r'(?i)\b[0-9a-f]{32,}\b', '[redacted]', output)
        raise ContainerError('LibHac could not decrypt this base/update pair:\n%s' % details[-3000:])


def replace_import_dump(build, staged_dump):
    import_root = (build / 'import').resolve()
    destination = import_root / 'dump'
    if destination.exists():
        if destination.is_symlink() or getattr(destination, 'is_junction', lambda: False)():
            raise ContainerError('refusing to replace a linked import folder: %s' % destination)
        if destination.resolve().parent != import_root:
            raise ContainerError('refusing to replace an import folder outside build/import')
        romfs_link = build / 'romfs'
        is_junction = getattr(romfs_link, 'is_junction', lambda: False)()
        if is_junction and romfs_link.resolve() == (destination / 'romfs').resolve():
            os.rmdir(romfs_link)
        shutil.rmtree(destination)
    import_root.mkdir(parents=True, exist_ok=True)
    os.replace(staged_dump, destination)
    return destination


def prepare_dump(base_path, update_path, keys_path=None, title_keys_path=None,
                 hactool_path=None, build_path=None):
    base_source = pathlib.Path(base_path).expanduser().resolve()
    update_source = pathlib.Path(update_path).expanduser().resolve()
    for source in (base_source, update_source):
        if not source.is_file() or source.suffix.lower() not in ('.nsp', '.xci'):
            raise ContainerError('%s must be an existing .nsp or .xci' % source)
    keys = resolve_keys(keys_path)
    title_keys = resolve_title_keys(title_keys_path, keys)
    tool = resolve_hactool(hactool_path)
    build = pathlib.Path(build_path or T.BUILD).resolve()
    build.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='smo2pc-import-', dir=str(build)) as temporary:
        work = pathlib.Path(temporary)
        with hactool_environment(work, title_keys) as env:
            print('Extracting package containers with hactool (keys remain local)...')
            base_package = work / 'base-package'
            update_package = work / 'update-package'
            package_files(tool, keys, env, base_source, base_package)
            package_files(tool, keys, env, update_source, update_package)
            prepared = work / 'prepared-dump'
            print('Decrypting NCAs and applying the BKTR update with Ryujinx LibHac...')
            run_libhac(keys, title_keys, base_package, update_package, prepared)

            control_files = list((prepared / 'control').rglob('control.nacp'))
            if len(control_files) != 1:
                raise ContainerError('expected one control.nacp file in the update, found %d' % len(control_files))
            control_data = control_files[0].read_bytes()
            (prepared / 'control.nacp').write_bytes(control_data)
            shutil.rmtree(prepared / 'control')
            romfs = prepared / 'romfs'

            required = (prepared / 'exefs' / 'main', prepared / 'exefs' / 'main.npdm',
                        prepared / 'exefs' / 'sdk', prepared / 'exefs' / 'rtld',
                        prepared / 'exefs-1.0.0' / 'main', prepared / 'control.nacp')
            missing = [path.name for path in required if not path.is_file()]
            if missing:
                raise ContainerError('decrypted dump is incomplete: %s missing' % ', '.join(missing))
            for label, module, npdm, expected_sdk in (
                    ('base game', prepared / 'exefs-1.0.0' / 'main',
                     prepared / 'exefs-1.0.0' / 'main.npdm', BASE_SDK),
                    ('1.4.1 update', prepared / 'exefs' / 'main',
                     prepared / 'exefs' / 'main.npdm', UPDATE_SDK)):
                try:
                    program_id = nso.npdm_program_id(npdm.read_bytes())
                    elf, _ = nso.nso_to_elf(module.read_bytes())
                    sdk = nso.sdk_version(elf)
                except (ValueError, struct.error) as exc:
                    raise ContainerError('%s contains an invalid program module: %s' % (label, exc))
                if program_id != PROGRAM_ID:
                    raise ContainerError('%s has program id %016x, expected %016x' %
                                         (label, program_id, PROGRAM_ID))
                if sdk != expected_sdk:
                    raise ContainerError('%s uses NintendoSDK %s, expected %s' %
                                         (label, sdk or '(unknown)', expected_sdk))
            if not all((romfs / name).is_dir() for name in ('SystemData', 'StageData', 'ObjectData')):
                raise ContainerError('decrypted RomFS is missing expected SMO folders; check that the update is 1.4.1')
            info = nso.nacp_info(control_data)
            if info['display_version'] != VERSION:
                raise ContainerError('control data is version %s; this build expects %s' %
                                     (info['display_version'] or '(none)', VERSION))
            result = replace_import_dump(build, prepared)
    print('Decrypted dump prepared at %s' % result)
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--base', required=True, help='base game .nsp or .xci')
    ap.add_argument('--update', required=True, help='1.4.1 update .nsp or .xci')
    ap.add_argument('--keys', help='local prod.keys path; defaults to the standard Switch key directory')
    ap.add_argument('--title-keys', help='local title.keys path, if the dump uses rights-ID encrypted NCAs')
    ap.add_argument('--hactool', help='hactool executable; defaults to the pinned upstream Windows release')
    ap.add_argument('--build', default=T.BUILD, help='build output directory')
    args = ap.parse_args()
    try:
        prepare_dump(args.base, args.update, args.keys, args.title_keys, args.hactool, args.build)
    except (ContainerError, OSError, subprocess.SubprocessError) as exc:
        raise SystemExit('container import: %s' % exc)


if __name__ == '__main__':
    main()
