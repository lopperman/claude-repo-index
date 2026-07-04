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


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Repo Index</title>
<style>
:root{
  --bg:#f4f5f7;--surface:#fff;--border:#e1e4e9;--text:#1b2230;--muted:#68717f;
  --accent:#4a63d0;--accent-soft:#e9edfb;--ok:#2da44e;--warn:#b58a1f;--warn-bg:#fdf3d7;
  --bad:#c33c3c;--bad-bg:#fbe9e9;--dim-bg:#eceef2;--code:#eef0f4;
  --shadow:0 1px 2px rgba(18,24,38,.06),0 2px 8px rgba(18,24,38,.05);
}
@media (prefers-color-scheme:dark){
  :root{--bg:#0d1117;--surface:#161c24;--border:#2a3240;--text:#e4e8ee;--muted:#8d97a5;
  --accent:#8aa2ff;--accent-soft:#202a44;--ok:#3fb950;--warn:#d9a62e;--warn-bg:#332a12;
  --bad:#e5605c;--bad-bg:#3a1d1d;--dim-bg:#212835;--code:#10151d;
  --shadow:0 1px 2px rgba(0,0,0,.5);}
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
  font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",sans-serif}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.85em}
.wrap{max-width:1000px;margin:0 auto;padding:0 20px 80px}
header.top{padding:36px 0 8px}
header.top h1{margin:0;font-size:26px;letter-spacing:-.02em}
.sub{color:var(--muted);margin:6px 0 0;font-size:13px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:12px;margin:20px 0}
.tile{background:var(--surface);border:1px solid var(--border);border-radius:10px;
  padding:12px 16px;box-shadow:var(--shadow)}
.tile b{display:block;font-size:24px;font-weight:650;letter-spacing:-.02em}
.tile span{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.06em}
.tile.warn b{color:var(--warn)} .tile.bad b{color:var(--bad)}
.controls{position:sticky;top:0;background:var(--bg);padding:10px 0 12px;z-index:5;
  display:flex;flex-wrap:wrap;gap:10px;align-items:center;border-bottom:1px solid var(--border)}
#q{flex:1;min-width:220px;padding:9px 14px;border:1px solid var(--border);border-radius:8px;
  background:var(--surface);color:var(--text);font-size:14px;outline:none}
#q:focus{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
select{padding:8px 10px;border:1px solid var(--border);border-radius:8px;
  background:var(--surface);color:var(--text);font-size:13px}
.chip{display:inline-block;border:1px solid var(--border);background:var(--surface);color:var(--muted);
  border-radius:999px;padding:5px 12px;font-size:12.5px;cursor:pointer;user-select:none}
.chip:hover{border-color:var(--accent)}
.chip.on{background:var(--accent-soft);border-color:var(--accent);color:var(--accent);font-weight:600}
.chip i{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:5px}
#langchips{display:flex;flex-wrap:wrap;gap:6px}
#count{color:var(--muted);font-size:13px;margin-left:auto}
h2.group{font-size:13px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);
  margin:26px 0 10px;border-bottom:1px solid var(--border);padding-bottom:6px}
.card{background:var(--surface);border:1px solid var(--border);border-radius:12px;
  padding:16px 18px;margin:12px 0;box-shadow:var(--shadow)}
.card header{display:flex;flex-wrap:wrap;gap:8px 14px;align-items:center}
.name{font-size:17px;font-weight:650;color:var(--text);text-decoration:none;letter-spacing:-.01em}
a.name{color:var(--accent)} a.name:hover{text-decoration:underline}
.lang{color:var(--muted);font-size:12.5px}
.lang i{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:5px;vertical-align:-1px}
.badges{margin-left:auto;display:flex;flex-wrap:wrap;gap:6px}
.badge{font-size:11.5px;padding:2px 9px;border-radius:999px;background:var(--dim-bg);color:var(--muted)}
.badge.warn{background:var(--warn-bg);color:var(--warn)}
.badge.bad{background:var(--bad-bg);color:var(--bad)}
.desc{margin:8px 0 0;color:var(--text);opacity:.85;font-size:14px}
.card footer{display:flex;flex-wrap:wrap;gap:6px 18px;align-items:center;margin-top:10px;
  color:var(--muted);font-size:12.5px}
