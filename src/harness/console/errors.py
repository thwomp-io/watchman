"""The door's failure type, shared by the dispatch table and the command packages it merges."""

from __future__ import annotations

import json
from typing import Any


class CommandError(Exception):
    """A handler failure, reported to the client as HTTP 400 with the fields of `to_response`.

    `message` is the sentence a person reads. `kind` says whose problem it is (`source`: something
    outside didn't answer; `internal`: a bug here; `error`: everything else). `hint` is what to do
    about it, and `detail` is the raw output for whoever has to debug it.
    """

    def __init__(
        self, message: str, *, kind: str = "error", hint: str | None = None, detail: str | None = None
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.hint = hint
        self.detail = detail

    def to_response(self) -> dict[str, Any]:
        return {"error": str(self), "kind": self.kind, "hint": self.hint, "detail": self.detail}


_DETAIL_LIMIT = 4000
_MESSAGE_LIMIT = 300


def from_stderr(label: str, returncode: int, stderr: str) -> CommandError:
    """Turn a failed spawn's stderr into a `CommandError`.

    A harness command run with `HARNESS_ERROR_FORMAT=json` ends its stderr with one JSON line
    carrying the error's own fields. Any other program gets its last line as the message and the
    rest as detail, so a widget never shows a wall of output where a sentence belongs.
    """
    lines = [ln for ln in stderr.strip().splitlines() if ln.strip()]
    detail = stderr.strip()[-_DETAIL_LIMIT:] or None
    if lines:
        try:
            reported = json.loads(lines[-1]).get("error")
        except (json.JSONDecodeError, AttributeError):
            reported = None
        if isinstance(reported, dict) and isinstance(reported.get("message"), str):
            earlier = "\n".join(lines[:-1])[-_DETAIL_LIMIT:] or None
            return CommandError(
                reported["message"],
                kind=str(reported.get("kind") or "error"),
                hint=reported.get("hint") if isinstance(reported.get("hint"), str) else None,
                detail=earlier,
            )
        return CommandError(lines[-1][:_MESSAGE_LIMIT], detail=detail)
    return CommandError(f"{label} exited {returncode} without saying why", detail=detail)
