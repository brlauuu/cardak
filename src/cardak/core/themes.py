"""Accent and tint colors derived from a WezTerm color scheme. Pure, except SchemeStore."""

from collections.abc import Mapping
from dataclasses import dataclass

from cardak.core.config import Project

MIN_CONTRAST = 1.5


@dataclass(frozen=True)
class Scheme:
    name: str
    background: str
    foreground: str
    cursor_bg: str | None
    ansi: tuple[str, ...]


def _rgb(color: str) -> tuple[int, int, int]:
    h = color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _luminance(color: str) -> float:
    def channel(c: int) -> float:
        v = c / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in _rgb(color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    """WCAG contrast ratio, 1.0 (same) to 21.0 (black on white)."""
    la, lb = _luminance(a), _luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def default_accent(s: Scheme) -> str:
    c = s.cursor_bg
    if (
        c
        and contrast(c, s.background) >= MIN_CONTRAST
        and contrast(c, s.foreground) >= MIN_CONTRAST
    ):
        return c
    return s.ansi[5] if len(s.ansi) > 5 else s.foreground


def tint(background: str, accent: str, amount: float = 0.10) -> str:
    mixed = (
        round(b + (a - b) * amount) for b, a in zip(_rgb(background), _rgb(accent), strict=True)
    )
    return "#" + "".join(f"{c:02x}" for c in mixed)


def project_accent(project: Project, schemes: Mapping[str, Scheme]) -> str | None:
    if project.accent:
        return project.accent
    scheme = schemes.get(project.theme) if project.theme else None
    return default_accent(scheme) if scheme else None


def project_tint(project: Project, schemes: Mapping[str, Scheme]) -> str | None:
    if project.tint:
        return project.tint
    scheme = schemes.get(project.theme) if project.theme else None
    if scheme is None:
        return None
    return tint(scheme.background, project_accent(project, schemes) or default_accent(scheme))


class SchemeStore:
    """The scheme list shared by the deck and its source; replaced whole, never mutated."""

    def __init__(self, schemes: Mapping[str, Scheme] | None = None) -> None:
        self._schemes: Mapping[str, Scheme] = dict(schemes or {})

    def get(self) -> Mapping[str, Scheme]:
        return self._schemes

    def set(self, schemes: Mapping[str, Scheme]) -> None:
        self._schemes = dict(schemes)
