"""The portable agent memory kit in docs/agent-memory-kit must stay valid, in sync, and used here word for word."""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "docs" / "agent-memory-kit" / "templates"
SHARED = ["scripts/roadmap.py", "scripts/check_handoff.py", "tests/test_handoff.py",
          "tests/test_roadmap.py", ".github/PULL_REQUEST_TEMPLATE.md", "CLAUDE.md"]
HORIZON_ROWS = ("| `short-term` |", "| `mid-term` |", "| `long-term` |")


def _load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _text(path):
    return path.read_bytes().decode("utf-8").replace("\r\n", "\n")


def _from(text, start, end=None):
    begin = text.index(start)
    return text[begin:text.index(end, begin)] if end else text[begin:]


def test_kit_copies_match_the_files_this_repo_uses():
    drifted = [rel for rel in SHARED if _text(TEMPLATES / rel) != _text(ROOT / rel)]
    assert not drifted, f"Make these identical in docs/agent-memory-kit/templates/ and the repo: {drifted}"


def test_kit_templates_pass_both_validators():
    roadmap = _load("kit_roadmap_tool", "scripts/roadmap.py")
    handoff = _load("kit_handoff_tool", "scripts/check_handoff.py")
    assert roadmap.check(TEMPLATES / "ROADMAP.md") == []
    text = _text(TEMPLATES / "HANDOFF.md")
    assert handoff.check_structure(text) == []
    assert handoff.check_roadmap_links(text, TEMPLATES / "ROADMAP.md") == []


def test_agents_protocol_matches_the_kit_word_for_word():
    mine, kit = _text(ROOT / "AGENTS.md"), _text(TEMPLATES / "AGENTS.md")
    facts, dont = "## Project facts you need", "## Things not to do without asking the user"
    assert mine[:mine.index(facts)] == kit[:kit.index(facts)], "AGENTS.md opening and protocol must match the kit"
    assert _from(mine, dont) == _from(kit, dont), "AGENTS.md 'Things not to do' must match the kit"


def test_roadmap_rules_match_the_kit_except_horizon_meanings():
    def rules(path):
        header = _from(_text(path), "## How this file works", "## Summary")
        return [line for line in header.split("\n") if not line.startswith(HORIZON_ROWS)]
    assert rules(ROOT / "ROADMAP.md") == rules(TEMPLATES / "ROADMAP.md")
