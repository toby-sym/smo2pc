#!/usr/bin/env python3
"""Download the pinned Ryujinx.LibHac package used to read Switch NCA/BKTR data."""
import argparse
import hashlib
import pathlib
import tempfile
import urllib.request
import zipfile

REPO = pathlib.Path(__file__).resolve().parents[1]
PACKAGE_URL = (
    "https://api.nuget.org/v3-flatcontainer/ryujinx.libhac/0.20.0/"
    "ryujinx.libhac.0.20.0.nupkg"
)
PACKAGE_SHA256 = "9acae7f4112538e1ad400cfaa0adc76cc7b22ce435d17b7cc13d10cabf0dc185"
DLL_SHA256 = "b68c441a59974b58f0941d068813fc94df0c3a47dcc38552ea1036838c7cb628"
DESTINATION = REPO / "deps" / "libhac-0.20.0"


def install(force=False):
    dll = DESTINATION / "LibHac.dll"
    if not force and dll.is_file():
        if hashlib.sha256(dll.read_bytes()).hexdigest() == DLL_SHA256:
            return dll

    DESTINATION.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="smo2pc-libhac-") as temporary:
        package = pathlib.Path(temporary) / "ryujinx.libhac.0.20.0.nupkg"
        request = urllib.request.Request(PACKAGE_URL, headers={"User-Agent": "smo2pc-build/1"})
        with urllib.request.urlopen(request, timeout=60) as response, package.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)

        digest = hashlib.sha256(package.read_bytes()).hexdigest()
        if digest != PACKAGE_SHA256:
            raise RuntimeError("Ryujinx.LibHac package checksum mismatch")

        with zipfile.ZipFile(package) as archive:
            dll.write_bytes(archive.read("lib/net8.0/LibHac.dll"))
            (DESTINATION / "LICENSE").write_bytes(archive.read("LICENSE"))
    print("Installed pinned Ryujinx.LibHac 0.20.0 in deps/ (package and keys remain local).")
    return dll


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="download and verify the package again")
    args = parser.parse_args()
    try:
        path = install(args.force)
    except (OSError, RuntimeError, urllib.error.URLError, zipfile.BadZipFile, KeyError) as exc:
        raise SystemExit("LibHac setup: %s" % exc)
    print(path)


if __name__ == "__main__":
    main()
