"""ROADMAP.md must stay valid, and the roadmap tool must catch malformed goal trees. See AGENTS.md."""

import datetime as dt
import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "roadmap.py"
_spec = importlib.util.spec_from_file_location("roadmap_tool", SCRIPT)
roadmap = importlib.util.module_from_spec(_spec)
sys.modules["roadmap_tool"] = roadmap
_spec.loader.exec_module(roadmap)

VALID = """# Test roadmap

```
### G9.9 Examples inside code fences are ignored
- [x] G9.9.1 Not a real task.
```

<!-- roadmap:summary:start -->
<!-- roadmap:summary:end -->

## G1. Quality

### G1.1 Fast tests
`short-term` `P0` `doing`
Done when: tests are fast.
- [x] G1.1.1 Mark slow tests. (done 2026-09-01: tests/test_example.py)
- [ ] G1.1.2 Document the fast command.

### G1.2 Continuous integration
`short-term` `P1` `todo` needs: G1.1
Done when: every pull request runs the tests.
- [ ] G1.2.1 Add the workflow.

## G2. Product

### G2.1 Pricing
`long-term` `P2` `decision`
Decision needed: whether to charge.
Done when: pricing is decided.
- [ ] G2.1.1 Ask the user.
"""


def problems_for(text):
    return roadmap.parse(text)[1]


def test_repository_roadmap_is_valid():
    result = subprocess.run([sys.executable, str(SCRIPT), "check"], cwd=ROOT,
                            capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, "ROADMAP.md failed validation:\n" + result.stdout + result.stderr


def test_valid_tree_has_no_problems():
    assert problems_for(VALID) == []


def test_duplicate_and_misfiled_ids_are_reported():
    problems = problems_for(VALID.replace("G1.2.1 Add the workflow.", "G1.1.2 Add the workflow."))
    assert any("duplicates" in p for p in problems)
    assert any("filed under G1.2" in p for p in problems)


def test_missing_metadata_is_reported():
    problems = problems_for(VALID.replace("`short-term` `P1` `todo` needs: G1.1\n", ""))
    assert any("metadata line" in p for p in problems)


def test_checked_task_needs_dated_evidence():
    problems = problems_for(VALID.replace("- [ ] G1.2.1 Add the workflow.", "- [x] G1.2.1 Add the workflow."))
    assert any("(done YYYY-MM-DD" in p for p in problems)
    assert any("set its status to done" in p for p in problems)
    future = (dt.date.today() + dt.timedelta(days=30)).isoformat()
    problems = problems_for(VALID.replace("(done 2026-09-01:", f"(done {future}:"))
    assert any("in the future" in p for p in problems)


def test_status_rules():
    assert any("tasks are open" in p for p in problems_for(VALID.replace("`P0` `doing`", "`P0` `done`")))
    assert any("Decision needed" in p for p in problems_for(VALID.replace("Decision needed: whether to charge.\n", "")))
    assert any("Done when" in p for p in problems_for(VALID.replace("Done when: pricing is decided.\n", "")))


def test_dependencies_must_exist_and_not_cycle():
    assert any("unknown sub-goal G9.9" in p for p in problems_for(VALID.replace("needs: G1.1", "needs: G9.9")))
    cyclic = VALID.replace("`short-term` `P0` `doing`", "`short-term` `P0` `doing` needs: G1.2")
    assert any("dependency cycle" in p for p in problems_for(cyclic))


def test_summary_is_generated_and_staleness_is_detected(tmp_path):
    path = tmp_path / "ROADMAP.md"
    path.write_text(VALID, encoding="utf-8")
    assert any("stale" in p for p in roadmap.check(path))
    assert roadmap.write(path) == []
    assert roadmap.check(path) == []
    text = path.read_text(encoding="utf-8")
    assert "| G1.2 | Continuous integration | P1 | todo | G1.1 |" in text
    assert "| G2.1 | whether to charge. | long-term |" in text
    assert "**Progress:** 1 of 4 tasks done." in text


def test_write_preserves_crlf_line_endings(tmp_path):
    path = tmp_path / "ROADMAP.md"
    path.write_bytes(VALID.replace("\n", "\r\n").encode("utf-8"))
    roadmap.write(path)
    data = path.read_bytes()
    assert data.count(b"\n") == data.count(b"\r\n")


def test_next_only_offers_work_whose_dependencies_are_finished():
    goals, _ = roadmap.parse(VALID)
    assert [s.id for s in roadmap.actionable(goals)] == ["G1.1"]
    finished = VALID.replace("`P0` `doing`", "`P0` `done`").replace(
        "- [ ] G1.1.2 Document the fast command.",
        "- [x] G1.1.2 Document the fast command. (done 2026-09-02: AGENTS.md)")
    goals, problems = roadmap.parse(finished)
    assert problems == []
    assert [s.id for s in roadmap.actionable(goals)] == ["G1.2"]


def test_configured_roadmap_page_is_generated_and_kept_current(tmp_path):
    path = tmp_path / "ROADMAP.md"
    path.write_text(VALID, encoding="utf-8")
    (tmp_path / ".agent-memory.json").write_text('{"roadmap_page": "docs/roadmap.html"}', encoding="utf-8")
    assert any("roadmap page" in p for p in roadmap.check(path))
    assert roadmap.write(path) == []
    assert roadmap.check(path) == []
    page = (tmp_path / "docs" / "roadmap.html").read_text(encoding="utf-8")
    assert "<title>Test roadmap</title>" in page
    assert "Continuous integration" in page
    assert "__ROADMAP_" not in page and "__PAGE_TITLE__" not in page
    path.write_text(path.read_text(encoding="utf-8").replace("Ask the user.", "Ask the owner."), encoding="utf-8")
    assert any("roadmap page" in p for p in roadmap.check(path))


def test_no_page_is_written_without_configuration(tmp_path):
    path = tmp_path / "ROADMAP.md"
    path.write_text(VALID, encoding="utf-8")
    roadmap.write(path)
    assert not (tmp_path / "docs").exists()
