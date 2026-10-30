"""Summaries: set up, test and switch who writes the waiting lines (#47, #48, #49)."""

import time
from dataclasses import dataclass
from typing import Protocol

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, OptionList, Select, Static

from cardak.core.config import KEYED, OLLAMA_URL, PROVIDERS, ProviderSettings, SummarySettings
from cardak.core.summary import (
    FREE,
    MODEL,
    SAMPLE_INPUT,
    SYSTEM_PROMPT,
    KeyProblem,
    ProviderTools,
    cost_note,
    validate,
)
from cardak.ui.confirm import ConfirmScreen

KINDS = {
    "anthropic": "Anthropic API",
    "ollama": "a local Ollama server",
    "fireworks": "Fireworks API",
    "openai": "an OpenAI-compatible API",
    "codex": "the Codex CLI",
}
FIELDS = {
    "anthropic": ("model", "key"),
    "ollama": ("url", "model"),
    "fireworks": ("model", "key"),
    "openai": ("url", "model", "key"),
    "codex": ("model",),
}
NEEDED = {"ollama": ("model",), "fireworks": ("model",), "openai": ("url", "model")}
KEYS_HINT = "enter use · t test · n set up · e edit · del remove · esc close"


class SummaryControl(Protocol):
    """What the screen reads and writes; LiveSource in the deck."""

    def summary_settings(self) -> SummarySettings: ...

    def is_set_up(self, name: str) -> bool: ...

    def summary_spend(self) -> float | None: ...

    def use_provider(self, name: str) -> str | None: ...

    def save_provider(
        self, name: str, url: str | None, model: str | None, key: str | None
    ) -> str | None: ...

    def remove_provider(self, name: str) -> str | None: ...


@dataclass(frozen=True)
class TestResult:
    ok: bool
    seconds: float
    text: str


