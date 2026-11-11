"""What `w` in the PM view types into a new tab to start work on an issue (#120). Pure.

The tab opens like `x`'s (`core/cleanup`): a plain shell in the project, with
`claude '<prompt>'` typed into it. The prompt is `[pm] issue_command` with the issue's number.
"""

import shlex

from cardak.core.pm import Card

TODO_ONLY = "w starts work on a todo issue."
TODO, ONIT = 0, 1  # the PM view's columns


def command(template: str, number: int) -> str:
    """The line typed into the new tab: claude with the prompt, shell-quoted."""
    return "claude " + shlex.quote(template.replace("{number}", str(number)))


def refusal(column: int, card: Card | None) -> str | None:
    """Why `w` does nothing on this card, or None for an issue nobody is on yet."""
    if card is not None and card.kind == "issue":
        if column == TODO:
            return None
        if column == ONIT:
            return f"#{card.number} is already on it."
    return TODO_ONLY
