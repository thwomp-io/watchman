"""The pocket agent's console-door commands (the CHAT tab) — merged into the door's dispatch table.

The door never runs the agent: these read the runner's state files and drop requests into its spool
(harness.pocket.store). The spool writes are the door's one carve-out beyond acking events — a queued
prompt, a stop flag, a reset flag — and the runner decides what happens to each.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from harness.console.errors import CommandError
from harness.pocket import config as pocket_config
from harness.pocket.store import PocketStore

MAX_QUEUED_PROMPTS = 5
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _pocket_store() -> PocketStore:
    return PocketStore()


def _h_pocket_state(args: dict[str, Any]) -> dict[str, Any]:
    since = args.get("since", 0)
    if not isinstance(since, int) or since < 0:
        raise CommandError("since must be a non-negative integer")
    snap: dict[str, Any] = _pocket_store().snapshot(since=since)
    return snap


def _h_pocket_send(args: dict[str, Any]) -> dict[str, Any]:
    text = args.get("text")
    if not isinstance(text, str):
        raise CommandError("text must be a string")
    text = _CONTROL.sub("", text).strip()
    if not text:
        raise CommandError("empty message")
    cap = pocket_config.load().max_prompt_chars
    if len(text) > cap:
        raise CommandError(f"message is {len(text)} characters; the limit is {cap}")
    store = _pocket_store()
    if len(store.queued()) >= MAX_QUEUED_PROMPTS:
        raise CommandError("the runner already has prompts waiting — wait for a reply")
    item: dict[str, Any] = store.enqueue(text)
    return item


def _h_pocket_stop(_: dict[str, Any]) -> bool:
    _pocket_store().request("stop")
    return True


def _h_pocket_reset(_: dict[str, Any]) -> bool:
    _pocket_store().request("reset")
    return True


HANDLERS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "pocket_state": _h_pocket_state,
    "pocket_send": _h_pocket_send,
    "pocket_stop": _h_pocket_stop,
    "pocket_reset": _h_pocket_reset,
}
