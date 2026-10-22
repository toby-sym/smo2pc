#!/usr/bin/env python3
"""Create a versioned, game-data-free source archive and SHA-256 file."""

import argparse
import hashlib
import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', required=True)
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


if __name__ == '__main__':
    main()
