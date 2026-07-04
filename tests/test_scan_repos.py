"""Plain-assert tests for scan_repos. Run: python3 tests/test_scan_repos.py"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "plugins/repo-index/skills/repo-index/scripts"
sys.path.insert(0, str(SCRIPTS))
import scan_repos as sr

# Isolated git env: no user/system config (signing hooks, templates), fixed identity.
E = {**os.environ,
     "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.com",
     "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.com",
     "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, env=E)


def make_repo(d: Path) -> Path:
    d.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(d)], check=True, capture_output=True, env=E)
    (d / "README.md").write_text(f"# {d.name}\n\nA test repo called {d.name}.\n")
    (d / "main.py").write_text("print('hi')\n")
    git(d, "add", "-A")
    git(d, "commit", "-q", "-m", "init")
    return d


def build_fixture(tmp: Path) -> Path:
    root = tmp / "code"
    # alpha: clean, tracks a local bare remote, 1 unpushed commit -> ahead=1
    bare = tmp / "bare.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True, capture_output=True, env=E)
    alpha = make_repo(root / "work" / "alpha")
    git(alpha, "remote", "add", "origin", str(bare))
    git(alpha, "push", "-q", "-u", "origin", "main")
    (alpha / "extra.py").write_text("x = 1\n")
    git(alpha, "add", "-A")
    git(alpha, "commit", "-q", "-m", "second")
    # bravo: dirty worktree + one stash + ssh remote (never pushed -> no upstream)
    bravo = make_repo(root / "work" / "bravo")
    git(bravo, "remote", "add", "origin", "git@github.com:lopperman/bravo.git")
    (bravo / "main.py").write_text("print('changed')\n")
    git(bravo, "stash", "push", "-q")
    (bravo / "main.py").write_text("print('changed again')\n")
    # charlie: no remote, under a different parent dir
    make_repo(root / "grp" / "charlie")
    # decoy repo inside node_modules: must be skipped
    make_repo(root / "work" / "node_modules" / "decoy")
    # hidden dir containing a repo: must be skipped
    make_repo(root / ".hidden" / "ghost")
    # symlinked dir pointing at a repo: must not be followed
    (root / "link-to-charlie").symlink_to(root / "grp" / "charlie")
    # plain folder, not a repo
    (root / "notarepo").mkdir(parents=True)
    return root


def test_normalize_remote():
    n = sr.normalize_remote
    assert n("git@github.com:lopperman/bravo.git") == "https://github.com/lopperman/bravo"
    assert n("ssh://git@github.com/u/r.git") == "https://github.com/u/r"
    assert n("https://github.com/u/r.git") == "https://github.com/u/r"
    assert n("https://gitlab.com/u/r") == "https://gitlab.com/u/r"
    assert n("/tmp/bare.git") is None
    assert n(None) is None


def test_find_repos(fx: Path):
    found = {p.name for p in sr.find_repos(fx)}
    assert found == {"alpha", "bravo", "charlie"}, found


def main():
    tmp = Path(tempfile.mkdtemp(prefix="repoindex-test-"))
    try:
        fx = build_fixture(tmp)
        test_normalize_remote()
        test_find_repos(fx)
        print("all tests passed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
