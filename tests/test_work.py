from cardak.core.pm import Card
from cardak.core.work import TODO_ONLY, command, refusal


def card(kind: str = "issue", number: int = 120) -> Card:
    return Card(kind, number, "Issue to PR", (), 0.0, f"https://github.com/o/r/issues/{number}")  # type: ignore[arg-type]


def test_command_starts_claude_with_the_prompt_quoted() -> None:
    assert command("/issue-to-pr {number}", 120) == "claude '/issue-to-pr 120'"


def test_command_fills_every_number_and_quotes_the_shell_out() -> None:
    assert command("fix #{number}; see {number}", 7) == "claude 'fix #7; see 7'"
    assert command("it's {number}", 7) == """claude 'it'"'"'s 7'"""


def test_a_todo_issue_is_allowed() -> None:
    assert refusal(0, card()) is None


def test_an_onit_issue_is_already_on_it() -> None:
    assert refusal(1, card(number=205)) == "#205 is already on it."


def test_prs_done_and_nothing_selected_are_refused() -> None:
    assert refusal(1, card("pr", 210)) == TODO_ONLY
    assert refusal(2, None) == TODO_ONLY
    assert refusal(0, None) == TODO_ONLY
    assert TODO_ONLY == "w starts work on a todo issue."
