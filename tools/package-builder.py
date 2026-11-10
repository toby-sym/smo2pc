#!/usr/bin/env python3
"""Create an app-only Windows builder archive and its SHA-256 file."""

import argparse
import hashlib
import os
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', required=True)
    parser.add_argument('--publish', required=True, help='published Windows builder app directory')
    args = parser.parse_args()

    publish = Path(args.publish).resolve()
    executable = publish / 'Smo2pc.Builder.exe'
    if not executable.is_file():
        raise SystemExit('published builder executable is missing: %s' % executable)

    output = REPO / 'artifacts' / 'distribution'
    output.mkdir(parents=True, exist_ok=True)
    archive = output / ('SMO2PC-%s-windows-builder.zip' % args.version)
    checksum = archive.with_suffix('.zip.sha256')
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for path in sorted(publish.rglob('*')):
            if not path.is_file() or path.suffix.lower() in {'.pdb', '.xml'}:
                continue
            bundle.write(path, path.relative_to(publish).as_posix())
        bundle.writestr(
            'BUILD-WINDOWS.txt',
            'SMO2PC Windows Builder\n\n'
            '1. Extract this complete folder somewhere writable.\n'
            '2. Run Smo2pc.Builder.exe.\n'
            '3. Select your own prod.keys, base game NSP/XCI, and 1.4.1 update NSP/XCI.\n'
            '4. Choose where to save the built executable and press Save.\n\n'
            'The builder creates a local Python 3.13 environment when needed. NSP/XCI import also requires '
            '.NET SDK 8 or later. The native build requires LLVM/clang 9, Visual Studio C++ Build Tools, '
            'the Windows SDK, Vulkan SDK, and SDL3. The app shows missing requirements in its build output.\n'
            'Keys and game packages are passed by local file path and are not uploaded. The source build payload '
            'is embedded in the app and extracted privately under LocalAppData when you first build.\n',
        )

    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    checksum.write_text('%s  %s\n' % (digest, archive.name), encoding='ascii', newline='')
    print('Created %s' % os.path.relpath(archive, REPO))
    print('Created %s' % os.path.relpath(checksum, REPO))


if __name__ == '__main__':
    main()