.spark{margin-left:auto}
.spark rect{fill:var(--accent);opacity:.75}
.toggle{border:1px solid var(--border);background:none;color:var(--accent);border-radius:6px;
  padding:3px 10px;font-size:12px;cursor:pointer}
.toggle:hover{background:var(--accent-soft)}
.readme{border-top:1px solid var(--border);margin-top:14px;padding-top:6px;font-size:14px;
  overflow-wrap:break-word}
.readme pre{background:var(--code);border:1px solid var(--border);border-radius:8px;
  padding:12px;overflow-x:auto;font-size:12.5px;line-height:1.5}
.readme code{background:var(--code);border-radius:4px;padding:.1em .35em;
  font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.88em}
.readme pre code{background:none;padding:0}
.readme h2,.readme h3,.readme h4{margin:1.2em 0 .4em}
.readme blockquote{border-left:3px solid var(--border);margin:.6em 0;padding:.1em 0 .1em 1em;color:var(--muted)}
.readme a{color:var(--accent)}
.empty{text-align:center;color:var(--muted);padding:60px 0;font-size:15px}
</style>
</head>
<body>
<div class="wrap">
<header class="top">
  <h1>Repo Index</h1>
  <p class="sub"><span class="mono">__ROOT__</span> &middot; generated __GENERATED__</p>
</header>
<div class="tiles" id="tiles"></div>
<div class="controls">
  <input id="q" type="search" placeholder="Search name, path, description, README&hellip;  (* = wildcard)">
  <select id="sort">
    <option value="modified">Recently modified</option>
    <option value="name">Name</option>
    <option value="parent">Parent directory</option>
  </select>
  <span class="chip" id="attn"></span>
  <span id="langchips"></span>
  <select id="parent" hidden></select>
  <span id="count"></span>
</div>
<div id="list"></div>
</div>
<script>
const DATA=__DATA__;
DATA.forEach((r,i)=>{r._i=i;r._hay=r.name+"\n"+r.rel_path+"\n"+r.description+"\n"+r.readme;});
const state={q:"",sort:"modified",attention:false,lang:null,parent:""};

