#!/usr/bin/env python3
"""Decrypt a base/update NSP or XCI pair into the folder consumed by extract.py.

The only decryption implementation is upstream hactool. A local prod.keys keyset is required; title.keys is used
when the dump contains rights-ID encrypted NCAs. Extracted content and temporary key copies stay under build/.
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
PROGRAM_IDS = {PROGRAM_ID, PROGRAM_ID + 0x800}
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


def read_nca_info(tool, keys, env, path):
    output = run_hactool(tool, keys, env,
                         ['-t', 'nca', '--suppresskeys', str(path)],
                         'reading NCA metadata from %s' % path.name)
    title = re.search(r'^Title ID:\s*([0-9a-fA-F]{16})\s*$', output, re.MULTILINE)
    content = re.search(r'^Content Type:\s*([A-Za-z]+)\s*$', output, re.MULTILINE)
    if not title or not content:
        raise ContainerError('hactool could not read NCA metadata from %s; check that prod.keys and title.keys match the dump' % path.name)
    sections = re.findall(r'^\s*Partition Type:\s*(.*?)\s*$', output, re.MULTILINE)
    return {'path': path, 'title_id': int(title.group(1), 16), 'content_type': content.group(1),
            'sections': sections, 'title_key_missing': 'Unable to match rights id to titlekey' in output}


def extract_nca(tool, keys, env, path, destination, base_nca=None):
    destination.mkdir(parents=True, exist_ok=True)
    exefs = destination / 'exefs'
    romfs = destination / 'romfs'
    sections = [destination / ('section%d' % i) for i in range(4)]
    exefs.mkdir(parents=True, exist_ok=True)
    romfs.mkdir(parents=True, exist_ok=True)
    for section in sections:
        section.mkdir(parents=True, exist_ok=True)
    args = ['-t', 'nca', '--suppresskeys', '--exefsdir=' + str(exefs), '--romfsdir=' + str(romfs)]
    args.extend('--section%ddir=%s' % (i, sections[i]) for i in range(4))
    if base_nca:
        args.append('--basenca=' + str(base_nca))
    args.append(str(path))
    run_hactool(tool, keys, env, args, 'decrypting %s' % path.name)
    return {'exefs': exefs, 'romfs': romfs, 'sections': sections}


def identify_program_candidates(tool, keys, env, ncas, destination, expected_sdk):
    matches = []
    candidates = [nca for nca in ncas if nca['content_type'] == 'Program' and nca['title_id'] in PROGRAM_IDS]
    for nca in candidates:
        if nca['title_key_missing']:
            continue
        if not any(section == 'ExeFS' for section in nca['sections']):
            continue
        extracted = extract_nca(tool, keys, env, nca['path'], destination / nca['path'].stem)
        exefs = extracted['exefs']
        main = exefs / 'main'
        npdm = exefs / 'main.npdm'
        if not main.is_file() or not npdm.is_file():
            continue
        try:
            program_id = nso.npdm_program_id(npdm.read_bytes())
            elf, _ = nso.nso_to_elf(main.read_bytes())
            sdk = nso.sdk_version(elf)
        except (ValueError, struct.error) as exc:
            raise ContainerError('%s contains an invalid program module: %s' % (nca['path'].name, exc))
        if program_id == PROGRAM_ID and sdk == expected_sdk:
            matches.append(exefs)
    if len(matches) != 1:
        if not matches and any(nca['title_key_missing'] for nca in candidates):
            raise ContainerError('a rights-ID encrypted program NCA needs its matching local title.keys entry')
        raise ContainerError('expected one %s SMO program NCA, found %d' % (expected_sdk, len(matches)))
    return matches[0]


def extract_control(tool, keys, env, ncas, destination):
    matches = []
    candidates = [nca for nca in ncas if nca['content_type'] == 'Control' and nca['title_id'] in PROGRAM_IDS]
    for nca in candidates:
        if nca['title_key_missing']:
            continue
        extracted = extract_nca(tool, keys, env, nca['path'], destination / nca['path'].stem)
        for nacp in extracted['sections'][0].rglob('control.nacp'):
            data = nacp.read_bytes()
            if nso.nacp_info(data)['display_version'] == VERSION:
                matches.append(data)
        for section in extracted['sections'][1:]:
            for nacp in section.rglob('control.nacp'):
                data = nacp.read_bytes()
                if nso.nacp_info(data)['display_version'] == VERSION:
                    matches.append(data)
    if len(matches) != 1:
        if not matches and any(nca['title_key_missing'] for nca in candidates):
            raise ContainerError('a rights-ID encrypted control NCA needs its matching local title.keys entry')
        raise ContainerError('expected one %s control.nacp file, found %d' % (VERSION, len(matches)))
    return matches[0]


def merge_tree(source, destination):
    for current, dirs, files in os.walk(source):
        relative = pathlib.Path(current).relative_to(source)
        out = destination / relative
        out.mkdir(parents=True, exist_ok=True)
        for name in files:
            shutil.copy2(pathlib.Path(current) / name, out / name)


def extract_updated_romfs(tool, keys, env, base_ncas, update_ncas, destination, work):
    base_candidates = [nca for nca in base_ncas
                       if nca['content_type'] == 'Data' and nca['title_id'] in PROGRAM_IDS
                       and not nca['title_key_missing']
                       and 'RomFS' in nca['sections']]
    update_candidates = [nca for nca in update_ncas
                         if nca['content_type'] == 'Data' and nca['title_id'] in PROGRAM_IDS
                         and not nca['title_key_missing']
                         and any(s in nca['sections'] for s in ('RomFS', 'Patch RomFS'))]
    if not update_candidates:
        if any(nca['content_type'] == 'Data' and nca['title_id'] in PROGRAM_IDS and nca['title_key_missing']
               for nca in update_ncas):
            raise ContainerError('a rights-ID encrypted update data NCA needs its matching local title.keys entry')
        raise ContainerError('the update package has no SMO data NCA with a RomFS')
    destination.mkdir(parents=True, exist_ok=True)
    for nca in update_candidates:
        base = None
        if 'Patch RomFS' in nca['sections']:
            matching = [candidate for candidate in base_candidates if candidate['title_id'] == nca['title_id']]
            if not matching:
                matching = base_candidates
            if not matching and any(candidate['content_type'] == 'Data' and
                                    candidate['title_id'] in PROGRAM_IDS and candidate['title_key_missing']
                                    for candidate in base_ncas):
                raise ContainerError('the base RomFS NCA needs its matching local title.keys entry')
            if len(matching) != 1:
                raise ContainerError('the update RomFS is a patch, but its base data NCA is ambiguous or missing')
            base = matching[0]['path']
        extracted = extract_nca(tool, keys, env, nca['path'], work / 'romfs' / nca['path'].stem, base)
        if not any(extracted['romfs'].iterdir()):
            raise ContainerError('hactool did not produce a RomFS from %s; verify the title keys' % nca['path'].name)
        merge_tree(extracted['romfs'], destination)


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
            base_files = package_files(tool, keys, env, base_source, work / 'base-package')
            update_files = package_files(tool, keys, env, update_source, work / 'update-package')
            print('Reading NCA metadata...')
            base_ncas = [read_nca_info(tool, keys, env, path) for path in base_files]
            update_ncas = [read_nca_info(tool, keys, env, path) for path in update_files]

            prepared = work / 'prepared-dump'
            base_exefs = identify_program_candidates(tool, keys, env, base_ncas,
                                                      work / 'base-exefs', BASE_SDK)
            update_exefs = identify_program_candidates(tool, keys, env, update_ncas,
                                                        work / 'update-exefs', UPDATE_SDK)
            control = extract_control(tool, keys, env, update_ncas, work / 'control')
            romfs = prepared / 'romfs'
            extract_updated_romfs(tool, keys, env, base_ncas, update_ncas, romfs, work)

            (prepared / 'exefs').mkdir(parents=True, exist_ok=True)
            (prepared / 'exefs-1.0.0').mkdir(parents=True, exist_ok=True)
            for path in update_exefs.iterdir():
                if path.is_file():
                    shutil.copy2(path, prepared / 'exefs' / path.name)
            for name in ('main', 'main.npdm'):
                path = base_exefs / name
                if path.is_file():
                    shutil.copy2(path, prepared / 'exefs-1.0.0' / name)
            (prepared / 'control.nacp').write_bytes(control)

            required = (prepared / 'exefs' / 'main', prepared / 'exefs' / 'main.npdm',
                        prepared / 'exefs' / 'sdk', prepared / 'exefs' / 'rtld',
                        prepared / 'exefs-1.0.0' / 'main', prepared / 'control.nacp')
            missing = [path.name for path in required if not path.is_file()]
            if missing:
                raise ContainerError('decrypted dump is incomplete: %s missing' % ', '.join(missing))
            if not all((romfs / name).is_dir() for name in ('SystemData', 'StageData', 'ObjectData')):
                raise ContainerError('decrypted RomFS is missing expected SMO folders; check that the update is 1.4.1')
            info = nso.nacp_info(control)
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
