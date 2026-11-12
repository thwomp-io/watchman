"""The error types every lane raises, and the one shape they are reported in.

A failure a user can act on is a `HarnessError`: a message, an optional hint, and a `kind` that says
whose problem it is. The CLI prints it in a line or two, the console door relays the same fields,
and the web console renders them as a widget's error state. Anything else that escapes a command
is a bug, reported as kind `internal` with the traceback held back unless asked for.
"""

from __future__ import annotations

from typing import ClassVar


class HarnessError(RuntimeError):
    """A failure with a cause the user can read and, usually, act on."""

    kind: ClassVar[str] = "error"
    exit_code: ClassVar[int] = 1

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.hint = hint


class UsageError(HarnessError):
    """The command was called wrong: options that conflict or a value in the wrong shape. Exits 2,
    the Click convention for a usage error, so a script can tell a bad call from a failed one."""

    exit_code: ClassVar[int] = 2


class ProviderError(HarnessError):
    """An outside source couldn't fulfil a request: a missing key, an upstream error, no data.

    `transient` marks a failure that says nothing about the data itself (a throttle, a timeout), so
    a caller that caches misses knows not to cache this one.
    """

    kind: ClassVar[str] = "source"

    def __init__(self, message: str, *, hint: str | None = None, transient: bool = False) -> None:
        super().__init__(message, hint=hint)
        self.transient = transient
