#!/usr/bin/env python3
"""Create a versioned, game-data-free source archive and SHA-256 file."""

import argparse
import hashlib
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', required=True)
    parser.add_argument('--builder-publish', help='published Windows builder app directory; adds a runnable builder bundle')
    args = parser.parse_args()

    output = REPO / 'artifacts' / 'distribution'
    output.mkdir(parents=True, exist_ok=True)
    archive = output / ('SMO2PC-%s-source.zip' % args.version)
    prefix = 'smo2pc-%s/' % args.version
    subprocess.run(
        ['git', 'archive', '--format=zip', '--prefix=%s' % prefix,
         '--output=%s' % archive, 'HEAD'],
        cwd=REPO,
        check=True,
    )

    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    checksum = archive.with_suffix('.zip.sha256')
    checksum.write_text('%s  %s\n' % (digest, archive.name), encoding='ascii', newline='')
    print('Created %s' % os.path.relpath(archive, REPO))
    print('Created %s' % os.path.relpath(checksum, REPO))

    if args.builder_publish:
        builder_publish = Path(args.builder_publish).resolve()
        if not builder_publish.is_dir() or not any(builder_publish.iterdir()):
            raise SystemExit('builder publish directory is missing or empty: %s' % builder_publish)
        builder_archive = output / ('SMO2PC-%s-windows-builder.zip' % args.version)
        builder_checksum = builder_archive.with_suffix('.zip.sha256')
        with tempfile.TemporaryDirectory(prefix='smo2pc-builder-') as temporary:
            staging = Path(temporary)
            with zipfile.ZipFile(archive) as source:
                source.extractall(staging)
            roots = list(staging.iterdir())
            if len(roots) != 1 or not roots[0].is_dir():
                raise SystemExit('unexpected source archive layout')
            package_root = roots[0]
            for item in builder_publish.iterdir():
                destination = package_root / item.name
                if item.is_dir():
                    shutil.copytree(item, destination, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, destination)
            (package_root / 'BUILD-WINDOWS.txt').write_text(
                'SMO2PC Windows Builder\n\n'
                '1. Extract this complete folder.\n'
                '2. Run SMO2PC.Builder.exe.\n'
                '3. Select your own prod.keys, base game NSP/XCI, and 1.4.1 update NSP/XCI.\n'
                '4. Press Build SMO2PC.\n\n'
                'The builder creates a local Python 3.13 environment when needed. NSP/XCI import also requires '
                '.NET SDK 8 or later. The native build requires LLVM/clang 9, Visual Studio C++ Build Tools, '
                'the Windows SDK, Vulkan SDK, and SDL3. The app shows missing requirements in its build output.\n'
                'Keys and game packages are passed by local file path and are not uploaded.\n',
                encoding='utf-8')
            with zipfile.ZipFile(builder_archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
                for path in package_root.rglob('*'):
                    if path.is_file():
                        bundle.write(path, path.relative_to(staging).as_posix())
        builder_digest = hashlib.sha256(builder_archive.read_bytes()).hexdigest()
        builder_checksum.write_text('%s  %s\n' % (builder_digest, builder_archive.name),
                                    encoding='ascii', newline='')
        print('Created %s' % os.path.relpath(builder_archive, REPO))
        print('Created %s' % os.path.relpath(builder_checksum, REPO))


if __name__ == '__main__':
    main()
