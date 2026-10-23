#!/usr/bin/env python3
"""Install the pinned upstream hactool Windows release into ignored deps/."""
import hashlib
import io
import os
import pathlib
import tempfile
import urllib.error
import urllib.request
import zipfile

REPO = pathlib.Path(__file__).resolve().parents[1]
VERSION = '1.4.0'
URL = 'https://github.com/SciresM/hactool/releases/download/1.4.0/hactool-1.4.0-win.zip'
ARCHIVE_SHA256 = '36e9a221c8a7949c86ada9388eb703c90663aedfe9f65b6032429614c5e1abe8'
EXE_SHA256 = '01e6ccb916c74071645df84f13e05d0185c9b2f5d748e469c84a157a53395cf9'
DEST = REPO / 'deps' / ('hactool-' + VERSION) / 'hactool.exe'
ARCHIVE_CACHE = REPO / 'deps' / ('hactool-' + VERSION + '-win.zip')


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def install():
    """Return the pinned hactool executable, downloading only when it is absent."""
    if DEST.is_file():
        if sha256(DEST.read_bytes()) != EXE_SHA256:
            raise RuntimeError('%s exists but does not match the pinned upstream binary' % DEST)
        license_path = DEST.parent / 'LICENSE'
        if not license_path.exists():
            license_path.write_bytes((REPO / 'licenses' / 'hactool-ISC.txt').read_bytes())
        return DEST

    if ARCHIVE_CACHE.is_file():
        archive = ARCHIVE_CACHE.read_bytes()
    else:
        request = urllib.request.Request(URL, headers={'User-Agent': 'smo2pc-build'})
        with urllib.request.urlopen(request, timeout=60) as response:
            archive = response.read()
    if sha256(archive) != ARCHIVE_SHA256:
        raise RuntimeError('Downloaded hactool archive failed its SHA-256 check')
    if not ARCHIVE_CACHE.is_file():
        ARCHIVE_CACHE.parent.mkdir(parents=True, exist_ok=True)
        ARCHIVE_CACHE.write_bytes(archive)

    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        entries = [entry for entry in bundle.infolist() if not entry.is_dir()]
        binary = next((entry for entry in entries if entry.filename == 'hactool.exe'), None)
        if binary is None or len(entries) != 1:
            raise RuntimeError('The pinned hactool archive has an unexpected file layout')
        executable = bundle.read(binary)
    if sha256(executable) != EXE_SHA256:
        raise RuntimeError('hactool.exe failed its SHA-256 check')

    DEST.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='hactool-', suffix='.exe', dir=str(DEST.parent))
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(executable)
        os.replace(temporary, DEST)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    (DEST.parent / 'LICENSE').write_bytes((REPO / 'licenses' / 'hactool-ISC.txt').read_bytes())
    print('Installed upstream hactool %s at %s' % (VERSION, DEST))
    return DEST


if __name__ == '__main__':
    try:
        install()
    except (OSError, RuntimeError, urllib.error.URLError, zipfile.BadZipFile) as exc:
        raise SystemExit('hactool: %s' % exc)
