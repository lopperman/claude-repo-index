#!/usr/bin/env python3
"""Scan a folder recursively for git repos and generate a self-contained HTML index.

Local-only: all data comes from the filesystem and local git commands.
No network access, no `git fetch`. Python 3.9+ stdlib only.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from html import escape
from pathlib import Path

SKIP_DIRS = {"node_modules", ".venv", "venv", "vendor", ".tox", "__pycache__"}


def run_git(repo, *args):
    """Run a git command in `repo`; return stripped stdout, or None on any failure."""
    try:
        r = subprocess.run(["git", "-C", str(repo), *args],
                           capture_output=True, text=True, timeout=30)
        return r.stdout.strip() if r.returncode == 0 else None
    except (subprocess.SubprocessError, OSError):
        return None


def find_repos(root: Path):
    """Dirs containing .git (dir or file). No descent into repos, SKIP_DIRS, hidden or symlinked dirs."""
    repos = []

    def walk(d: Path):
        if (d / ".git").exists():
            repos.append(d)
            return
        try:
            children = sorted(e for e in d.iterdir() if e.is_dir() and not e.is_symlink())
        except (PermissionError, OSError):
            return
        for e in children:
            if e.name in SKIP_DIRS or e.name.startswith("."):
                continue
            walk(e)

    walk(root)
    return repos


def normalize_remote(url):
    """ssh/scp git URLs -> https link; http(s) kept; non-URL (e.g. file path) -> None."""
    if not url:
        return None
    m = re.match(r"^(?:ssh://)?git@([^:/]+)[:/](.+?)(?:\.git)?/?$", url)
    if m:
        return f"https://{m.group(1)}/{m.group(2)}"
    if url.startswith(("http://", "https://")):
        return re.sub(r"\.git$", "", url)
    return None


LANG_BY_EXT = {
    ".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript", ".go": "Go", ".rs": "Rust",
    ".rb": "Ruby", ".java": "Java", ".cs": "C#", ".cpp": "C++", ".cc": "C++",
    ".cxx": "C++", ".c": "C", ".h": "C", ".swift": "Swift", ".kt": "Kotlin",
    ".php": "PHP", ".html": "HTML", ".css": "CSS", ".scss": "CSS",
    ".sh": "Shell", ".bash": "Shell", ".zsh": "Shell", ".ps1": "PowerShell",
    ".lua": "Lua", ".vue": "Vue", ".dart": "Dart", ".r": "R", ".sql": "SQL",
    ".tf": "HCL", ".ex": "Elixir", ".exs": "Elixir", ".scala": "Scala",
    ".clj": "Clojure", ".hs": "Haskell", ".m": "Objective-C", ".pl": "Perl",
}
LANG_COLORS = {
    "Python": "#3572A5", "JavaScript": "#f1e05a", "TypeScript": "#3178c6",
    "Go": "#00ADD8", "Rust": "#dea584", "Ruby": "#701516", "Java": "#b07219",
    "C#": "#178600", "C++": "#f34b7d", "C": "#555555", "Swift": "#F05138",
    "Kotlin": "#A97BFF", "PHP": "#4F5D95", "HTML": "#e34c26", "CSS": "#663399",
    "Shell": "#89e051", "PowerShell": "#012456", "Lua": "#000080",
    "Vue": "#41b883", "Dart": "#00B4AB", "R": "#198CE7", "SQL": "#e38c00",
    "HCL": "#844FBA", "Elixir": "#6e4a7e", "Scala": "#c22d40", "Clojure": "#db5855",
    "Haskell": "#5e5086", "Objective-C": "#438eff", "Perl": "#0298c3",
}
DEFAULT_LANG_COLOR = "#8b949e"


def detect_language(repo: Path):
    """Most common code-file language among tracked files (docs/data extensions ignored)."""
    counts = {}
    for f in (run_git(repo, "ls-files") or "").splitlines():
        lang = LANG_BY_EXT.get(Path(f).suffix.lower())
        if lang:
            counts[lang] = counts.get(lang, 0) + 1
    if not counts:
        return None, DEFAULT_LANG_COLOR
    lang = max(counts, key=counts.get)
    return lang, LANG_COLORS.get(lang, DEFAULT_LANG_COLOR)


def dir_size(path: Path) -> int:
    """Working-tree bytes, excluding .git."""
    total = 0
    for dirpath, dirnames, filenames in os.walk(path, onerror=lambda e: None):
        if ".git" in dirnames:
            dirnames.remove(".git")
        for f in filenames:
            try:
                total += (Path(dirpath) / f).stat().st_size
            except OSError:
                pass
    return total


def read_readme(repo: Path) -> str:
    try:
        for f in sorted(repo.iterdir()):
            if f.is_file() and f.name.lower() in ("readme.md", "readme.markdown", "readme"):
                return f.read_text(errors="replace")[:200_000]
    except OSError:
        pass
    return ""


def get_description(repo: Path, readme: str) -> str:
    """Manifest description (package.json / pyproject.toml / Cargo.toml), else README first paragraph."""
    pkg = repo / "package.json"
    if pkg.is_file():
        try:
            desc = json.loads(pkg.read_text(errors="replace")).get("description", "")
            if desc:
                return str(desc).strip()[:300]
        except (json.JSONDecodeError, OSError):
            pass
    for toml_name in ("pyproject.toml", "Cargo.toml"):
        t = repo / toml_name
        if t.is_file():
            try:
                m = re.search(r'^description\s*=\s*"(.*?)"', t.read_text(errors="replace"), re.M)
                if m and m.group(1):
                    return m.group(1)[:300]
            except OSError:
                pass
    # First README block that isn't a heading, badge, image, html, list, quote, or rule.
    for block in re.split(r"\n\s*\n", readme):
        text = block.strip()
        if not text or text.startswith(("#", "!", "<", "[", "-", ">", "```", "|", "=")):
            continue
        return re.sub(r"\s+", " ", text)[:300]
    return ""


def sparkline_counts(repo: Path):
    """Commit counts for the last 12 calendar months, oldest first."""
    buckets = [0] * 12
    now = datetime.now()
    months = []
    for i in range(11, -1, -1):
        y, m = now.year, now.month - i
        while m <= 0:
            m += 12
            y -= 1
        months.append(f"{y:04d}-{m:02d}")
    idx = {mo: i for i, mo in enumerate(months)}
    out = run_git(repo, "log", "--since=1 year ago", "--date=format:%Y-%m", "--pretty=%ad")
    for line in (out or "").splitlines():
        if line in idx:
            buckets[idx[line]] += 1
    return buckets


def collect_repo(repo: Path, root: Path) -> dict:
    rel = repo.relative_to(root) if repo != root else Path(repo.name)
    branch = run_git(repo, "rev-parse", "--abbrev-ref", "HEAD") or "?"
    dirty = bool(run_git(repo, "status", "--porcelain"))
    stashes = len((run_git(repo, "stash", "list") or "").splitlines())
    remote_url = normalize_remote(run_git(repo, "remote", "get-url", "origin"))
    ahead = behind = 0
    has_upstream = False
    lr = run_git(repo, "rev-list", "--left-right", "--count", "@{upstream}...HEAD")
    if lr and len(lr.split()) == 2:
        behind, ahead = (int(n) for n in lr.split())
        has_upstream = True
    last_commit = None
    modified_ts = repo.stat().st_mtime
    log = run_git(repo, "log", "-1", "--pretty=%aI%x00%s%x00%an")
    if log:
        date, message, author = (log.split("\x00") + ["", ""])[:3]
        last_commit = {"date": date, "message": message, "author": author}
        try:
            modified_ts = datetime.fromisoformat(date).timestamp()
        except ValueError:
            pass
    readme = read_readme(repo)
    language, lang_color = detect_language(repo)
    return {
        "name": repo.name,
        "path": str(repo),
        "rel_path": str(rel),
        "parent": str(rel.parent),
        "remote_url": remote_url,
        "branch": branch,
        "dirty": dirty,
        "stashes": stashes,
        "ahead": ahead,
        "behind": behind,
        "has_upstream": has_upstream,
        "last_commit": last_commit,
        "modified_ts": modified_ts,
        "language": language,
        "lang_color": lang_color,
        "size_bytes": dir_size(repo),
        "commit_count": int(run_git(repo, "rev-list", "--count", "HEAD") or 0),
        "sparkline": sparkline_counts(repo),
        "description": get_description(repo, readme),
        "readme": readme,
    }
