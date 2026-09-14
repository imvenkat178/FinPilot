"""The owner's roadmap dashboard page must keep building from the shared memory files."""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location("roadmap_artifact_tool", ROOT / "scripts" / "roadmap_artifact.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["roadmap_artifact_tool"] = module
    spec.loader.exec_module(module)
    return module


def test_dashboard_page_builds_from_roadmap_and_handoff():
    tool = _load()
    snap = tool.snapshot()
    html = tool.build_html(snap)
    assert "<title>FinPilot Roadmap</title>" in html
    assert "Working on now" in html
    assert "__NOW__" not in html and "__ROADMAP_" not in html
    assert "<!doctype" not in html.lower() and "<body>" not in html
    assert snap["focus"]["focus"], "HANDOFF.md section 4 should list the current focus"
    assert snap["focus"]["sessions"], "HANDOFF.md section 8 should have session entries"
    assert snap["focus"]["last_step"]["when"]


def test_handoff_focus_reads_the_expected_sections():
    tool = _load()
    text = (
        "# Handoff\n\n## 1. Last step (read this first)\n\n- **When:** 2026-01-02\n- **Who:** Agent\n"
        "- **What happened:** Did `G1.1`. Then more.\n- **State left behind:** Clean.\n- **Resume by:**\n  1. Start.\n\n"
        "## 4. Next (current focus)\n\n1. `G1.1` First thing.\n2. Second thing.\n\n## 5. Ideas\n\n"
        "## 8. Session log (append newest first)\n\n<!--\n### YYYY-MM-DD | <agent> | <title>\n-->\n\n"
        "### 2026-01-02 | Agent | Newest\n- **Goal:** x\n\n### 2026-01-01 | Agent | Older\n"
    )
    focus = tool.handoff_focus(text)
    assert focus["focus"] == ["`G1.1` First thing.", "Second thing."]
    assert [s["title"] for s in focus["sessions"]] == ["Newest", "Older"]
    assert focus["last_step"] == {"when": "2026-01-02", "who": "Agent", "what": "Did `G1.1`. Then more.", "state": "Clean."}
