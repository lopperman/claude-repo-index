---
name: repo-index
description: Scan a folder recursively for local git repositories and generate a self-contained interactive HTML index page (search with wildcards, sort by name/folder/recency, work-status badges, activity sparklines, expandable READMEs). Use when the user wants to index, inventory, browse, or search their local git repos — e.g. "index my repos", "what repos do I have", "build a repo dashboard", "/repo-index ~/projects".
---

# Repo Index

Generate an interactive HTML index of all git repos under a folder.

## Steps

1. Determine the folder to scan. If the user gave one (skill argument or in their message), use it; otherwise ask "Which folder should I scan for git repos?". Expand `~` and confirm the directory exists before running.
2. Run the bundled scanner (scripts/ lives alongside this SKILL.md in the skill's base directory):

   ```
   python3 "<skill-base-dir>/scripts/scan_repos.py" "<folder>"
   ```

   It scans locally (no network, no `git fetch`), writes `repo-index.html` into the scanned folder, and prints `N repos -> <path>`.
3. Open the result in the default browser: `open <path>` on macOS, `xdg-open <path>` on Linux, `start "" <path>` on Windows.
4. Report the repo count and output path. If the script printed `skip <repo>: ...` lines on stderr, list them.

## Notes

- Re-running refreshes `repo-index.html` in place — safe to repeat.
- Pass `-o <path>` to write the HTML somewhere other than the scanned folder.
- The scan is read-only with one exception: the generated HTML file itself.
