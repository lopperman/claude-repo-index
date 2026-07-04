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
