"""Build guide.html, the readable page for this kit, from README.md.

Usage:
    python docs/agent-memory-kit/build_guide.py           # regenerate guide.html
    python docs/agent-memory-kit/build_guide.py --check   # exit 1 when guide.html is out of date
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MARKER = '/*__KIT_MD__*/""'


def render() -> str:
    markdown = (HERE / "README.md").read_bytes().decode("utf-8").replace("\r\n", "\n")
    template = (HERE / "guide_page.html").read_bytes().decode("utf-8").replace("\r\n", "\n")
    if template.count(MARKER) != 1:
        raise ValueError(f"guide_page.html must contain {MARKER} exactly once")
    return template.replace(MARKER, json.dumps(markdown, ensure_ascii=False).replace("</", "<\\/"))


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    target = HERE / "guide.html"
    page = render()
    if "--check" in args:
        current = target.read_bytes().decode("utf-8").replace("\r\n", "\n") if target.exists() else None
        if current != page:
            print("guide.html is out of date; run: python docs/agent-memory-kit/build_guide.py")
            return 1
        print("guide.html OK")
        return 0
    target.write_bytes(page.encode("utf-8"))
    print(f"wrote {target.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
