"""Build the owner's roadmap dashboard page and track whether its published copy is current.

The page combines ROADMAP.md (every goal, sub-goal and task) with HANDOFF.md (the current focus,
the last session and recent sessions). Claude Code publishes it as a claude.ai artifact. A local
Claude Code Stop hook runs `stop-hook`, so a session on this machine republishes the page whenever
the shared memory changed since the last publish.

Usage (standard library only; any Python 3.8 or newer):
    python scripts/roadmap_artifact.py build                       # write .local/roadmap-artifact.html
    python scripts/roadmap_artifact.py mark-published --url URL    # record that the last build is live
    python scripts/roadmap_artifact.py status                      # say whether the live page is current
    python scripts/roadmap_artifact.py stop-hook --url URL         # Stop hook: ask for a republish when stale
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / ".local" / "roadmap-artifact.html"
BUILD_INFO = ROOT / ".local" / "roadmap-artifact.build.json"
PUBLISHED = ROOT / ".local" / "roadmap-artifact.published.json"
TITLE = "FinPilot Roadmap"
ENTRY_RE = re.compile(r"^### (\d{4}-\d{2}-\d{2}) \| (.+?) \| (.+?)\s*$")
FIELD_RE = re.compile(r"^- \*\*(When|Who|What happened|State left behind):\*\* (.*)$")

PANEL_CSS = """
.now{display:grid;gap:12px}
.now>h2{font-size:17px}
.now-grid{display:grid;grid-template-columns:minmax(0,1.25fr) minmax(0,1fr);gap:12px;align-items:start}
.now-card{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:16px 18px;display:grid;gap:10px;align-content:start}
.now-card h3{font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);font-weight:600}
.now-card ol,.now-card ul{margin:0;padding-left:20px;display:grid;gap:8px;font-size:14px}
.now-card p{margin:0;font-size:14px;max-width:75ch}
.now-meta{font-size:13px;color:var(--muted)}
.now details{border-top:0}
.now summary{display:block;padding:0;cursor:pointer;color:var(--accent);font-size:13px;font-weight:600}
.now summary::-webkit-details-marker{display:none}
.now details p{margin-top:8px}
.now-empty{color:var(--muted)}
.idlink{all:unset;cursor:pointer;font-family:var(--mono);font-size:.88em;color:var(--accent);text-decoration:underline;text-underline-offset:2px}
.idlink:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
@media (max-width:720px){.now-grid{grid-template-columns:1fr}}
"""

PANEL_HTML = """<section class="now" aria-labelledby="now-title">
    <h2 id="now-title">Working on now</h2>
    <div class="now-grid">
      <div class="now-card">
        <h3>Current focus</h3>
        <ol id="now-focus"></ol>
        <h3>In progress</h3>
        <ul id="now-doing"></ul>
      </div>
      <div class="now-card">
        <h3>Last session</h3>
        <p class="now-meta" id="now-last-meta"></p>
        <p id="now-last"></p>
        <details><summary>Full summary and state left behind</summary><p id="now-last-full"></p><p id="now-state"></p></details>
        <h3>Recent sessions</h3>
        <ul id="now-sessions"></ul>
      </div>
    </div>
  </section>"""

PANEL_JS = """<script>
const NOW = /*__NOW__*/{};
(() => {
  const ID = /\\bG\\d+\\.\\d+(?:\\.\\d+)?\\b/g;
  const idButton = id => {
    const button = el("button", "idlink", id);
    button.type = "button";
    button.addEventListener("click", () => reveal(id.split(".").slice(0, 2).join(".")));
    return button;
  };
  const linked = text => {
    const frag = document.createDocumentFragment();
    String(text || "").replace(/\\*\\*/g, "").split("`").forEach((part, i) => {
      if (i % 2) {
        frag.append(/^G\\d+\\.\\d+(?:\\.\\d+)?$/.test(part) ? idButton(part) : el("code", null, part));
        return;
      }
      let last = 0;
      for (const match of part.matchAll(ID)) {
        frag.append(document.createTextNode(part.slice(last, match.index)), idButton(match[0]));
        last = match.index + match[0].length;
      }
      frag.append(document.createTextNode(part.slice(last)));
    });
    return frag;
  };
  const fill = (id, items, render, emptyText) => {
    const list = document.getElementById(id);
    if (!items.length) { list.append(el("li", "now-empty", emptyText)); return; }
    items.forEach(item => { const li = el("li"); li.append(render(item)); list.append(li); });
  };
  fill("now-focus", NOW.focus || [], linked, "No current focus is recorded in HANDOFF.md.");
  fill("now-doing", subs.filter(s => s.status === "doing"), s => {
    const frag = document.createDocumentFragment();
    frag.append(idButton(s.id), document.createTextNode(` ${s.title}, ${s.horizon}, ${s.priority}`));
    return frag;
  }, "No sub-goal is marked doing.");
  const last = NOW.last_step || {};
  document.getElementById("now-last-meta").textContent = [last.when, last.who].filter(Boolean).join(" · ");
  const what = String(last.what || "");
  const cut = what.search(/\\.\\s/);
  document.getElementById("now-last").append(linked(cut > 0 ? what.slice(0, cut + 1) : what));
  document.getElementById("now-last-full").append(linked(what));
  document.getElementById("now-state").append(linked(last.state ? "State left behind: " + last.state : ""));
  fill("now-sessions", NOW.sessions || [], s => document.createTextNode(`${s.date} · ${s.agent} · ${s.title}`), "No sessions are recorded.");
  document.getElementById("intro").textContent = "Every goal, sub-goal and task from ROADMAP.md, with the current focus and recent sessions from HANDOFF.md. Claude Code republishes this page whenever that shared memory changes.";
  document.getElementById("snapshot").textContent = `Built ${NOW.built || ""} from ROADMAP.md and HANDOFF.md at commit ${NOW.commit || "unknown"}${NOW.dirty ? " plus uncommitted edits" : ""}.`;
})();
</script>"""


def _roadmap_tool():
    spec = importlib.util.spec_from_file_location("roadmap_tool", ROOT / "scripts" / "roadmap.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["roadmap_tool"] = module
    previous, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


def _read(path: Path) -> str:
    return path.read_bytes().decode("utf-8").replace("\r\n", "\n")


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _section(text: str, number: int) -> str:
    start = text.find(f"\n## {number}. ")
    if start < 0:
        return ""
    end = text.find("\n## ", start + 1)
    return text[start:end if end >= 0 else len(text)]


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def handoff_focus(text: str) -> dict:
    """Current focus, last step and the five newest session headings from HANDOFF.md."""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    last = {}
    for line in _section(text, 1).split("\n"):
        match = FIELD_RE.match(line.strip())
        if match:
            last[match.group(1)] = match.group(2).strip()
    focus = [m.group(1).strip() for m in (re.match(r"^\d+\.\s+(.*)$", line.strip())
                                          for line in _section(text, 4).split("\n")) if m]
    sessions = [{"date": m.group(1), "agent": m.group(2), "title": m.group(3)}
                for m in (ENTRY_RE.match(line) for line in _section(text, 8).split("\n")) if m][:5]
    return {"last_step": {"when": last.get("When", ""), "who": last.get("Who", ""),
                          "what": last.get("What happened", ""), "state": last.get("State left behind", "")},
            "focus": focus, "sessions": sessions}


def snapshot() -> dict:
    tool = _roadmap_tool()
    text = _read(ROOT / "ROADMAP.md")
    goals, problems = tool.parse(text)
    page = tool.render_page(text, goals)
    focus = handoff_focus(_read(ROOT / "HANDOFF.md"))
    commit = _git("rev-parse", "--short", "HEAD")
    dirty = bool(_git("status", "--porcelain", "--", "ROADMAP.md", "HANDOFF.md"))
    fingerprint = json.dumps({"page": page, "focus": focus, "commit": commit, "dirty": dirty}, sort_keys=True)
    return {"page": page, "focus": focus, "commit": commit, "dirty": dirty, "problems": problems,
            "hash": hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()}


def build_html(snap: dict) -> str:
    """Turn the standalone roadmap page into an artifact body with the working-now panel."""
    page = snap["page"]
    for piece in ('<!doctype html>\n', '<html lang="en">\n', '<head>\n', '<meta charset="utf-8">\n',
                  '<meta name="viewport" content="width=device-width, initial-scale=1">\n', '</head>\n',
                  '<body>', '</body>\n', '</html>\n'):
        if piece not in page:
            raise ValueError(f"unexpected roadmap page structure: {piece.strip()} is missing")
        page = page.replace(piece, "", 1)
    page = re.sub(r"<title>.*?</title>", f"<title>{TITLE}</title>", page, count=1)
    page = page.replace("</style>", PANEL_CSS.lstrip("\n") + "</style>", 1)
    anchor = '<section class="bands" id="bands" aria-label="Horizons"></section>'
    if anchor not in page:
        raise ValueError("unexpected roadmap page structure: the horizon bands are missing")
    page = page.replace(anchor, PANEL_HTML + "\n\n  " + anchor, 1)
    now = {"commit": snap["commit"], "dirty": snap["dirty"], "built": dt.date.today().isoformat(), **snap["focus"]}
    literal = json.dumps(now, ensure_ascii=False).replace("</", "<\\/")
    return page.strip() + "\n" + PANEL_JS.replace("/*__NOW__*/{}", literal) + "\n"


def build(output: Path) -> int:
    snap = snapshot()
    if snap["problems"]:
        print(f"roadmap_artifact: ROADMAP.md has {len(snap['problems'])} problem(s); run python scripts/roadmap.py check",
              file=sys.stderr)
        return 1
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(build_html(snap).encode("utf-8"))
    BUILD_INFO.write_text(json.dumps({"hash": snap["hash"], "commit": snap["commit"], "dirty": snap["dirty"],
                                      "built_at": dt.datetime.now().isoformat(timespec="seconds"),
                                      "output": output.as_posix()}, indent=2), encoding="utf-8")
    print(f"roadmap_artifact: wrote {output.as_posix()}")
    return 0


def mark_published(url: str) -> int:
    info = _load(BUILD_INFO)
    if not info.get("hash"):
        print("roadmap_artifact: build the page before marking it published", file=sys.stderr)
        return 1
    PUBLISHED.write_text(json.dumps({**info, "url": url,
                                     "published_at": dt.datetime.now().isoformat(timespec="seconds")}, indent=2),
                         encoding="utf-8")
    stale = snapshot()["hash"] != info["hash"]
    print("roadmap_artifact: recorded the published build"
          + ("; the shared memory changed after that build, so build and publish again" if stale else ""))
    return 0


def status() -> int:
    published = _load(PUBLISHED)
    current = snapshot()["hash"]
    if published.get("hash") == current:
        print(f"roadmap_artifact: the published page is current ({published.get('published_at', 'unknown time')})")
        return 0
    print("roadmap_artifact: the published page is out of date" if published else
          "roadmap_artifact: the page has not been published from this machine yet")
    return 1


def stop_hook(url: str) -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except (OSError, ValueError):
        payload = {}
    if payload.get("stop_hook_active"):
        return 0  # already asked once in this stop cycle; never trap the session
    try:
        current = snapshot()["hash"]
    except Exception as exc:  # a broken check must not block the session
        print(json.dumps({"systemMessage": f"Roadmap page check skipped: {exc}"}))
        return 0
    if _load(PUBLISHED).get("hash") == current:
        return 0
    python = ".venv/Scripts/python.exe" if (ROOT / ".venv" / "Scripts" / "python.exe").exists() else "python"
    reason = (
        "ROADMAP.md or HANDOFF.md changed since the owner's roadmap page was last published. Before finishing: "
        f"1. Run `{python} scripts/roadmap_artifact.py build`. "
        f"2. If this session has not read or published the page yet, read it with the Artifact tool (action read, url {url}). "
        f"3. Publish `.local/roadmap-artifact.html` with the Artifact tool, passing url {url} and no favicon. "
        f"4. Run `{python} scripts/roadmap_artifact.py mark-published --url {url}`."
    )
    print(json.dumps({"decision": "block", "reason": reason}))
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    build_cmd = commands.add_parser("build")
    build_cmd.add_argument("--output", type=Path, default=OUTPUT)
    commands.add_parser("mark-published").add_argument("--url", required=True)
    commands.add_parser("status")
    commands.add_parser("stop-hook").add_argument("--url", required=True)
    args = parser.parse_args(argv)
    if args.command == "build":
        return build(args.output)
    if args.command == "mark-published":
        return mark_published(args.url)
    if args.command == "status":
        return status()
    return stop_hook(args.url)


if __name__ == "__main__":
    sys.exit(main())
