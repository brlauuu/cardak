from dataclasses import dataclass, field, replace
from pathlib import Path

from textual.app import App
from textual.widgets import Button, Input, OptionList, Select, Static

from cardak.core.config import ProviderSettings, SummarySettings, load
from cardak.core.prices import BUILTIN
from cardak.core.source import LiveSource
from cardak.core.summary import SAMPLE_INPUT, SYSTEM_PROMPT, ProviderTools
from cardak.llm.http import ProviderError
from cardak.ui.confirm import ConfirmScreen
from cardak.ui.summaries import SummariesScreen

LINE = "Wants to delete build/; approve the rm -rf"


@dataclass
class R:
    text: str | None
    model: str = "m"
    stop_reason: str | None = "end_turn"
    input_tokens: int = 10
    output_tokens: int = 5


@dataclass
class FakeControl:
    summary: SummarySettings = field(default_factory=SummarySettings)
    keyed: set[str] = field(default_factory=set)  # providers whose key file exists
    spend: float | None = 1.84
    calls: list[tuple[object, ...]] = field(default_factory=list)

    def summary_settings(self) -> SummarySettings:
        return self.summary

    def is_set_up(self, name: str) -> bool:
        return name in self.keyed if name == "anthropic" else name in self.summary.providers

    def summary_spend(self) -> float | None:
        return self.spend

    def use_provider(self, name: str) -> str | None:
        self.calls.append(("use", name))
        if not self.is_set_up(name):
            return f"{name!r} isn't set up"
        self.summary = replace(self.summary, provider=name)
        return None

    def save_provider(
        self, name: str, url: str | None, model: str | None, key: str | None
    ) -> str | None:
        self.calls.append(("save", name, url, model, key))
        providers = dict(self.summary.providers) | {name: ProviderSettings(url, model)}
        self.summary = replace(self.summary, providers=providers)
        if key:
            self.keyed.add(name)
        return None

    def remove_provider(self, name: str) -> str | None:
        self.calls.append(("remove", name))
        if name == self.summary.provider:
            return f"{name} is in use; switch to another provider first"
        providers = {k: v for k, v in self.summary.providers.items() if k != name}
        self.summary = replace(self.summary, providers=providers)
        return None


@dataclass
class FakeTools:
    reply: R | Exception = field(default_factory=lambda: R(LINE))
    models: list[str] | Exception = field(default_factory=lambda: ["gemma4:e4b", "qwen2.5:3b"])
    made: list[tuple[str, ProviderSettings, str | None]] = field(default_factory=list)
    sent: list[tuple[str, str]] = field(default_factory=list)

    def tools(self) -> ProviderTools:
        return ProviderTools(
            make=self.make,
            list_models=self.list_models,
            codex_version=lambda: "codex-cli 0.160.0",
            prices=lambda: BUILTIN,
            read_key=lambda name: "k" if name in ("anthropic", "fireworks") else None,
        )

    def make(self, name: str, settings: ProviderSettings, key: str | None) -> "FakeTools":
        self.made.append((name, settings, key))
        return self

    def summarize(self, system: str, user: str) -> R:
        self.sent.append((system, user))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply

    def list_models(self, url: str) -> list[str]:
        if isinstance(self.models, Exception):
            raise self.models
        return self.models


class Host(App[None]):
    def __init__(self, control: object, tools: ProviderTools) -> None:
        super().__init__()
        self.control, self.tools = control, tools

    def on_mount(self) -> None:
        self.push_screen(SummariesScreen(self.control, self.tools))  # type: ignore[arg-type]


OLLAMA = ProviderSettings("http://127.0.0.1:11434", "gemma4:e4b")


def two_set_up() -> FakeControl:
    summary = SummarySettings(providers={"ollama": OLLAMA, "codex": ProviderSettings()})
    return FakeControl(summary, keyed={"anthropic"})


def rows(app: App[None]) -> list[str]:
    options = app.screen.query_one("#providers", OptionList)
    return [str(options.get_option_at_index(i).prompt) for i in range(options.option_count)]


def text_of(app: App[None], selector: str) -> str:
    return str(app.screen.query_one(selector, Static).render())


async def move_to(app: App[None], pilot, name: str) -> None:  # noqa: ANN001
    options = app.screen.query_one("#providers", OptionList)
    options.highlighted = ["anthropic", "ollama", "fireworks", "openai", "codex"].index(name)
    await pilot.pause()


