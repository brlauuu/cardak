import re
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).parent.parent
SITE = ROOT / "site"
PAGE = SITE / "index.html"
REPO = "https://github.com/brlauuu/cardak"


class _Tags(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str], int]] = []
        self.comments: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, {k: v or "" for k, v in attrs}, self.getpos()[0]))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_comment(self, data: str) -> None:
        self.comments.append(data)


def _page() -> _Tags:
    tags = _Tags()
    tags.feed(PAGE.read_text())
    return tags


def _refs(tags: _Tags) -> list[tuple[str, str, str]]:
    """Every (tag, attribute, url) the page points at, srcset entries split."""
    out = []
    for tag, attrs, _ in tags.tags:
        for name in ("src", "href"):
            if name in attrs:
                out.append((tag, name, attrs[name]))
        if "srcset" in attrs:
            out += [(tag, "srcset", part.split()[0]) for part in attrs["srcset"].split(",")]
    return out


def _find(tags: _Tags, tag: str, **want: str) -> list[dict[str, str]]:
    return [a for t, a, _ in tags.tags if t == tag and all(a.get(k) == v for k, v in want.items())]


def test_every_asset_it_references_exists() -> None:
    for _, _, url in _refs(_page()):
        if url.startswith("assets/"):
            assert (SITE / url).is_file(), url


def test_it_requests_nothing_from_elsewhere() -> None:
    tags = _page()
    for tag, _, url in _refs(tags):
        if tag == "a":
            # links only lead away; they load nothing
            continue
        assert not re.match(r"(https?:)?//", url), url
    assert not [a for t, a, _ in tags.tags if t == "script" and "src" in a]


def test_paths_are_relative() -> None:
    # the page is served under /cardak/ behind a proxy: a root path would miss it
    for _, _, url in _refs(_page()):
        assert not url.startswith("/") or url.startswith("//"), url


def test_wordmark_follows_the_color_scheme() -> None:
    tags = _page()
    dark = "(prefers-color-scheme: dark)"
    assert _find(tags, "source", media=dark, srcset="assets/cardak-wordmark-dark.svg")
    assert _find(tags, "img", src="assets/cardak-wordmark-light.svg", alt="cardak")


def test_favicons_are_linked() -> None:
    tags = _page()
    assert _find(tags, "link", rel="icon", type="image/svg+xml", href="assets/cardak-icon.svg")
    assert _find(tags, "link", rel="icon", sizes="32x32", href="assets/cardak-icon-32.png")
    assert _find(tags, "link", rel="apple-touch-icon", href="assets/cardak-icon-180.png")


def test_version_placeholder_appears_once() -> None:
    text = PAGE.read_text()
    assert re.findall(r"__[A-Z_]{2,}__", text) == ["__VERSION__"]


def test_video_placeholder_says_how_to_swap() -> None:
    tags = _page()
    assert _find(tags, "section", id="demo")
    assert any("<video" in c and "assets/cardak-demo.mp4" in c for c in tags.comments)


def test_it_tells_how_to_install() -> None:
    # with every extra (#131): the art and Anthropic summaries are optional since 0.18
    assert f"uv tool install 'cardak[all] @ git+{REPO}'" in PAGE.read_text()


def test_sections_in_order() -> None:
    tags = _page()
    line = {t if t in ("picture", "footer") else a.get("id"): n for t, a, n in tags.tags}
    order = [line["picture"], line["what"], line["features"], line["demo"], line["install"]]
    assert order == sorted(order)
    assert order[-1] < line["footer"]


def test_has_a_description_and_social_tags() -> None:
    tags = _page()
    assert _find(tags, "meta", name="description")
    for prop in ("og:title", "og:description", "og:image"):
        assert _find(tags, "meta", property=prop), prop
    # crawlers need an absolute URL for the preview image; the only absolute asset URL
    image = _find(tags, "meta", property="og:image")[0]["content"]
    assert image == "https://brlauuu.dev/cardak/assets/cardak-icon-180.png"


def test_readme_shows_the_wordmark() -> None:
    text = (ROOT / "README.md").read_text()
    head, _, rest = text.partition("# cardak\n")
    # the picture comes first; the heading stays for anything that reads the text
    assert rest
    tags = _Tags()
    tags.feed(head)
    links = [a for t, a, _ in tags.tags if t == "a"]
    assert links and links[0]["href"] == "https://brlauuu.dev/cardak/"
    dark = "(prefers-color-scheme: dark)"
    assert _find(tags, "source", media=dark, srcset="site/assets/cardak-wordmark-dark.svg")
    assert _find(tags, "img", src="site/assets/cardak-wordmark-light.svg", alt="cardak")


def test_install_says_what_the_wezterm_step_needs() -> None:
    # `cardak wezterm install` asks nothing; the user adds one line to wezterm.lua
    text = PAGE.read_text()
    assert "require('cardak').apply(config)" in text
    assert "Each one shows what it will change" not in text


def test_a_video_fits_however_it_is_swapped_in() -> None:
    # replacing the placeholder or its contents: either way the video is never wider than the page
    css = re.search(r"<style>(.*?)</style>", PAGE.read_text(), flags=re.S)
    assert css
    rule = re.search(r"(?m)^\s*video\s*\{([^}]*)\}", css.group(1))
    assert rule and "width: 100%" in rule.group(1) and "height: auto" in rule.group(1)


def test_preview_image_is_1200_by_630() -> None:
    # link previews want at least 200 px, ideally 1200×630 (#106)
    import struct

    head = (SITE / "assets" / "cardak-og.png").read_bytes()[:24]
    assert head[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", head[16:24]) == (1200, 630)
