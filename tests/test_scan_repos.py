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


def test_alpha_status(fx: Path):
    d = sr.collect_repo(fx / "work" / "alpha", fx)
    assert d["name"] == "alpha" and d["rel_path"] == "work/alpha" and d["parent"] == "work"
    assert d["branch"] == "main" and not d["dirty"] and d["stashes"] == 0
    assert d["has_upstream"] and d["ahead"] == 1 and d["behind"] == 0
    assert d["commit_count"] == 2
    assert d["last_commit"] and d["last_commit"]["message"] == "second"
    assert d["modified_ts"] > 0


def test_bravo_dirty_stash_remote(fx: Path):
    d = sr.collect_repo(fx / "work" / "bravo", fx)
    assert d["dirty"] and d["stashes"] == 1
    assert not d["has_upstream"] and d["ahead"] == 0 and d["behind"] == 0
    assert d["remote_url"] == "https://github.com/lopperman/bravo"


def test_charlie_content(fx: Path):
    d = sr.collect_repo(fx / "grp" / "charlie", fx)
    assert d["remote_url"] is None and d["parent"] == "grp"
    assert d["language"] == "Python" and d["lang_color"] == "#3572A5"
    assert d["description"] == "A test repo called charlie."
    assert d["readme"].startswith("# charlie")
    assert len(d["sparkline"]) == 12 and sum(d["sparkline"]) >= 1
    assert d["size_bytes"] > 0


def test_cli_writes_html(fx: Path):
    r = subprocess.run([sys.executable, str(SCRIPTS / "scan_repos.py"), str(fx)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "3 repos" in r.stdout
    html = (fx / "repo-index.html").read_text()
    for name in ("alpha", "bravo", "charlie"):
        assert name in html
    # self-contained: no external scripts/stylesheets
    assert "<script src" not in html and "stylesheet" not in html
    assert "https://github.com/lopperman/bravo" in html


def test_cli_bad_dir():
    r = subprocess.run([sys.executable, str(SCRIPTS / "scan_repos.py"), "/nope/does-not-exist"],
                       capture_output=True, text=True)
    assert r.returncode == 2 and "not a directory" in r.stderr


def test_cli_empty_dir(tmp: Path):
    empty = tmp / "empty"
    empty.mkdir()
    r = subprocess.run([sys.executable, str(SCRIPTS / "scan_repos.py"), str(empty)],
                       capture_output=True, text=True)
    assert r.returncode == 0 and "0 repos" in r.stdout
    assert (empty / "repo-index.html").exists()


def test_build_html_hostile_content():
    hostile = {
        "name": "<img src=x onerror=alert(1)>", "path": "/tmp/h", "rel_path": "h",
        "parent": ".", "remote_url": None, "branch": '"><script>alert(1)</script>',
        "dirty": True, "stashes": 0, "ahead": 0, "behind": 0, "has_upstream": False,
        "last_commit": {"date": "2026-01-01T00:00:00+00:00", "message": "</script><script>alert(1)</script>", "author": "x"},
        "modified_ts": 1.0, "language": None, "lang_color": "#8b949e",
        "size_bytes": 1, "commit_count": 1, "sparkline": [0] * 12,
        "description": "</script> not closed", "readme": "<!--<script>\n# boom\nno closing comment",
    }
    html = sr.build_html([hostile], Path("/tmp"))
    payload_line = html.split("const DATA=", 1)[1].splitlines()[0]
    assert "<" not in payload_line, "raw < must never appear in the JSON payload"
    assert "\\u003c" in payload_line
    assert "<img src=x" not in html


def test_broken_repo_skipped(fx: Path):
    import io
    from contextlib import redirect_stderr, redirect_stdout
    orig = sr.collect_repo
    state = {"n": 0}

    def boom(repo, root):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("synthetic failure")
        return orig(repo, root)

    sr.collect_repo = boom
    err, out = io.StringIO(), io.StringIO()
    try:
        with redirect_stderr(err), redirect_stdout(out):
            rc = sr.main([str(fx), "-o", str(fx / "skip-test.html")])
    finally:
        sr.collect_repo = orig
    assert rc == 0
    assert "skip" in err.getvalue() and "synthetic failure" in err.getvalue()
    assert "2 repos" in out.getvalue()
    assert (fx / "skip-test.html").exists()


def main():
    tmp = Path(tempfile.mkdtemp(prefix="repoindex-test-"))
    try:
        fx = build_fixture(tmp)
        test_normalize_remote()
        test_find_repos(fx)
        test_alpha_status(fx)
        test_bravo_dirty_stash_remote(fx)
        test_charlie_content(fx)
        test_cli_writes_html(fx)
        test_cli_bad_dir()
        test_cli_empty_dir(tmp)
        test_build_html_hostile_content()
        test_broken_repo_skipped(fx)
        print("all tests passed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
