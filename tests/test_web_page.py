import base64
import hashlib
import json
import re
import shutil
import subprocess
from importlib.resources import files

import pytest

from cardak.web.server import csp

PAGE = files("cardak.web").joinpath("page.html").read_text(encoding="utf-8")


def test_page_has_no_html_injection_sinks() -> None:
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert sink not in PAGE, sink


def test_page_is_self_contained() -> None:
    for ref in ("http://", "https://", "//cdn", "<script src"):
        assert ref not in PAGE, ref
    assert re.findall(r"<link[^>]*>", PAGE) == ['<link rel="icon" href="data:,">']
    assert PAGE.count("<script>") == 1 and PAGE.count("<style>") == 1


def test_page_has_no_inline_style_attributes() -> None:
    assert not re.search(r"\sstyle=", PAGE)  # the hash-based CSP would block them
    assert not re.search(r"\son[a-z]+=", PAGE)  # nor inline handlers


def test_page_copy() -> None:
    for text in (
        "open the link from `cardak web setup` (or scan its QR code) on this device",
        "this link's key is no longer valid · run `cardak web setup` on the computer",
        "can't reach cardak · last data from ",
        "this session has ended",
        "#k=",
        "Bearer ",
        "3000",
        "visibilitychange",
        "Waiting on you",
    ):
        assert text in PAGE, text


def test_csp_hashes_match_page() -> None:
    for tag in ("script", "style"):
        body = re.search(rf"<{tag}>(.*?)</{tag}>", PAGE, re.DOTALL)
        assert body is not None
        digest = base64.b64encode(hashlib.sha256(body.group(1).encode()).digest()).decode()
        assert f"{tag}-src 'sha256-{digest}'" in csp()


def test_fetches_time_out_and_never_overlap() -> None:
    assert "AbortController" in PAGE and "FETCH_TIMEOUT_MS = 5000" in PAGE
    assert "if (inflight) return;" in PAGE


def test_sheet_parses_before_clearing() -> None:
    sheet = PAGE[PAGE.index("async function loadSheet") : PAGE.index("function openSheet")]
    assert sheet.index("await r.json()") < sheet.index("clear(body)")


def _js_function(name: str) -> str:
    """One top-level function's source: a one-liner, or up to the first `}` in column 0."""
    found = re.search(rf"^function {name}\(.*\}}$", PAGE, re.M) or re.search(
        rf"^function {name}\(.*?^\}}$", PAGE, re.M | re.S
    )
    assert found is not None, name
    return found.group(0) + "\n"


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_ram_shows_share_of_the_machine() -> None:
    gib = 2**30
    script = "".join(_js_function(f) for f in ("gb", "share", "ram")) + (
        f"console.log(JSON.stringify([ram({int(13.9 * gib)}, {43 * gib}), "
        f"ram({100 * 2**20}, {43 * gib}), ram(0, {43 * gib}), ram({gib}, null), "
        f"share({22 * gib}, {32 * gib})]));"
    )
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    assert json.loads(out.stdout) == [
        "13.9 GB (32%)",
        "100 MB (<1%)",
        "0 MB (0%)",
        "1.0 GB",
        " (69%)",
    ]


def test_every_ram_figure_carries_its_share() -> None:
    assert "share(m.mem_used, m.mem_total)" in PAGE
    assert "gb(p.rss" not in PAGE and "gb(g.rss" not in PAGE and "gb(d.rss" not in PAGE
    assert "projectCard(g, snap.now, snap.machine && snap.machine.mem_total)" in PAGE
