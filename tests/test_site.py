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


def test_only_the_builds_placeholders() -> None:
    text = PAGE.read_text()
    assert set(re.findall(r"__[A-Z_]{2,}__", text)) == {"__VERSION__", "__RELEASED__"}


def test_video_placeholder_says_how_to_swap() -> None:
    tags = _page()
    assert _find(tags, "section", id="demo")
    assert any("<video" in c and "assets/cardak-demo.mp4" in c for c in tags.comments)


def test_it_tells_how_to_install() -> None:
    # with every extra (#131): the art and Anthropic summaries are optional since 0.18
    assert f"uv tool install 'cardak[all] @ git+{REPO}'" in PAGE.read_text()


def test_sections_in_order() -> None:
    # #106: the tagline, the video beside the install, then the carousel of screens
    tags = _page()
    line: dict[str | None, int] = {}
    for t, a, n in tags.tags:  # the first of each: the wordmark's picture, not the phone slide's
        line.setdefault(t if t in ("picture", "footer") else a.get("id"), n)
    order = [
        line["release-banner"],
        line["picture"],
        line["tagline"],
        line["demo"],
        line["install"],
        line["screens"],
    ]
    assert order == sorted(order)
    assert order[-1] < line["footer"]


def test_has_a_description_and_social_tags() -> None:
    tags = _page()
    assert _find(tags, "meta", name="description")
    for prop in ("og:title", "og:description", "og:image"):
        assert _find(tags, "meta", property=prop), prop
    # crawlers need an absolute URL for the preview image; the only absolute asset URL
    image = _find(tags, "meta", property="og:image")[0]["content"]
    assert image == "https://brlauuu.dev/cardak/assets/cardak-og.png"


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


TAGLINE = "Your control deck for multi-project agentic development."
FULL = f"uv tool install 'cardak[all] @ git+{REPO}'"
BASIC = f"uv tool install git+{REPO}"
SLIDES = ("overview", "waiting", "summaries", "usage", "board", "colors", "phone")


def _content(tags: _Tags, **want: str) -> str:
    return _find(tags, "meta", **want)[0]["content"]


def test_tagline() -> None:
    tags = _page()
    text = PAGE.read_text()
    found = re.search(r'id="tagline"[^>]*>(.*?)</p>', text, flags=re.S)
    assert found and re.sub(r"<[^>]+>", "", found.group(1)).strip() == TAGLINE
    assert TAGLINE in re.search(r"<title>(.*?)</title>", text).group(1)  # type: ignore[union-attr]
    assert _content(tags, name="description").startswith(TAGLINE)
    assert _content(tags, property="og:description") == TAGLINE


def test_link_preview() -> None:
    tags = _page()
    assert _content(tags, property="og:url") == "https://brlauuu.dev/cardak/"
    assert _content(tags, property="og:image:width") == "1200"
    assert _content(tags, property="og:image:height") == "630"


def test_install_panel() -> None:
    text = PAGE.read_text()
    section = text[text.index('id="install"') : text.index('id="screens"')]
    for command in (FULL, BASIC):
        found = re.search(r"<code[^>]*>" + re.escape(command) + r"</code>", section)
        assert found, command
    # each command has a copy button next to it, which works on phones too (#106)
    assert section.count("<button") >= 2 and section.count("data-copy") >= 2
    assert f'href="{REPO}"' in section
    assert "cardak wezterm install" in section and "cardak hooks install" in section


def test_carousel() -> None:
    tags = _page()
    region = _find(tags, "section", id="screens")
    assert region and region[0].get("aria-roledescription") == "carousel"
    assert region[0].get("aria-label")
    imgs = [
        a for t, a, _ in tags.tags if t == "img" and a.get("src", "").startswith("assets/screens/")
    ]
    assert [Path(a["src"]).stem for a in imgs] == list(SLIDES)
    for i, a in enumerate(imgs):
        assert a.get("alt") and a.get("width") and a.get("height"), a
        assert (a.get("loading") == "lazy") == (i > 0), a
    labels = [a.get("aria-label", "") for t, a, _ in tags.tags if t == "button"]
    assert any("Previous" in x for x in labels) and any("Next" in x for x in labels)
    assert "setInterval" not in PAGE.read_text()  # it moves only when the visitor moves it


def test_slides_open_full_size() -> None:
    # a 160-column deck is small on a phone: a tap opens the image to zoom in (#106)
    text = PAGE.read_text()
    for name in SLIDES:
        assert re.search(
            rf'<a [^>]*href="assets/screens/{name}\.webp"[^>]*>\s*(<picture>.*?)?<img',
            text,
            flags=re.S,
        ), name


def test_phone_slide_is_upright_on_phones() -> None:
    tags = _page()
    assert _find(tags, "source", srcset="assets/screens/phone-portrait.webp")


def test_feature_list_is_gone() -> None:
    assert 'class="features"' not in PAGE.read_text()


