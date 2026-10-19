"""The door's failure type, shared by the dispatch table and the command packages it merges."""


class CommandError(Exception):
    """A handler failure reported to the client as the Tauri error-string shape (HTTP 400)."""
