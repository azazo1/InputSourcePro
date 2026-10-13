#!/usr/bin/env python3
"""Prepare, validate, and publish Input Source Pro releases."""
import argparse
import hashlib
import html
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess

OFFSET = 1000
REPOSITORY = 'azazo1/InputSourcePro'
REPO_URL = f'https://github.com/{REPOSITORY}'
VERSION = re.compile(r'v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)\Z')


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def git(*args):
    return run('git', *args)


def gh_json(*args):
    return json.loads(run('gh', *args))


def releases():
    pages = gh_json('api', '--paginate', '--slurp', f'repos/{REPOSITORY}/releases?per_page=100')
    return [release for page in pages for release in page]


def version_tuple(tag):
    match = VERSION.fullmatch(tag)
    return tuple(map(int, match.groups())) if match else None


def plan(args):
    # A main push only builds an artifact; publishing happens for numeric version tags.
    ref = os.environ['GITHUB_REF']
    if ref == 'refs/heads/main':
        build = False
    elif ref.startswith('refs/tags/') and version_tuple(ref.removeprefix('refs/tags/')):
        build = True
    else:
        raise ValueError('Only main pushes and numeric version tags are supported')
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        output.write(f'build={str(build).lower()}\n')


def build_number(commit):
    if git('rev-parse', '--is-shallow-repository') != 'false':
        raise ValueError('Release builds require full Git history')
    return int(git('rev-list', '--count', commit)) + OFFSET


def release_build(release):
    builds = [int(match.group(1)) for asset in release['assets']
              if (match := re.fullmatch(r'Input-Source-Pro-(\d+)\.dmg', asset['name']))]
    return builds[0] if len(builds) == 1 else None


def notes(baseline, commit, version):
    """Uses the reviewed notes file when it exists, otherwise the commit subjects."""
    path = f'docs/release-notes/{version}.md'
    if git('ls-tree', '--name-only', commit, '--', path):
        markdown = subprocess.check_output(['git', 'show', f'{commit}:{path}'], text=True)
        if not markdown.strip():
            raise ValueError(f'Release notes must not be empty: {path}')
        return markdown
    entries = git('log', '--reverse', '--no-merges', '--format=%H %s', f'{baseline}..{commit}').splitlines()
    markdown = []
    for entry in entries:
        sha, subject = entry.split(' ', 1)
        # Escaping keeps raw HTML and Markdown in commit messages inert in release notes.
        escaped = re.sub(r'([\\`*_{}\[\]()#+.!|>~-])', r'\\\1', html.escape(subject))
        markdown.append(f'- {escaped} ([{sha[:7]}]({REPO_URL}/commit/{sha}))')
    return '\n'.join(markdown) + '\n'


def prepare(args):
    commit = git('rev-parse', f'{os.environ["GITHUB_SHA"]}^{{commit}}')
    ref = os.environ['GITHUB_REF']
    if not (ref.startswith('refs/tags/') and version_tuple(ref.removeprefix('refs/tags/'))):
        raise ValueError('Only numeric version tags may release')
    tag = ref.removeprefix('refs/tags/')
    version = tag.removeprefix('v')
    subprocess.run(['git', 'merge-base', '--is-ancestor', commit, 'origin/main'], check=True)
    build = build_number(commit)
    if git('rev-parse', f'{tag}^{{commit}}') != commit:
        raise ValueError('Release tag belongs to another commit; history may have been rewritten')
    existing_releases = releases()
    existing = next((r for r in existing_releases if r['tag_name'] == tag), None)
    complete = existing is not None and not existing['draft']
    if existing and not complete and existing['target_commitish'] != commit:
        raise ValueError('Draft release belongs to another commit; history may have been rewritten')
    if complete and release_build(existing) != build:
        raise ValueError('Published release exists but is incomplete; refusing to overwrite it')
    baseline = '2.12.0'
    if not complete:
        previous = [r for r in existing_releases if not r['draft'] and not r['prerelease']
                    and version_tuple(r['tag_name']) and r['tag_name'] != tag]
        if previous:
            latest = max(previous, key=lambda r: version_tuple(r['tag_name']))
            baseline = latest['tag_name']
        if version_tuple(tag) <= version_tuple(baseline) or build <= build_number(baseline):
            raise ValueError('Release must advance the previous stable version and build')
        subprocess.run(['git', 'merge-base', '--is-ancestor', baseline, commit], check=True)
    metadata = dict(version=version, build=build, tag=tag, commit=commit,
                    asset=f'Input-Source-Pro-{build}.dmg', complete=complete)
    Path('dist').mkdir(exist_ok=True)
    Path('dist/release.json').write_text(json.dumps(metadata, indent=2) + '\n')
    if not complete:
        Path('dist/notes.md').write_text(notes(baseline, commit, version))
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        for key, value in metadata.items():
            output.write(f'{key}={str(value).lower() if isinstance(value, bool) else value}\n')


def check_bundle(args):
    metadata = json.loads(Path('dist/release.json').read_text())
    info = plistlib.loads(Path('dist/export/Input Source Pro.app/Contents/Info.plist').read_bytes())
    expected = {'CFBundleVersion': str(metadata['build']),
                'CFBundleShortVersionString': metadata['version']}
    for key, value in expected.items():
        if info.get(key) != value:
            raise ValueError(f'Exported app has an unexpected {key}')


def publish(args):
    metadata = json.loads(Path('dist/release.json').read_text())
    if metadata['complete']:
        return
    tag = metadata['tag']
    existing = next((r for r in releases() if r['tag_name'] == tag), None)
    if existing and not existing['draft']:
        raise ValueError('Release was published during this run; refusing to overwrite assets')
    if not existing:
        subprocess.run(['gh', 'release', 'create', tag, '--repo', REPOSITORY, '--draft',
                        '--target', metadata['commit'], '--title',
                        f'{metadata["version"]} ({metadata["build"]})',
                        '--notes-file', 'dist/notes.md'], check=True)
    else:
        subprocess.run(['gh', 'release', 'edit', tag, '--repo', REPOSITORY,
                        '--notes-file', 'dist/notes.md'], check=True)
    paths = [Path('dist/updates') / metadata['asset']]
    subprocess.run(['gh', 'release', 'upload', tag, '--repo', REPOSITORY,
                    '--clobber', *map(str, paths)], check=True)
    draft = next(r for r in releases() if r['tag_name'] == tag)
    uploaded = gh_json('api', f'repos/{REPOSITORY}/releases/{draft["id"]}')
    for path in paths:
        asset = next(a for a in uploaded['assets'] if a['name'] == path.name)
        digest = 'sha256:' + hashlib.sha256(path.read_bytes()).hexdigest()
        if asset['size'] != path.stat().st_size or asset.get('digest') != digest:
            raise ValueError(f'Uploaded asset failed verification: {path.name}')
    for release in releases():
        if not release['draft'] and not release['prerelease'] and version_tuple(release['tag_name']):
            if version_tuple(release['tag_name']) >= version_tuple(tag):
                raise ValueError('A newer stable release was published during this build')
    subprocess.run(['gh', 'release', 'edit', tag, '--repo', REPOSITORY, '--draft=false',
                    '--latest=true'], check=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['plan', 'prepare', 'check-bundle', 'publish'])
    arguments = parser.parse_args()
    {'plan': plan, 'prepare': prepare, 'check-bundle': check_bundle,
     'publish': publish}[arguments.command](arguments)