def test_script_is_inline_and_small() -> None:
    scripts = re.findall(r"<script>(.*?)</script>", PAGE.read_text(), flags=re.S)
    assert len(scripts) == 1 and len(scripts[0]) < 6000
    assert "eval(" not in scripts[0]


def _css() -> str:
    found = re.search(r"<style>(.*?)</style>", PAGE.read_text(), flags=re.S)
    assert found
    return found.group(1)


def _rule(selector: str) -> str:
    found = re.search(r"(?m)^\s*" + re.escape(selector) + r"\s*\{([^}]*)\}", _css())
    assert found, selector
    return found.group(1)


def test_slides_keep_their_own_height() -> None:
    # the upright phone slide must not stretch the others on a phone (#106 review)
    assert "align-items: flex-start" in _rule(".track")


def test_a_picture_fills_its_frame() -> None:
    # the phone slide's <picture> is the frame's flex item: without this its img overflows
    rule = _rule(".shot picture")
    assert "display: contents" in rule


def test_tap_targets_are_44px() -> None:
    for selector in (".dots button", ".copy", ".steps button"):
        rule = _rule(selector)
        sizes = [int(v) for v in re.findall(r"(?:min-)?(?:height|width):\s*(\d+)px", rule)]
        assert sizes and min(sizes) >= 44, (selector, rule)


def test_arrow_keys_leave_browser_shortcuts_alone() -> None:
    # Alt+← is "back" on Linux and Windows; the carousel only takes plain arrows
    script = re.search(r"<script>(.*?)</script>", PAGE.read_text(), flags=re.S).group(1)  # type: ignore[union-attr]
    assert "e.altKey" in script and "e.ctrlKey" in script and "e.metaKey" in script


def test_track_takes_the_shown_slides_height() -> None:
    # a flex row is as tall as its tallest item: the script fits it to the slide on screen,
    # again as images load, so the dots follow the caption on a phone (#106 review)
    script = re.search(r"<script>(.*?)</script>", PAGE.read_text(), flags=re.S).group(1)  # type: ignore[union-attr]
    assert "track.style.height" in script
    assert 'addEventListener("load"' in script
    assert "overflow-y: hidden" in _rule(".track")


def test_release_banner() -> None:
    # as on the owner's other pages (#164): hidden until the script finds the release recent
    tags = _page()
    banner = _find(tags, "a", id="release-banner")
    assert banner and "hidden" in banner[0]
    assert banner[0]["href"] == f"{REPO}/releases/tag/v__VERSION__"
    assert banner[0]["data-released"] == "__RELEASED__"
    script = re.search(r"<script>(.*?)</script>", PAGE.read_text(), flags=re.S).group(1)  # type: ignore[union-attr]
    assert "WINDOW_DAYS = 30" in script and "banner.hidden = false" in script


def test_header_is_centered() -> None:
    assert "text-align: center" in _rule("header")
    # one line from tablet width up; a phone may wrap it once (#164)
    assert re.search(r"@media \(min-width: 700px\) \{\s*#tagline \{ white-space: nowrap;", _css())


def test_video_is_full_width_with_the_install_below() -> None:
    text = PAGE.read_text()
    assert 'class="hero"' not in text
    assert text.index('id="demo"') < text.index('id="install"')


def test_install_tabs() -> None:
    # one card, Full and Basic as tabs, Full selected (#164)
    tags = _page()
    assert _find(tags, "div", role="tablist")
    tabs = _find(tags, "button", role="tab")
    assert [a["id"] for a in tabs] == ["tab-full", "tab-basic"]
    assert [a["aria-selected"] for a in tabs] == ["true", "false"]
    panels = _find(tags, "div", role="tabpanel")
    assert [a["aria-labelledby"] for a in panels] == ["tab-full", "tab-basic"]
    assert "hidden" not in panels[0]  # without JS both show; the script hides the other
    text = PAGE.read_text()
    assert ">Basic<" in text and "Lite" not in text
    assert 'html:not(.js) [role="tablist"]' in _css()


def test_tabs_follow_the_arrow_keys() -> None:
    # the ARIA tabs pattern: ←/→ move between tabs, the selected one is the only one in Tab order
    script = re.search(r"<script>(.*?)</script>", PAGE.read_text(), flags=re.S).group(1)  # type: ignore[union-attr]
    assert '"aria-selected"' in script and "tabIndex" in script
    tabs = _find(_page(), "button", role="tab")
    assert [a.get("tabindex") for a in tabs] == [None, "-1"]


def test_footer_names_the_author_and_the_changelog() -> None:
    text = PAGE.read_text()
    footer = text[text.index("<footer") : text.index("</footer>")]
    assert 'Created by <a href="https://brlauuu.dev">brlauuu</a>' in footer
    assert f'href="{REPO}/blob/main/CHANGELOG.md">changelog</a>' in footer
    assert "v__VERSION__" in footer
    assert footer.index("Created by") < footer.index("changelog")
    assert "text-align: center" in _rule("footer")


def test_captions_are_centered() -> None:
    assert "text-align: center" in _rule("figcaption")
