"""Reference documentation must cover every route and setting and match the code. See docs/."""

import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_generated_references_are_current():
    builder = _load("docs_builder", ROOT / "scripts" / "build_docs.py")
    assert builder.check() == [], "Run: .venv/Scripts/python.exe scripts/build_docs.py"


def test_every_route_has_reference_documentation():
    from finpilot.api.app import create_app
    from finpilot.api.route_docs import ROUTES, apply_route_docs, iter_api_routes

    app = create_app("sqlite://")
    routes = {(method, path) for route, path in iter_api_routes(app.routes) for method in route.methods}
    assert apply_route_docs(app) == [], "Add the missing routes to finpilot/api/route_docs.py"
    assert not set(ROUTES) - routes, f"Remove entries for routes that no longer exist: {sorted(set(ROUTES) - routes)}"
    operations = [op for item in app.openapi()["paths"].values() for op in item.values()]
    assert len(operations) == len(ROUTES)
    assert all(op.get("summary") and op.get("description") for op in operations)


def test_every_setting_is_documented():
    reference = (ROOT / "docs" / "configuration.md").read_text(encoding="utf-8")
    names = set()
    for folder in ("finpilot", "scripts"):
        for path in (ROOT / folder).rglob("*.py"):
            names.update(re.findall(r"\b((?:FINPILOT|PLAID|LANGCHAIN)_[A-Z0-9_]+|FORWARDED_ALLOW_IPS)\b", path.read_text(encoding="utf-8")))
    missing = sorted(name for name in names if f"`{name}`" not in reference)
    assert not missing, f"Document these settings in docs/configuration.md: {missing}"
