# Releases

Push a numeric version tag (`2.13.0` or `v2.13.0`) on a commit in `main` to publish a
GitHub release containing `Input-Source-Pro-<build>.dmg`. The tag must advance the
previously published release. Do not use both tag spellings for the same version.

For a release, create the tag locally and push it:

```sh
git tag 2.14.0
git push origin refs/tags/2.14.0
```

Build numbers are `1000 + git rev-list --count <commit>`, including merged commits.
Keep full history and do not rewrite `main` or change the offset after publishing. The
workflow refuses a tag whose commit is not an ancestor of `origin/main`.

## Continuous integration

Every run builds an ad-hoc signed app and uploads it as the
`Input-Source-Pro-<version>-macos-<arch>` artifact, so a branch, tag, or manual run can
be inspected without signing credentials. Branch builds name the version after
`MARKETING_VERSION` plus the short commit; tag builds use the tag.

Manual runs never publish. The optional `tag` input selects which tag to build, and an
empty value builds the ref chosen in the UI. A repository without the signing secrets
skips publishing and keeps producing artifacts, so a fork stays green while it
validates changes.

## One-time setup

1. Add the repository Actions secrets: `DEVELOPER_ID_APPLICATION` (base64 of a
   Developer ID Application certificate, exported as p12),
   `DEVELOPER_ID_APPLICATION_PSW`, `EXPORT_OPTIONS_PLIST_B64`, `KEYCHAIN_PASSWORD`,
   `APPLE_ID`, `NOTARY_PASSWORD`, and `TEAM_ID`. The workflow needs `contents: write`
   and the `xcode-27` runner.
2. Push to `main` and confirm the artifact appears before tagging a release.

Never paste private keys into logs or commit them. The workflow puts credentials in a
temporary directory with restricted permissions and restores the runner's keychain
search list during cleanup.

## Publication and recovery

CI tests the app and release scripts, archives and exports the app, creates and
notarizes the DMG, and staples it. Each release contains
`Input-Source-Pro-<build>.dmg`. CI uploads to a draft, checks GitHub's asset hashes,
then publishes. A failed run can be rerun to resume the draft. Completed releases are
never overwritten.

Releases use `docs/release-notes/<version>.md` when that file exists in the release
commit. Both `2.13.0` and `v2.13.0` tags use `2.13.0.md`. If the file is absent, notes
contain non-merge commit subjects since the previously published release, initially
`2.12.0`. Empty files stop the release rather than replacing custom notes with a commit
list.

To prepare custom notes:

1. Review changes since the previously published release and send a draft changelog in
   chat. Include user-facing changes, verified contributor credits, and relevant pull
   request links, following the 2.12.0 release style.
2. Let the user review and approve the wording before saving the release notes or
   publishing the release.
3. Save the approved Markdown in `docs/release-notes/<version>.md` and commit it with
   the release changes before tagging. Uncommitted edits are not used.

## Update checks

The app checks `https://api.github.com/repos/azazo1/InputSourcePro/releases/latest`,
which reports published non-prerelease releases only. Automatic checks stay off until
the user enables them in Settings. Manual checks always report a result. No other part
of the app contacts the network.

## Local checks

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s scripts -p 'test_*.py'
xcodebuild -scheme "Input Source Pro" -configuration Debug test
```

Local tests cannot validate Apple credentials, notarization, or a real installed-app
update; verify those with the first tagged release.