async def test_lists_providers_in_order_with_markers() -> None:
    app = Host(two_set_up(), FakeTools().tools())
    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause()
        assert [r.split()[:2] for r in rows(app)] == [
            ["●", "anthropic"],
            ["○", "ollama"],
            ["·", "fireworks"],
            ["·", "openai"],
            ["○", "codex"],
        ]
        assert "in use" in rows(app)[0] and "set up" in rows(app)[1]
        assert "this month $1.84 of $5.00" in text_of(app, "#summaries-top")


async def test_detail_shows_settings_and_cost() -> None:
    app = Host(two_set_up(), FakeTools().tools())
    async with app.run_test(size=(110, 30)) as pilot:
        await move_to(app, pilot, "ollama")
        detail = text_of(app, "#detail")
        assert "http://127.0.0.1:11434" in detail and "gemma4:e4b" in detail
        assert "local, $0" in detail
        await move_to(app, pilot, "codex")
        assert "your Codex plan, $0" in text_of(app, "#detail")


async def test_enter_switches_provider() -> None:
    control = two_set_up()
    app = Host(control, FakeTools().tools())
    async with app.run_test(size=(110, 30)) as pilot:
        await move_to(app, pilot, "ollama")
        await pilot.press("enter")
        await pilot.pause()
        assert control.calls == [("use", "ollama")]
        assert rows(app)[1].split()[0] == "●" and rows(app)[0].split()[0] == "○"


async def test_enter_refuses_unset() -> None:
    control = two_set_up()
    app = Host(control, FakeTools().tools())
    async with app.run_test(size=(110, 30)) as pilot:
        await move_to(app, pilot, "fireworks")
        await pilot.press("enter")
        await pilot.pause()
        assert control.calls == []
        assert "isn't set up" in text_of(app, "#message")


async def test_test_runs_in_background_and_shows_line() -> None:
    fake = FakeTools()
    app = Host(two_set_up(), fake.tools())
    async with app.run_test(size=(110, 30)) as pilot:
        await move_to(app, pilot, "ollama")
        await pilot.press("t")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert fake.made == [("ollama", OLLAMA, None)]
        assert fake.sent == [(SYSTEM_PROMPT, SAMPLE_INPUT)]
        detail = text_of(app, "#detail")
        assert "✓" in detail and LINE in detail


async def test_test_shows_error() -> None:
    fake = FakeTools(reply=ProviderError("can't reach http://127.0.0.1:11434/api/chat"))
    app = Host(two_set_up(), fake.tools())
    async with app.run_test(size=(110, 30)) as pilot:
        await move_to(app, pilot, "ollama")
        await pilot.press("t")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert "✗ can't reach http://127.0.0.1:11434/api/chat" in text_of(app, "#detail")


async def test_test_of_unset_provider_is_refused() -> None:
    fake = FakeTools()
    app = Host(two_set_up(), fake.tools())
    async with app.run_test(size=(110, 30)) as pilot:
        await move_to(app, pilot, "openai")
        await pilot.press("t")
        await pilot.pause()
        assert fake.made == [] and "isn't set up" in text_of(app, "#message")


async def test_setup_ollama_lists_models() -> None:
    app = Host(two_set_up(), FakeTools().tools())
    async with app.run_test(size=(110, 30)) as pilot:
        await move_to(app, pilot, "ollama")
        await pilot.press("e")
        await app.workers.wait_for_complete()
        await pilot.pause()
        select = app.screen.query_one("#f-models", Select)
        assert select.display and select.value == "gemma4:e4b"
        assert app.screen.query_one("#f-url", Input).value == "http://127.0.0.1:11434"
        assert not app.screen.query_one("#f-model", Input).display


async def test_setup_ollama_without_server_falls_back_to_input() -> None:
    fake = FakeTools(models=ProviderError("can't reach http://127.0.0.1:11434/api/tags"))
    control = FakeControl()
    app = Host(control, fake.tools())
    async with app.run_test(size=(110, 30)) as pilot:
        await move_to(app, pilot, "ollama")
        await pilot.press("n")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert not app.screen.query_one("#f-models", Select).display
        model = app.screen.query_one("#f-model", Input)
        assert model.display
        model.value = "qwen2.5:3b"
        app.screen.query_one("#b-save", Button).press()
        await pilot.pause()
        assert control.calls == [("save", "ollama", "http://127.0.0.1:11434", "qwen2.5:3b", None)]


