"""Build the landing page (#66): copy site/ to _site/ and fill in the version.

The version lives in pyproject.toml only, so the page carries `__VERSION__` and this fills
it at deploy. Any other `__NAME__` left in a page means a placeholder nothing fills: the
build fails rather than publish it. Run from the repo root: `python scripts/build_site.py`.
"""

import re
import shutil
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
OUT = ROOT / "_site"
PYPROJECT = ROOT / "pyproject.toml"

PLACEHOLDER = re.compile(r"__[A-Z_]{2,}__")


def build(src: Path, out: Path, pyproject: Path) -> str:
    version = tomllib.loads(pyproject.read_text())["project"]["version"]
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(src, out)
    for page in out.rglob("*.html"):
        text = page.read_text().replace("__VERSION__", version)
        page.write_text(text)
        left = sorted(set(PLACEHOLDER.findall(text)))
        if left:
            raise ValueError(f"{page.relative_to(out)}: placeholder left: {', '.join(left)}")
    return version


def main() -> int:
    try:
        version = build(SITE, OUT, PYPROJECT)
    except ValueError as e:
        print(f"build_site: {e}", file=sys.stderr)
        return 1
    print(f"built {OUT.name}/ for cardak {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