function esc(s){return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;")}
function matcher(q){
  const terms=q.trim().split(/\s+/).filter(Boolean).map(t=>
    new RegExp(t.replace(/[.+?^${}()|[\]\\]/g,"\\$&").replace(/\*/g,".*"),"i"));
  return h=>terms.every(rx=>rx.test(h));
}
function fmtSize(b){if(b>=1e9)return(b/1e9).toFixed(1)+" GB";if(b>=1e6)return(b/1e6).toFixed(1)+" MB";
  if(b>=1e3)return Math.round(b/1e3)+" KB";return b+" B"}
function fmtDate(ts){const days=(Date.now()/1000-ts)/86400;
  if(days<1)return"today";if(days<2)return"yesterday";if(days<30)return Math.floor(days)+"d ago";
  if(days<365)return Math.floor(days/30)+"mo ago";return new Date(ts*1000).toLocaleDateString()}
function trunc(s,n){return s.length>n?s.slice(0,n-1)+"…":s}
function spark(counts){
  const max=Math.max(1,...counts);
  const bars=counts.map((c,i)=>{
    const h=c?Math.max(2,Math.round(c/max*18)):1;
    return '<rect x="'+(i*8)+'" y="'+(20-h)+'" width="6" height="'+h+'" rx="1"/>';
  }).join("");
  return '<svg class="spark" width="96" height="20" viewBox="0 0 96 20" aria-hidden="true">'+bars+"</svg>";
}

function inline(s){
  s=esc(s);
  s=s.replace(/`([^`]+)`/g,function(m,c){return"<code>"+c+"</code>"});
  s=s.replace(/!\[([^\]]*)\]\(([^)]*)\)/g,"$1");
  s=s.replace(/\[([^\]]+)\]\(([^)]+)\)/g,function(m,t,u){
    return /^https?:/.test(u)?'<a href="'+u+'" target="_blank" rel="noopener">'+t+"</a>":t});
  s=s.replace(/\*\*([^*]+)\*\*/g,"<strong>$1</strong>");
  s=s.replace(/(^|\s)\*([^*\s][^*]*)\*(?=\s|$)/g,"$1<em>$2</em>");
  return s;
}
function mdToHtml(md){
  const out=[],lines=md.replace(/\r\n/g,"\n").split("\n");
  let i=0,list=null,para=[];
  const flushPara=()=>{if(para.length){out.push("<p>"+inline(para.join(" "))+"</p>");para=[]}};
  const flushList=()=>{if(list){out.push("</"+list+">");list=null}};
  while(i<lines.length){
    const L=lines[i];
    if(/^```/.test(L)){flushPara();flushList();
      const buf=[];i++;
      while(i<lines.length&&!/^```/.test(lines[i])){buf.push(lines[i]);i++}
      out.push("<pre><code>"+esc(buf.join("\n"))+"</code></pre>");i++;continue}
    const h=L.match(/^(#{1,6})\s+(.*)/);
    if(h){flushPara();flushList();const n=Math.min(h[1].length+1,6);
      out.push("<h"+n+">"+inline(h[2])+"</h"+n+">");i++;continue}
    if(/^\s*([-*+]|\d+\.)\s+/.test(L)){flushPara();
      const tag=/^\s*\d+\./.test(L)?"ol":"ul";
      if(list!==tag){flushList();out.push("<"+tag+">");list=tag}
      out.push("<li>"+inline(L.replace(/^\s*([-*+]|\d+\.)\s+/,""))+"</li>");i++;continue}
    if(/^\s*>\s?/.test(L)){flushPara();flushList();
      out.push("<blockquote>"+inline(L.replace(/^\s*>\s?/,""))+"</blockquote>");i++;continue}
    if(/^\s*(-{3,}|\*{3,})\s*$/.test(L)){flushPara();flushList();out.push("<hr>");i++;continue}
    if(/^\s*$/.test(L)){flushPara();flushList();i++;continue}
    para.push(L.trim());i++;
  }
  flushPara();flushList();return out.join("\n");
}

let match=matcher("");
function currentList(){
  let list=DATA.filter(r=>match(r._hay));
  if(state.attention)list=list.filter(r=>r.dirty||r.stashes>0||r.ahead>0);
  if(state.lang)list=list.filter(r=>r.language===state.lang);
  if(state.parent)list=list.filter(r=>r.parent===state.parent);
  if(state.sort==="name")list.sort((a,b)=>a.name.localeCompare(b.name));
  else if(state.sort==="parent")list.sort((a,b)=>a.parent.localeCompare(b.parent)||a.name.localeCompare(b.name));
  else list.sort((a,b)=>b.modified_ts-a.modified_ts);
  return list;
}
function card(r){
  const name=r.remote_url
    ?'<a class="name" href="'+esc(r.remote_url)+'" target="_blank" rel="noopener">'+esc(r.name)+"</a>"
    :'<span class="name">'+esc(r.name)+"</span>";
  let badges='<span class="badge mono">'+esc(r.branch)+"</span>";
  if(r.dirty)badges+='<span class="badge warn">&#9679; uncommitted</span>';
  if(r.stashes)badges+='<span class="badge warn">'+r.stashes+" stashed</span>";
  if(r.ahead)badges+='<span class="badge bad">&uarr;'+r.ahead+" unpushed</span>";
  if(r.behind)badges+='<span class="badge">&darr;'+r.behind+" behind</span>";
  if(!r.remote_url)badges+='<span class="badge">no remote</span>';
  const lang=r.language
    ?'<span class="lang"><i style="background:'+esc(r.lang_color)+'"></i>'+esc(r.language)+"</span>":"";
  const lc=r.last_commit
    ?'<span title="'+esc(r.last_commit.message)+'">'+fmtDate(r.modified_ts)+" &mdash; "+esc(trunc(r.last_commit.message,60))+"</span>"
    :"<span>"+fmtDate(r.modified_ts)+"</span>";
  const btn=r.readme?'<button class="toggle" onclick="toggleReadme(this,'+r._i+')">README &#9656;</button>':"";
  return '<article class="card">'+
    "<header>"+name+lang+'<span class="badges">'+badges+"</span></header>"+
    (r.description?'<p class="desc">'+esc(r.description)+"</p>":"")+
    '<footer><span class="path mono">'+esc(r.rel_path)+"</span>"+lc+
    "<span>"+r.commit_count+" commits &middot; "+fmtSize(r.size_bytes)+"</span>"+
    spark(r.sparkline)+btn+"</footer>"+
    '<div class="readme" id="rm'+r._i+'" hidden></div></article>';
}
function toggleReadme(btn,i){
  const el=document.getElementById("rm"+i);
  if(el.hidden){if(!el.innerHTML)el.innerHTML=mdToHtml(DATA[i].readme);
    el.hidden=false;btn.innerHTML="README &#9662;";}
  else{el.hidden=true;btn.innerHTML="README &#9656;";}
}
function render(){
  const list=currentList(),el=document.getElementById("list");
  let html="",lastGroup=null;
  for(const r of list){
    if(state.sort==="parent"&&r.parent!==lastGroup){
      lastGroup=r.parent;
      html+='<h2 class="group">'+esc(r.parent==="."?"(top level)":r.parent)+"</h2>";
    }
    html+=card(r);
  }
  el.innerHTML=html||('<div class="empty">'+(DATA.length?"No repos match.":"No git repositories found under this folder.")+"</div>");
  document.getElementById("count").textContent=list.length+" of "+DATA.length;
}
function tile(n,label,cls){return '<div class="tile '+(cls||"")+'"><b>'+n+"</b><span>"+label+"</span></div>"}
function init(){
  const dirty=DATA.filter(r=>r.dirty).length,ahead=DATA.filter(r=>r.ahead>0).length;
  const langs={};DATA.forEach(r=>{if(r.language)langs[r.language]=(langs[r.language]||0)+1});
  document.getElementById("tiles").innerHTML=
    tile(DATA.length,"repositories")+tile(dirty,"uncommitted",dirty?"warn":"")+
    tile(ahead,"unpushed",ahead?"bad":"")+tile(Object.keys(langs).length,"languages");
  const attn=document.getElementById("attn");
  attn.textContent="Needs attention ("+DATA.filter(r=>r.dirty||r.stashes>0||r.ahead>0).length+")";
  attn.onclick=()=>{state.attention=!state.attention;attn.classList.toggle("on",state.attention);render()};
  const top=Object.entries(langs).sort((a,b)=>b[1]-a[1]).slice(0,8);
  const lc=document.getElementById("langchips");
  lc.innerHTML=top.map(([l,n])=>{
    const color=(DATA.find(r=>r.language===l)||{}).lang_color||"#8b949e";
    return '<span class="chip lang-chip" data-lang="'+esc(l)+'"><i style="background:'+esc(color)+'"></i>'+esc(l)+" "+n+"</span>";
  }).join("");
  lc.querySelectorAll(".lang-chip").forEach(ch=>ch.onclick=()=>{
    state.lang=state.lang===ch.dataset.lang?null:ch.dataset.lang;
    lc.querySelectorAll(".lang-chip").forEach(c=>c.classList.toggle("on",c.dataset.lang===state.lang));
    render();
  });
  const parents=[...new Set(DATA.map(r=>r.parent))].sort();
  const ps=document.getElementById("parent");
  if(parents.length>1){
    ps.hidden=false;
    ps.innerHTML='<option value="">All folders</option>'+parents.map(p=>
      '<option value="'+esc(p)+'">'+esc(p==="."?"(top level)":p)+"</option>").join("");
    ps.onchange=()=>{state.parent=ps.value;render()};
  }
  document.getElementById("q").oninput=e=>{state.q=e.target.value;match=matcher(state.q);render()};
  document.getElementById("sort").onchange=e=>{state.sort=e.target.value;render()};
  render();
}
init();
</script>
</body>
</html>"""


def build_html(repos, root: Path) -> str:
    payload = json.dumps(repos, separators=(",", ":")).replace("</", "<\\/")
    return (HTML_TEMPLATE
            .replace("__ROOT__", escape(str(root)))
            .replace("__GENERATED__", datetime.now().strftime("%Y-%m-%d %H:%M"))
            .replace("__DATA__", payload))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Generate an HTML index of git repos under a folder.")
    p.add_argument("folder", help="folder to scan recursively for git repos")
    p.add_argument("-o", "--output", help="output HTML path (default: <folder>/repo-index.html)")
    args = p.parse_args(argv)
    root = Path(args.folder).expanduser().resolve()
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 2
    repos = []
    for r in find_repos(root):
        try:
            repos.append(collect_repo(r, root))
        except Exception as e:  # ponytail: one broken repo must never kill the scan
            print(f"skip {r}: {e}", file=sys.stderr)
    out = Path(args.output).expanduser().resolve() if args.output else root / "repo-index.html"
    out.write_text(build_html(repos, root), encoding="utf-8")
    print(f"{len(repos)} repos -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