async def test_form_test_uses_the_typed_settings() -> None:
    fake = FakeTools()
    app = Host(FakeControl(), fake.tools())
    async with app.run_test(size=(110, 30)) as pilot:
        await move_to(app, pilot, "openai")
        await pilot.press("n")
        await pilot.pause()
        app.screen.query_one("#f-url", Input).value = "http://127.0.0.1:8080/v1"
        app.screen.query_one("#f-model", Input).value = "local"
        app.screen.query_one("#b-test", Button).press()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert fake.made == [
            ("openai", ProviderSettings("http://127.0.0.1:8080/v1", "local"), None)
        ]
        assert "custom server: no price" in text_of(app, "#detail")


async def test_paid_provider_says_a_test_costs() -> None:
    app = Host(FakeControl(), FakeTools().tools())
    async with app.run_test(size=(110, 30)) as pilot:
        await move_to(app, pilot, "fireworks")
        await pilot.press("n")
        await pilot.pause()
        assert "a test costs a fraction of a cent" in text_of(app, "#form-note")
        assert app.screen.query_one("#f-key", Input).password


async def test_save_and_use_writes_key_and_switches(tmp_path: Path) -> None:
    config = tmp_path / "projects.toml"
    config.write_text("# mine\n")
    source = LiveSource(config, env={}, keys_dir=tmp_path / "keys")
    app = Host(source, FakeTools().tools())
    async with app.run_test(size=(110, 30)) as pilot:
        await move_to(app, pilot, "fireworks")
        await pilot.press("n")
        await pilot.pause()
        app.screen.query_one("#f-model", Input).value = "accounts/fireworks/models/x"
        app.screen.query_one("#f-key", Input).value = "fw-secret"
        app.screen.query_one("#b-use", Button).press()
        await pilot.pause()
        assert rows(app)[2].split()[0] == "●"
    key = tmp_path / "keys" / "fireworks_key"
    assert key.read_text() == "fw-secret\n" and key.stat().st_mode & 0o777 == 0o600
    summary = load(config).summary
    assert summary.provider == "fireworks" and "# mine" in config.read_text()
    assert "fw-secret" not in config.read_text()


async def test_remove_asks_and_refuses_in_use() -> None:
    control = two_set_up()
    control.summary = replace(control.summary, provider="codex")
    app = Host(control, FakeTools().tools())
    async with app.run_test(size=(110, 30)) as pilot:
        await move_to(app, pilot, "codex")
        await pilot.press("delete")
        await pilot.pause()
        assert not isinstance(app.screen, ConfirmScreen)
        assert "in use" in text_of(app, "#message")
        await move_to(app, pilot, "ollama")
        await pilot.press("delete")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.press("enter")
        await pilot.pause()
        assert ("remove", "ollama") in control.calls
        assert rows(app)[1].split()[0] == "·"


async def test_shows_summary_error() -> None:
    control = FakeControl(SummarySettings(error="[summary] provider 'gpt' is not one of …"))
    app = Host(control, FakeTools().tools())
    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause()
        assert "[summary] provider 'gpt'" in text_of(app, "#message")


async def test_escape_closes_form_then_screen() -> None:
    app = Host(two_set_up(), FakeTools().tools())
    async with app.run_test(size=(110, 30)) as pilot:
        await move_to(app, pilot, "codex")
        await pilot.press("e")
        await pilot.pause()
        assert app.screen.query_one("#form").display
        await pilot.press("escape")
        await pilot.pause()
        assert not app.screen.query_one("#form").display
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, SummariesScreen)


async def test_typing_in_the_form_is_not_a_command() -> None:
    fake = FakeTools()
    app = Host(FakeControl(), fake.tools())
    async with app.run_test(size=(110, 30)) as pilot:
        await move_to(app, pilot, "openai")
        await pilot.press("n")
        await pilot.pause()
        app.screen.query_one("#f-model", Input).focus()
        await pilot.press("t", "e", "n")
        await pilot.pause()
        assert app.screen.query_one("#f-model", Input).value == "ten"
        assert fake.made == []


async def test_in_use_without_key_says_so() -> None:
    app = Host(FakeControl(), FakeTools().tools())  # anthropic in use, no anthropic_key
    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause()
        assert rows(app)[0].split() == ["●", "anthropic", "in", "use", "·", "no", "key"]
