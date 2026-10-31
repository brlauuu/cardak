from dataclasses import dataclass, field
from pathlib import Path

import pytest

from cardak.collectors.github import Closed, Merged, RepoData
from cardak.core.config import ProviderSettings, SummarySettings
from cardak.core.pm import NOTHING_DONE, Bullet, Done
from cardak.core.pm_done import (
    DONE_SYSTEM_PROMPT,
    MAX_BULLETS,
    done_hash,
    done_input,
    fallback,
    parse_done,
    write_done,
)
from cardak.core.prices import BUILTIN
from cardak.core.store import PmSummaryRow, Store
from cardak.core.summary import MODEL, ProviderTools, cost

U = "https://github.com/example/webshop"
NOW = 2_000_000_000.0
DATA = RepoData(
    (),
    (),
    (
        Merged(203, "Fix tax rounding", f"{U}/pull/203", NOW - 100, ()),
        Merged(201, "Add order export", f"{U}/pull/201", NOW - 300, (190,)),
    ),
    (
        Closed(150, "Old idea", f"{U}/issues/150", NOW - 200),
        Closed(190, "Export orders", f"{U}/issues/190", NOW - 299),
    ),
)
EMPTY = RepoData((), (), (), ())


@dataclass
class R:
    text: str | None
    stop_reason: str | None = "end_turn"
    model: str = MODEL
    input_tokens: int = 100
    output_tokens: int = 20


@dataclass
class Stub:
    reply: R | Exception = field(default_factory=lambda: R("- Fixed tax rounding (#203)"))
    calls: list[tuple[str, str]] = field(default_factory=list)

    def make(self, name: str, settings: ProviderSettings, key: str | None) -> "Stub":
        return self

    def summarize(self, system: str, user: str) -> R:
        self.calls.append((system, user))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def tools(stub: Stub) -> ProviderTools:
    return ProviderTools(stub.make, lambda url: [], lambda: None, lambda: BUILTIN, lambda n: None)


@pytest.fixture
def keys(tmp_path: Path) -> Path:
    key = tmp_path / "anthropic_key"
    key.write_text("sk-test\n")
    key.chmod(0o600)
    return tmp_path


def run(stub: Stub, store: Store, keys: Path, data: RepoData = DATA, limit: float = 5.0):  # noqa: ANN201
    return write_done(
        "example/webshop",
        data,
        NOW,
        summary=SummarySettings(limit),
        keys_dir=keys,
        tools=tools(stub),
        store=store,
    )


def test_done_input_lines() -> None:
    assert done_input(DATA).splitlines() == [
        "PR #201 merged: Add order export (closes #190)",
        "Issue #190 closed: Export orders",
        "Issue #150 closed: Old idea",
        "PR #203 merged: Fix tax rounding",
    ]
    assert done_hash(DATA) != done_hash(EMPTY) and len(done_hash(DATA)) == 64


def test_prompt_asks_for_bullets_with_numbers() -> None:
    assert "- " in DONE_SYSTEM_PROMPT and "(#203, #204)" in DONE_SYSTEM_PROMPT


def test_parse_drops_invented_refs() -> None:
    got = parse_done("Here you go:\n- Fixed tax rounding (#203)\n- Did magic (#999)\nnoise", DATA)
    assert got == (Bullet("Fixed tax rounding", (203,), f"{U}/pull/203"),)


def test_parse_keeps_issue_refs_and_order() -> None:
    got = parse_done("* Exported orders (#201, #190)\n- Dropped an idea (#150).", DATA)
    assert [(b.text, b.refs) for b in got] == [
        ("Exported orders", (201, 190)),
        ("Dropped an idea", (150,)),
    ]
    assert got[1].url == f"{U}/issues/150"


def test_parse_caps_bullets_and_length() -> None:
    assert len(parse_done("\n".join("- x (#203)" for _ in range(9)), DATA)) == MAX_BULLETS
    [long] = parse_done("- " + "y" * 400 + " (#203)", DATA)
    assert len(long.text) <= 160


def test_fallback_lists_prs_then_lone_closed_issues() -> None:
    done = fallback(DATA, "why")
    assert [b.refs for b in done.bullets] == [(203,), (201,), (150,)]  # #190 was closed by #201
    assert done.note == "why" and done.bullets[0].text == "Fix tax rounding"
    assert fallback(EMPTY, "why") == Done((), NOTHING_DONE)


def test_nothing_done_makes_no_call(tmp_path: Path, keys: Path) -> None:
    stub = Stub()
    assert run(stub, Store(tmp_path / "db"), keys, EMPTY) == (Done((), NOTHING_DONE), True)
    assert stub.calls == []


def test_summary_is_recorded_as_spend(tmp_path: Path, keys: Path) -> None:
    store = Store(tmp_path / "db")
    stub = Stub()
    done, settled = run(stub, store, keys)
    assert settled and done.bullets[0].refs == (203,) and done.note is None
    assert stub.calls[0] == (DONE_SYSTEM_PROMPT, done_input(DATA))
    expected = cost("anthropic", ProviderSettings(), MODEL, 100, 20, BUILTIN)
    assert expected > 0 and store.spend_since(0) == pytest.approx(expected)


def test_cap_reached_makes_no_call(tmp_path: Path, keys: Path) -> None:
    store = Store(tmp_path / "db")
    store.add_pm_summary(PmSummaryRow("r", "h", "m", 1, 1, 2.0, NOW - 10))
    stub = Stub()
    done, settled = run(stub, store, keys, limit=1.0)
    assert not settled and stub.calls == [] and done.note == "monthly summary cap reached"
    assert [b.refs for b in done.bullets][0] == (203,)


def test_not_ready_lists_prs(tmp_path: Path) -> None:
    stub = Stub()
    done, settled = run(stub, Store(tmp_path / "db"), tmp_path / "no-keys")
    assert not settled and stub.calls == []
    assert done.note == "summaries are off: set one up with s" and done.bullets[0].refs == (203,)


def test_no_tools_is_not_ready(tmp_path: Path, keys: Path) -> None:
    done, settled = write_done(
        "r",
        DATA,
        NOW,
        summary=SummarySettings(),
        keys_dir=keys,
        tools=None,
        store=Store(tmp_path / "db"),
    )
    assert not settled and done.note == "summaries are off: set one up with s"


def test_failure_is_recorded_and_retried(tmp_path: Path, keys: Path) -> None:
    store = Store(tmp_path / "db")
    done, settled = run(Stub(RuntimeError("HTTP 500: busy")), store, keys)
    assert not settled and done.note == "the summary failed: HTTP 500: busy"
    row = store._con.execute("SELECT error, cost_usd FROM pm_summaries").fetchone()
    assert row[0] == "RuntimeError: HTTP 500: busy" and row[1] == 0


def test_unusable_reply_falls_back(tmp_path: Path, keys: Path) -> None:
    store = Store(tmp_path / "db")
    done, settled = run(Stub(R("I can't help with that")), store, keys)
    assert settled and done.note == "the summary couldn't be used"
    assert store.spend_since(0) > 0  # it still cost money
