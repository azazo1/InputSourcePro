import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import release


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.previous_directory = os.getcwd()
        os.chdir(self.directory.name)
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(os.chdir, self.previous_directory)
        self.git('init', '-q', '-b', 'main')
        self.git('config', 'user.name', 'Release tests')
        self.git('config', 'user.email', 'release@example.com')
        Path('Input Source Pro.xcodeproj').mkdir()
        Path('Input Source Pro.xcodeproj/project.pbxproj').write_text('MARKETING_VERSION = 2.12.0;')
        self.git('add', '.')
        self.git('commit', '-qm', 'initial')
        self.git('tag', '2.12.0')
        self.commit('feat: next feature')

    def git(self, *args):
        return subprocess.check_output(['git', *args], text=True, stderr=subprocess.DEVNULL).strip()

    def commit(self, subject):
        self.git('commit', '--allow-empty', '-qm', subject)
        self.git('update-ref', 'refs/remotes/origin/main', 'HEAD')

    def prepare(self, tag='2.13.0', releases=None):
        with patch.dict(os.environ, GITHUB_REF=f'refs/tags/{tag}', GITHUB_SHA=self.git('rev-parse', 'HEAD'),
                        GITHUB_OUTPUT=str(Path('output').resolve())), patch.object(release, 'releases', return_value=releases or []):
            release.prepare(None)
        return json.loads(Path('dist/release.json').read_text())

    def plan(self, ref='refs/heads/main', commit='HEAD'):
        output = Path('plan-output').resolve()
        output.write_text('')
        with patch.dict(os.environ, GITHUB_REF=ref, GITHUB_SHA=self.git('rev-parse', commit),
                        GITHUB_OUTPUT=str(output)):
            release.plan(None)
        return output.read_text().strip()

    def test_main_push_only_builds_an_artifact(self):
        self.git('tag', '2.13.0')
        self.assertEqual(self.plan(), 'build=false')

    def test_version_tags_publish(self):
        for tag in ['2.13.0', 'v2.13.0']:
            for annotated in [False, True]:
                with self.subTest(tag=tag, annotated=annotated):
                    self.git('tag', *(['-a', '-m', 'Stable release'] if annotated else []), tag)
                    self.assertEqual(self.plan(f'refs/tags/{tag}'), 'build=true')
                    self.git('tag', '-d', tag)

    def test_plan_rejects_unsupported_refs(self):
        for ref in ['refs/heads/feature', 'refs/tags/beta-1002', 'refs/tags/2.13.0-beta', 'refs/tags/2.13']:
            with self.subTest(ref=ref), self.assertRaisesRegex(ValueError, 'numeric version tags'):
                self.plan(ref)

    def test_build_count_includes_merged_commits_and_is_stable_for_tags(self):
        self.git('checkout', '-qb', 'feature')
        self.commit('feat: branch work')
        self.git('checkout', '-q', 'main')
        self.commit('fix: main work')
        self.git('merge', '--no-ff', '-qm', 'merge feature', 'feature')
        self.git('tag', 'v2.13.0')
        self.assertEqual(release.build_number('HEAD'), 1005)
        self.assertEqual(release.build_number('HEAD'), release.build_number('v2.13.0'))

    def test_prepare_writes_metadata_and_notes(self):
        self.git('tag', '2.13.0')
        metadata = self.prepare()
        self.assertEqual(metadata['tag'], '2.13.0')
        self.assertEqual(metadata['version'], '2.13.0')
        self.assertEqual(metadata['build'], 1002)
        self.assertEqual(metadata['asset'], 'Input-Source-Pro-1002.dmg')
        self.assertFalse(metadata['complete'])
        self.assertIn(release.REPO_URL, Path('dist/notes.md').read_text())

    def test_v_prefix_is_stripped_from_the_version(self):
        self.git('tag', 'v2.13.0')
        metadata = self.prepare('v2.13.0')
        self.assertEqual(metadata['tag'], 'v2.13.0')
        self.assertEqual(metadata['version'], '2.13.0')

    def test_main_push_cannot_release(self):
        with patch.dict(os.environ, GITHUB_REF='refs/heads/main', GITHUB_SHA=self.git('rev-parse', 'HEAD'),
                        GITHUB_OUTPUT=str(Path('output').resolve())):
            with self.assertRaisesRegex(ValueError, 'numeric version tags'):
                release.prepare(None)

    def test_notes_escape_commit_subjects(self):
        self.commit('fix: <script>alert("x")</script> [link](bad)')
        self.git('tag', '2.13.0')
        self.prepare()
        notes = Path('dist/notes.md').read_text()
        self.assertIn('next feature', notes)
        self.assertIn('&lt;script&gt;', notes)
        self.assertNotIn('<script>', notes)
        self.assertIn(r'\[link\]', notes)

    def test_previous_published_stable_controls_note_range(self):
        self.git('tag', '2.13.0')
        self.commit('fix: only this is new')
        self.git('tag', '2.14.0')
        self.prepare('2.14.0', [dict(tag_name='2.13.0', draft=False, prerelease=False,
                                     assets=[dict(name='Input-Source-Pro-1002.dmg')])])
        notes = Path('dist/notes.md').read_text()
        self.assertNotIn('next feature', notes)
        self.assertIn('only this is new', notes)

    def commit_notes(self, markdown, version='2.13.0'):
        path = Path(f'docs/release-notes/{version}.md')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown)
        self.git('add', str(path))
        self.commit('docs: add approved release notes')
        return path

    def test_custom_notes_use_release_commit_for_both_tag_spellings(self):
        markdown = '## What\'s new\n\n- Cursor support by [@example](https://github.com/example).\n'
        path = self.commit_notes(markdown)
        self.git('tag', '2.13.0')
        self.git('tag', 'v2.13.0')
        path.write_text('Unreviewed working tree changes')
        for tag in ['2.13.0', 'v2.13.0']:
            with self.subTest(tag=tag):
                self.prepare(tag)
                self.assertEqual(Path('dist/notes.md').read_text(), markdown)

    def test_custom_notes_for_other_version_do_not_override_fallback(self):
        self.commit_notes('Notes for an older release', version='2.12.0')
        self.git('tag', '2.13.0')
        self.prepare()
        notes = Path('dist/notes.md').read_text()
        self.assertIn('next feature', notes)
        self.assertNotIn('Notes for an older release', notes)

    def test_empty_custom_notes_stop_release(self):
        self.commit_notes(' \n\t\n')
        self.git('tag', '2.13.0')
        with self.assertRaisesRegex(ValueError, 'must not be empty'):
            self.prepare()
        self.assertFalse(Path('dist/notes.md').exists())

    def test_completed_rerun_does_not_regenerate_notes(self):
        self.commit_notes('Approved notes')
        self.git('tag', '2.13.0')
        build = release.build_number('HEAD')
        metadata = self.prepare(releases=[dict(
            tag_name='2.13.0', draft=False, assets=[dict(name=f'Input-Source-Pro-{build}.dmg')])])
        self.assertTrue(metadata['complete'])
        self.assertFalse(Path('dist/notes.md').exists())

    def test_draft_release_for_another_commit_is_rejected(self):
        self.git('tag', '2.13.0')
        with self.assertRaisesRegex(ValueError, 'another commit'):
            self.prepare(releases=[dict(tag_name='2.13.0', draft=True,
                                        target_commitish='0' * 40, assets=[])])

    def test_shallow_checkout_is_rejected(self):
        with patch.object(release, 'git', return_value='true'):
            with self.assertRaisesRegex(ValueError, 'full Git history'):
                release.build_number('HEAD')

    def test_version_must_advance(self):
        self.git('tag', 'v2.12.0')
        with self.assertRaisesRegex(ValueError, 'advance'):
            self.prepare('v2.12.0')

    def test_repointed_tag_is_rejected(self):
        self.git('tag', '2.13.0', '2.12.0')
        with self.assertRaisesRegex(ValueError, 'another commit'):
            self.prepare()

    def test_published_assets_are_verified_before_leaving_draft(self):
        self.git('tag', '2.13.0')
        metadata = self.prepare()
        updates = Path('dist/updates')
        updates.mkdir()
        (updates / metadata['asset']).write_text('fixture')
        draft = dict(tag_name=metadata['tag'], draft=True, id=42)
        uploaded = dict(assets=[dict(name=metadata['asset'], size=7,
                                     digest='sha256:' + hashlib.sha256(b'fixture').hexdigest())])
        with patch.object(release, 'releases', side_effect=[[], [draft], []]), \
             patch.object(release, 'gh_json', return_value=uploaded), \
             patch.object(release.subprocess, 'run') as commands:
            release.publish(None)
        calls = [call.args[0] for call in commands.call_args_list]
        self.assertIn('--draft', calls[0])
        self.assertEqual(calls[1][2], 'upload')
        self.assertIn('--draft=false', calls[-1])
        self.assertIn('--latest=true', calls[-1])

    def test_bad_upload_digest_keeps_release_in_draft(self):
        self.git('tag', '2.13.0')
        metadata = self.prepare()
        updates = Path('dist/updates')
        updates.mkdir()
        (updates / metadata['asset']).write_text('fixture')
        draft = dict(tag_name=metadata['tag'], draft=True, id=42)
        uploaded = dict(assets=[dict(name=metadata['asset'], size=7, digest='sha256:wrong')])
        with patch.object(release, 'releases', return_value=[draft]), \
             patch.object(release, 'gh_json', return_value=uploaded), \
             patch.object(release.subprocess, 'run') as commands:
            with self.assertRaisesRegex(ValueError, 'verification'):
                release.publish(None)
        self.assertFalse(any('--draft=false' in call.args[0] for call in commands.call_args_list))

    def test_release_tags_are_strict(self):
        for tag in ['beta-12', '2.13.0-beta', '2.13', 'v02.13.0', '2.13.0\n']:
            self.assertIsNone(release.version_tuple(tag))
        self.assertEqual(release.version_tuple('v2.13.0'), (2, 13, 0))


if __name__ == '__main__':
    unittest.main()
