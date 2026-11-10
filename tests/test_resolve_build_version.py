import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import resolve_build_version


class ResolveBuildVersionTests(unittest.TestCase):
    def test_manual_release_targets_the_commit_selected_for_the_run(self):
        commit = 'a' * 40
        env = {
            'BUILD_TYPE': 'release',
            'TAG_NAME': 'v1.2.3',
            'GITHUB_REF_TYPE': 'branch',
            'GITHUB_SHA': commit,
        }
        with patch.dict(os.environ, env, clear=True), patch.object(
            resolve_build_version, 'git_tag_commit', side_effect=AssertionError('manual releases must not require an existing tag')
        ):
            result = resolve_build_version.resolve()

        self.assertEqual(result['ref'], commit)
        self.assertEqual(result['tag'], 'v1.2.3')
        self.assertEqual(result['version'], '1.2.3')
        self.assertEqual(result['channel'], 'release')

    def test_tag_trigger_still_requires_the_tag_to_match_the_triggering_commit(self):
        commit = 'b' * 40
        env = {
            'BUILD_TYPE': 'dev',
            'GITHUB_REF_TYPE': 'tag',
            'GITHUB_REF_NAME': 'v2.0.0',
            'GITHUB_SHA': commit,
        }
        with patch.dict(os.environ, env, clear=True), patch.object(
            resolve_build_version, 'git_tag_commit', return_value=commit
        ):
            result = resolve_build_version.resolve()
        self.assertEqual(result['ref'], commit)
        self.assertEqual(result['tag'], 'v2.0.0')

        with patch.dict(os.environ, env, clear=True), patch.object(
            resolve_build_version, 'git_tag_commit', return_value='c' * 40
        ):
            with self.assertRaisesRegex(ValueError, 'does not point to the triggering commit'):
                resolve_build_version.resolve()


if __name__ == '__main__':
    unittest.main()