class SummariesScreen(ModalScreen[None]):
    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("t", "test", "Test"),
        Binding("n", "form", "Set up"),
        Binding("e", "form", "Edit"),
        Binding("delete", "remove", "Remove"),
    ]
    DEFAULT_CSS = """
    SummariesScreen { align: center middle; }
    #summaries {
        width: 100; height: 28; border: round $accent; background: $surface; padding: 0 1;
    }
    #summaries-top { text-style: bold; }
    #message { color: $warning; height: auto; }
    #panes { height: 1fr; }
    #providers { width: 26; height: 100%; }
    #right { padding: 0 1; height: 100%; }
    #detail { height: auto; }
    #form { height: auto; display: none; }
    #form Input, #form Select { margin-bottom: 0; }
    #form-note { color: $text-muted; }
    #summaries-keys { color: $text-muted; }
    """

    def __init__(self, control: SummaryControl, tools: ProviderTools) -> None:
        super().__init__()
        self._control = control
        self._tools = tools
        self._tests: dict[str, TestResult | None] = {}  # None: running
        self._message: str | None = None
        self._form_for: str | None = None
        self._codex: str | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="summaries"):
            yield Static("", id="summaries-top")
            yield Static("", id="message")
            with Horizontal(id="panes"):
                yield OptionList(id="providers")
                with Vertical(id="right"):
                    yield Static("", id="detail")
                    with Vertical(id="form"):
                        yield Input(placeholder="url", id="f-url")
                        yield Select([], id="f-models", prompt="model")
                        yield Input(placeholder="model", id="f-model")
                        yield Input(placeholder="key", id="f-key", password=True)
                        yield Static("", id="form-note")
                        with Horizontal():
                            yield Button("Test", id="b-test")
                            yield Button("Save", id="b-save")
                            yield Button("Save and use", id="b-use", variant="primary")
            yield Static(KEYS_HINT, id="summaries-keys")

    def on_mount(self) -> None:
        self._refresh()
        self.query_one("#providers", OptionList).focus()
        self._find_codex()

    @work(thread=True, exit_on_error=False)
    def _find_codex(self) -> None:
        version = self._tools.codex_version()
        self.app.call_from_thread(self._codex_found, version)

    def _codex_found(self, version: str | None) -> None:
        self._codex = version
        self._refresh()

    # --- what's shown

    @property
    def _highlighted(self) -> str:
        index = self.query_one("#providers", OptionList).highlighted
        return PROVIDERS[index or 0]

    def _row(self, name: str, summary: SummarySettings) -> Text:
        if name == summary.provider:
            return Text(f"● {name:<11} in use")
        if self._control.is_set_up(name):
            return Text(f"○ {name:<11} set up")
        return Text(f"· {name:<11} —")

    def _refresh(self) -> None:
        summary = self._control.summary_settings()
        options = self.query_one("#providers", OptionList)
        keep = options.highlighted or 0
        options.clear_options()
        options.add_options([self._row(name, summary) for name in PROVIDERS])
        options.highlighted = keep
        spend = self._control.summary_spend()
        top = "Summaries"
        if spend is not None:
            top += f" · this month ${spend:.2f} of ${summary.monthly_limit_usd:.2f}"
        self.query_one("#summaries-top", Static).update(Text(top))
        message = self._message or summary.error or ""
        self.query_one("#message", Static).update(Text(message))
        self._show_detail()

    def _stored(self, name: str) -> ProviderSettings:
        return self._control.summary_settings().providers.get(name, ProviderSettings())

    def _shown(self, name: str) -> ProviderSettings:
        """The form's values while it's open for this provider, else the saved ones."""
        return self._form_settings() if self._form_for == name else self._stored(name)

    def _show_detail(self) -> None:
        name = self._highlighted
        set_up = self._control.is_set_up(name) or self._form_for == name
        text = Text()
        text.append(name, style="bold")
        text.append(f"  ({KINDS[name]})\n")
        settings = self._shown(name)
        if not set_up:
            text.append("  not set up · n to set it up\n")
        else:
            if settings.url and name != "fireworks":
                text.append(f"  url    {settings.url}\n")
            model = settings.model or (MODEL if name == "anthropic" else None)
            text.append(f"  model  {model or 'codex default'}\n")
            text.append(f"  cost   {cost_note(name, settings, self._tools.prices())}\n")
        if name == "codex":
            text.append(f"  found  {self._codex or 'codex is not on PATH'}\n")
        self._append_test(text, self._tests.get(name, False))
        self.query_one("#detail", Static).update(text)

    def _append_test(self, text: Text, result: TestResult | None | bool) -> None:
        if result is None:
            text.append("\n  testing…\n")
        elif isinstance(result, TestResult):
            mark, style = ("✓", "green") if result.ok else ("✗", "red")
            text.append(f"\n  last test  {mark} {result.seconds:.1f} s\n", style=style)
            text.append(f"  {result.text}\n" if result.ok else f"  {mark} {result.text}\n")

    def _say(self, message: str | None) -> None:
        self._message = message
        self._refresh()

    @on(OptionList.OptionHighlighted, "#providers")
    def _moved(self) -> None:
        if self._form_for is not None and self._form_for != self._highlighted:
            self._close_form()
        self._show_detail()

    # --- use, remove

    @on(OptionList.OptionSelected, "#providers")
    def _use(self) -> None:
        name = self._highlighted
        if not self._control.is_set_up(name):
            self._say(f"{name} isn't set up: press n to set it up")
            return
        self._say(self._control.use_provider(name))

    def action_remove(self) -> None:
        name = self._highlighted
        if name == self._control.summary_settings().provider:
            self._say(f"{name} is in use; switch to another provider first")
            return
        if name not in self._control.summary_settings().providers:
            self._say(f"{name} has no settings to remove")
            return

        def done(yes: bool | None) -> None:
            if yes:
                self._say(self._control.remove_provider(name))

        note = f"Its key file, if any, stays in ~/.config/cardak/{name}_key."
        self.app.push_screen(ConfirmScreen(f"Remove {name}'s settings?", note, ok="remove"), done)

    # --- test

    def action_test(self) -> None:
        name = self._highlighted
        if self._form_for != name and not self._control.is_set_up(name):
            self._say(f"{name} isn't set up: press n to set it up")
            return
        settings = self._shown(name)
        typed = self.query_one("#f-key", Input).value.strip() if self._form_for == name else ""
        try:
            key = typed or (self._tools.read_key(name) if name in KEYED else None)
        except KeyProblem as e:
            self._say(f"{name}_key: {e}")
            return
        self._tests[name] = None
        self._say(None)
        self._run_test(name, settings, key)

    @work(thread=True, exit_on_error=False, group="summary-test")
    def _run_test(self, name: str, settings: ProviderSettings, key: str | None) -> None:
        start = time.monotonic()
        try:
            reply = self._tools.make(name, settings, key).summarize(SYSTEM_PROMPT, SAMPLE_INPUT)
            line = validate(reply)
            result = TestResult(
                line is not None, time.monotonic() - start, line or "answered, but not one line"
            )
        except Exception as e:  # any provider failure is the test's result
            message = " ".join(str(getattr(e, "message", e)).split())
            result = TestResult(False, time.monotonic() - start, message)
        self.app.call_from_thread(self._tested, name, result)

    def _tested(self, name: str, result: TestResult) -> None:
        self._tests[name] = result
        self._show_detail()

    # --- the form

    def action_form(self) -> None:
        name = self._highlighted
        stored = self._stored(name)
        fields = FIELDS[name]
        url = self.query_one("#f-url", Input)
        url.display = "url" in fields
        url.value = stored.url or (OLLAMA_URL if name == "ollama" else "")
        model = self.query_one("#f-model", Input)
        model.display = "model" in fields
        model.value = stored.model or (MODEL if name == "anthropic" else "")
        model.placeholder = "model (empty: codex's default)" if name == "codex" else "model"
        self.query_one("#f-models", Select).display = False
        key = self.query_one("#f-key", Input)
        key.display = "key" in fields
        key.value = ""
        key.placeholder = self._key_placeholder(name)
        self.query_one("#form-note", Static).update(Text(self._form_note(name)))
        self._form_for = name
        self.query_one("#form").display = True
        self._show_detail()
        if name == "ollama":
            self._list_models(url.value, stored.model)
        first = url if url.display else model
        first.focus()

    def _key_placeholder(self, name: str) -> str:
        if self._control.is_set_up(name) and name != "ollama":
            return "key (empty: keep the saved one)"
        return "key (optional for a local server)" if name == "openai" else "key"

    def _form_note(self, name: str) -> str:
        notes = []
        if name in KEYED:
            notes.append(f"A key is saved to ~/.config/cardak/{name}_key, readable by you only.")
        if name not in FREE:
            notes.append("On a paid API a test costs a fraction of a cent; it isn't counted.")
        if name == "codex":
            notes.append(f"Found: {self._codex or 'codex is not on PATH'}.")
        return " ".join(notes)

    @work(thread=True, exit_on_error=False, group="ollama-models")
    def _list_models(self, url: str, current: str | None) -> None:
        try:
            found: list[str] | str = self._tools.list_models(url)
        except Exception as e:
            found = " ".join(str(e).split())
        self.app.call_from_thread(self._models_listed, found, current)

    def _models_listed(self, found: list[str] | str, current: str | None) -> None:
        if self._form_for != "ollama":
            return
        select = self.query_one("#f-models", Select)
        model = self.query_one("#f-model", Input)
        if isinstance(found, str) or not found:
            why = found or "no models on the server"
            self._say(f"couldn't list the server's models ({why}); type one")
            return
        select.set_options([(name, name) for name in found])
        select.value = current if current in found else found[0]
        select.display = True
        model.display = False

    def _form_settings(self) -> ProviderSettings:
        name = self._form_for or self._highlighted
        url = self.query_one("#f-url", Input).value.strip() if "url" in FIELDS[name] else ""
        select = self.query_one("#f-models", Select)
        if select.display and isinstance(select.value, str):
            model = select.value
        else:
            model = self.query_one("#f-model", Input).value.strip()
        stored = self._stored(name)
        if name == "fireworks":
            url = stored.url or ""
        return ProviderSettings(url or None, model or None)

    def _close_form(self) -> None:
        self._form_for = None
        self.query_one("#form").display = False
        self.query_one("#providers", OptionList).focus()

    @on(Button.Pressed, "#b-test")
    def _form_test(self) -> None:
        self.action_test()

    @on(Button.Pressed, "#b-save")
    def _form_save(self) -> None:
        self._save(use=False)

    @on(Button.Pressed, "#b-use")
    def _form_save_and_use(self) -> None:
        self._save(use=True)

    def _save(self, use: bool) -> None:
        name = self._form_for
        if name is None:
            return
        settings = self._form_settings()
        missing = [f for f in NEEDED.get(name, ()) if not getattr(settings, f)]
        if missing:
            self._say(f"{name} needs a {' and a '.join(missing)}")
            return
        key = self.query_one("#f-key", Input).value.strip() or None
        url = settings.url if name in ("ollama", "openai") else None
        error = self._control.save_provider(name, url, settings.model, key)
        if error is None and use:
            error = self._control.use_provider(name)
        if error is None:
            self._close_form()
        self._say(error)

    def action_close(self) -> None:
        if self._form_for is not None:
            self._close_form()
            self._refresh()
            return
        self.dismiss(None)
