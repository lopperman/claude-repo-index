# Design: repo-index Claude Code Plugin

**Date:** 2026-07-03
**Status:** Approved

## Purpose

A publicly distributable Claude Code plugin providing a `repo-index` skill: point it
at a folder of local git repos and it generates a single, self-contained, interactive
HTML index page for browsing and searching them.

## Goals

- Scan a user-provided folder recursively for git repositories.
- Generate a visually polished, interactive, offline-capable HTML index.
- Distribute publicly via this GitHub repo acting as its own plugin marketplace
  (no Anthropic approval required; community-marketplace submission is a possible
  later step).

## Non-Goals

- No network calls during scan (no GitHub API, no `git fetch`). Works offline and
  with private repos.
- No persistence/config beyond the generated HTML file.
- No third-party Python dependencies (stdlib only).

## Repo Layout

Follows the documented self-hosted marketplace structure (marketplace manifest at
repo root, plugin under `plugins/`):

```
claude-repo-index/
├── .claude-plugin/
│   └── marketplace.json          # catalogs this repo's plugin(s)
├── plugins/
│   └── repo-index/
│       ├── .claude-plugin/
│       │   └── plugin.json       # name, description, version
│       └── skills/
│           └── repo-index/
│               ├── SKILL.md
│               └── scripts/
│                   └── scan_repos.py
├── docs/superpowers/specs/       # design docs (this file)
├── README.md                     # install + usage instructions
└── LICENSE
```

**marketplace.json** (required fields): `name`, `owner.name`, `plugins[]` with
`name`, `source: "./plugins/repo-index"`, `description`.

**plugin.json** (required fields): `name`, `description`, `version`. Version is
bumped explicitly for releases.

**Install (end user):**

```
/plugin marketplace add lopperman/claude-repo-index
/plugin install repo-index@<marketplace-name>
```

## Skill Flow

1. User invokes `/repo-index [folder]`.
2. If no folder argument, Claude asks for one (must be an existing directory).
3. Claude runs `python3 scripts/scan_repos.py <folder>`.
4. Script writes `repo-index.html` into the root of the scanned folder and prints
   the output path.
5. Claude opens the file in the default browser (`open` on macOS, `xdg-open` on
   Linux, `start` on Windows).
6. Re-running refreshes the file in place.

## Scanner (`scan_repos.py`, Python 3 stdlib only)

**Discovery:** walk the folder recursively. A directory containing `.git` is a
repo; do not descend into it further. Skip `node_modules`, `.venv`, `venv`,
`vendor`, `.tox`, and hidden directories during the walk.

**Per repo, collected via local git commands only (no fetch):**

| Field | Source |
|---|---|
| Name, path, parent dir | filesystem |
| Remote URL | `git remote get-url origin`, SSH forms normalized to https hyperlink |
| Last commit date/message/author | `git log -1`; folder mtime as fallback for empty repos |
| Current branch | `git rev-parse --abbrev-ref HEAD` |
| Dirty/clean | `git status --porcelain` |
| Stash count | `git stash list` |
| Ahead/behind upstream | `git rev-list --left-right --count @{upstream}...HEAD` from cached refs (skip silently if no upstream) |
| Primary language | extension counts over `git ls-files`, GitHub-style color map |
| Disk size | working-tree size (excluding `.git`) |
| Commit count | `git rev-list --count HEAD` |
| Activity sparkline | `git log --since='1 year ago'` bucketed into 12 monthly counts |
| Description | `description` field from `package.json` / `pyproject.toml` / `Cargo.toml` if present, else first paragraph of README |
| README | full text of `README.md` (case-insensitive match), embedded for search and expandable rendering |

**Output:** the script embeds the collected data as JSON inside an HTML template
(template lives in the script) and writes one self-contained file. Individual repo
failures (permissions, corrupt repo) are logged to stderr and skipped, never fatal.

## HTML Page

Single self-contained file: inline CSS/JS, zero CDN/external requests, auto
dark/light theme via `prefers-color-scheme`.

- **Search:** one box, wildcard (`*`) plus plain substring matching, across repo
  name, path, description, and full README text.
- **Sort:** name / parent directory (grouped with headers) / last commit date
  (default: last commit, newest first).
- **Filter chips:** "needs attention" (dirty, stashes, or ahead of upstream),
  language, parent directory.
- **Header stat tiles:** total repos, dirty count, unpushed count, language
  breakdown.
- **Repo cards (collapsed):** name, language color dot, status badges (branch,
  dirty, stash, ahead/behind), last commit date + message, path, description,
  12-month sparkline, remote URL as hyperlink.
- **Repo cards (expanded):** full README rendered by a minimal embedded markdown
  renderer (headings, lists, code blocks/inline code, links, bold/italic, images
  degraded to alt text; raw-text fallback for anything else).

## Error Handling

- Nonexistent/non-directory input: script exits nonzero with a clear message.
- Zero repos found: still writes the HTML with an empty state ("no repos found
  under <path>").
- Any single-repo git failure: skip that repo, note it on stderr.

## Verification

1. **Fixture check:** a small script builds synthetic git repos in a temp dir
   (one clean with remote + upstream, one dirty with stash, one with no remote),
   runs the scanner, and asserts the key JSON fields (dirty flag, ahead count,
   remote normalization, language, README capture).
2. **Real run:** scan one of the user's actual project folders, open the HTML,
   confirm search/sort/expand behave with real data.
3. **Install check:** add the repo as a local marketplace and install the plugin
   to confirm the manifests are valid.
