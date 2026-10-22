#!/usr/bin/env python3
"""Resolve an immutable source ref and artifact version for GitHub Actions."""

import os
import re
import subprocess
import sys

TAG_PATTERN = re.compile(r'^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$')
BASE_VERSION = '0.1.0'


def git_tag_commit(tag):
    return subprocess.check_output(
        ['git', 'rev-parse', '--verify', 'refs/tags/%s^{commit}' % tag],
        text=True,
        stderr=subprocess.STDOUT,
    ).strip()


def resolve():
    build_type = os.environ.get('BUILD_TYPE', 'dev').strip().lower()
    ref_type = os.environ.get('GITHUB_REF_TYPE', '').strip().lower()
    ref_name = os.environ.get('GITHUB_REF_NAME', '').strip()
    tag_input = os.environ.get('TAG_NAME', '').strip()
    sha = os.environ.get('GITHUB_SHA', '').strip()

    if build_type not in ('dev', 'release'):
        raise ValueError('BUILD_TYPE must be dev or release')

    if ref_type == 'tag':
        # A version tag always denotes a release, including manual runs started from that tag.
        build_type = 'release'
        tag = ref_name
    elif build_type == 'release':
        tag = tag_input
    else:
        tag = ''

    if build_type == 'release':
        if not TAG_PATTERN.fullmatch(tag):
            raise ValueError('release builds require a vX.Y.Z tag')
        ref = git_tag_commit(tag)
        if ref_type == 'tag' and sha and ref != sha:
            raise ValueError('release tag does not point to the triggering commit')
        version = tag[1:]
        channel = 'release'
    else:
        if not re.fullmatch(r'[0-9a-fA-F]{40}', sha):
            raise ValueError('GITHUB_SHA must be a full commit SHA')
        run_number = os.environ.get('GITHUB_RUN_NUMBER', '').strip()
        if not run_number.isdigit():
            raise ValueError('GITHUB_RUN_NUMBER must be a number')
        ref = sha
        version = '%s-dev.%s+%s' % (BASE_VERSION, run_number, sha[:7].lower())
        channel = 'dev'

    return {
        'version': version,
        'tag': tag,
        'ref': ref,
        'channel': channel,
        'artifact': 'smo2pc-%s-source' % version,
    }


def main():
    try:
        values = resolve()
    except (ValueError, subprocess.CalledProcessError) as error:
        print('build version resolution failed: %s' % error, file=sys.stderr)
        raise SystemExit(1)

    output = os.environ.get('GITHUB_OUTPUT')
    if output:
        with open(output, 'a', encoding='utf-8', newline='') as stream:
            for key, value in values.items():
                stream.write('%s=%s\n' % (key, value))
    else:
        for key, value in values.items():
            print('%s=%s' % (key, value))


if __name__ == '__main__':
    main()
