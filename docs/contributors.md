# Contributors in the About window

**About ▸ Contributors** credits everyone who contributed to EffectCraft, and **About ▸ Models** credits
the AI models named in `Co-Authored-By` trailers.

## Decision

- Two files, both **compiled into the binary** by the UI crate's `build.rs` (nothing is read from
  disk or the network at run time; the web build has them too):
  - [`contributors/contributors.json`](../contributors/contributors.json): commit stats per GitHub
    username. Never hand-edit.
  - [`contributors/people.toml`](../contributors/people.toml): the names people chose for
    themselves. **Each contributor adds only their own entry**, in their own PR.
- Each contributor gets one line: GitHub username (always), display name (opt-in, or their synced
  GitHub profile name), real name (only if they told us), merged PRs, commits, lines added, lines
  deleted, binary assets added, binary assets removed, first and last commit date.
- The list is shown as a **grab bag** (names flowing as text) or a **table**, in the same order. It
  sorts by name, PRs, commits, lines added, lines deleted, line delta, binary assets added/removed,
  first or last commit date. The name toggle cycles **Username → Display name → Real name**; a
  missing name falls back to `@username`. Alphabetical sorting is case-insensitive and ignores the `@`.

## In EffectCraft

- The About window is `about()` in `crates/ui-egui/src/panels/dialogs.rs` (`app.about`, or click the header logo); automation ids `about.tab.*` and `about.credits.*`.

## Credit yourself

Add your entry to `contributors/people.toml` in a PR you commit yourself (every field optional):

```toml
[people.your-github-username]
real_name = "Your Name"
display_name = "Nickname"
sync_github_name = true   # use the name on your public GitHub profile as display name
```

Names show for entries committed by their own user (or by a maintainer with a `consent_source`).
Delete your entry to remove your names. Git author names and emails are never recorded.

## Refreshing release credits

The release workflow refreshes credits once for its exact commit before any source build.
Its read-only `credits` job calls the shared `refresh-contributors.yml` workflow, and the
`release` job waits for it and passes the snapshot artifact to every source-build target.
Refresh failure blocks the release. The snapshot is embedded in the applications; it is
not committed back to the repository or published as a standalone release asset.

The app-side generator uses full Git history for statistics and GitHub's API for account
identities and merged PRs. Names are enabled only for self-authored entries (or recorded
consent committed by someone with repository write access). Public profile names are
queried only for verified entries that opt into synchronization. Model credits come from
explicit model Co-Authored-By trailers, never guesses from human author names or emails.
GitHub-linked human co-authors share the commit's statistics; contributor row totals can
therefore exceed the repository commit/line totals. Commits with no linked account or
unambiguous merged PR author are counted as unattributed.

To refresh a local snapshot, use Python 3.11+, Git and an authenticated `gh` CLI:

```sh
python3 scripts/refresh-contributors.py --repo storytold/effectcraft
```

Check out the intended release commit with full history first. The command replaces only
`contributors/contributors.json`, atomically after collection succeeds. Local development
builds continue to use the checked-in snapshot until it is refreshed.

Generator tests use temporary Git repositories and fixture API responses, without network
access:

```sh
python3 -m unittest discover -s scripts/tests -v
```
