import base64
import hashlib
import json
import re
import shutil
import subprocess
from importlib.resources import files
from pathlib import Path

import pytest

from cardak.web.server import csp

PAGE = files("cardak.web").joinpath("page.html").read_text(encoding="utf-8")


def test_page_has_no_html_injection_sinks() -> None:
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert sink not in PAGE, sink


def test_page_is_self_contained() -> None:
    # SVG's namespace is a name, never fetched (#86), and the PM view's link prefix a check
    # on the links it makes (#68); nothing else may look like a URL
    page = PAGE.replace('"http://www.w3.org/2000/svg"', "", 1).replace(
        '"https://github.com/"', "", 1
    )
    for ref in ("http://", "https://", "//cdn", "<script src"):
        assert ref not in page, ref
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
        f"share({22 * gib}, {32 * gib}), share(1, 200), share(5, 200)]));"
    )
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    assert json.loads(out.stdout) == [
        "13.9 GB (32%)",
        "100 MB (<1%)",
        "0 MB (0%)",
        "1.0 GB",
        " (69%)",
        " (1%)",
        " (3%)",
    ]


def test_every_ram_figure_carries_its_share() -> None:
    # gb() stands alone only inside ram() and for the machine's total; every other figure is ram()
    calls = [line.strip() for line in PAGE.splitlines() if "gb(" in line]
    assert calls == [
        "function gb(bytes) {",
        "function ram(bytes, total) { return gb(bytes) + share(bytes, total); }",
        'let text = "RAM " + (m.mem_used / 2 ** 30).toFixed(1) + " / " + gb(m.mem_total) +',
    ]
    assert "share(m.mem_used, m.mem_total)" in PAGE


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_usd_matches_the_deck() -> None:
    from cardak.ui.widgets import fmt_usd

    values = [0.421, 9.994, 37.4, 1204.6, 0.0, 12345.0]
    script = _js_function("usd") + f"console.log(JSON.stringify({values}.map(usd)));"
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    assert json.loads(out.stdout) == [fmt_usd(v) for v in values]


def test_page_shows_spend() -> None:
    assert '<span id="spend"></span>' in PAGE
    assert '" this month at API prices"' in PAGE
    assert "g.spend_usd" in PAGE


def test_page_has_trends_view() -> None:
    assert 'id="trends"' in PAGE and '"/api/trends"' in PAGE
    assert "TRENDS_EVERY_MS = 30000" in PAGE


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_count_matches_the_deck() -> None:
    from cardak.ui.widgets import fmt_count

    values = [0, 999, 1234, 340000, 1234567, 3400000000, 12.6]
    script = _js_function("count") + f"console.log(JSON.stringify({values}.map(count)));"
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    assert json.loads(out.stdout) == [fmt_count(v) for v in values]


def test_page_has_context_view() -> None:
    assert 'figure === "context"' in PAGE and "createElementNS" in PAGE
    assert '" ended without one"' in PAGE
    assert '"/api/trends" + (figure === "context" ? "?context=1" : "")' in PAGE


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_log_position_matches_the_deck() -> None:
    from cardak.core.context import position

    values = [0, 5, 10_000, 31_623, 100_000, 400_000, 1_000_000, 5_000_000]
    consts = re.search(r"^const LOG_LOW = .*$", PAGE, re.MULTILINE)
    assert consts is not None
    script = consts.group(0) + "\n" + _js_function("ctxPos")
    script += f"console.log(JSON.stringify({values}.map(ctxPos)));"
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    assert json.loads(out.stdout) == pytest.approx([position(v) for v in values])


def test_page_has_limits_row() -> None:
    assert '<span id="limits"></span>' in PAGE
    assert "renderLimits(snap.plan_limits, snap.now)" in PAGE
    for level in ("lim-low", "lim-critical", "lim-unknown"):
        assert f".{level}" in PAGE


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_limits_text_matches_the_deck() -> None:
    from datetime import datetime

    from cardak.core.limits import LimitsView, LimitView
    from cardak.ui.widgets import limits_label

    now = datetime(2026, 10, 30, 12, 0).timestamp()
    five = datetime(2026, 10, 30, 16, 40).timestamp()
    view = LimitsView(LimitView(27, five, "ok"), LimitView(None, None, "unknown"), 1500.0)
    limits = {
        "five_hour": {"left": 27, "resets_at": five, "level": "ok"},
        "seven_day": {"left": None, "resets_at": None, "level": "unknown"},
        "age_s": 1500.0,
    }
    consts = re.search(r"^const LIMIT_BAR = .*$", PAGE, re.MULTILINE)
    assert consts is not None
    script = consts.group(0) + "\n"
    script += "".join(_js_function(f) for f in ("limitBar", "limitReset", "limitParts")) + (
        f"console.log(limitParts({json.dumps(limits)}, {now}).map(p => p[0]).join(''));"
    )
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == limits_label(view, now).plain


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_page_script_parses(tmp_path: Path) -> None:
    # #70: a second `const STALE_S` made the whole page stop at "connecting…"
    script = re.search(r"<script>(.*?)</script>", PAGE, re.DOTALL)
    assert script is not None
    (tmp_path / "page.js").write_text(script.group(1))
    done = subprocess.run(["node", "--check", str(tmp_path / "page.js")], capture_output=True)
    assert done.returncode == 0, done.stderr.decode()


def test_page_has_projects_sheet() -> None:
    assert 'id="pm"' in PAGE and '"/api/pm"' in PAGE and "PM_EVERY_MS = 30000" in PAGE
    assert ".lc0" in PAGE and ".lc7" in PAGE
    assert 'const GITHUB = "https://github.com/"' in PAGE and "url.startsWith(GITHUB)" in PAGE
