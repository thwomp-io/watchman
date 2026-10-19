"""The private extension seam: `harness.private.register(app)` mounts bespoke lanes when the module
exists (the personal branch), and its absence is a silent no-op (every public install)."""

from __future__ import annotations

import sys
import types

import typer

from harness import cli


def test_absent_module_mounts_nothing(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # Simulate "no private package" by making the import fail — branch-agnostic (on the personal
    # branch the real harness.private exists, so deleting the sys.modules entry is not enough).
    import importlib

    real = importlib.import_module

    def fake(name: str, package: str | None = None):  # type: ignore[no-untyped-def]
        if name == "harness.private":
            raise ImportError(name)
        return real(name, package)

    monkeypatch.setattr(importlib, "import_module", fake)
    root = typer.Typer()
    assert cli.mount_private_lanes(root) == []
    assert root.registered_groups == []


def test_present_module_mounts_its_lanes_and_records_them(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    lane = typer.Typer(help="a bespoke lane")

    @lane.command()
    def hello() -> None:
        pass

    def register(root: typer.Typer) -> list[str]:
        root.add_typer(lane, name="bespoke")
        return ["bespoke"]

    fake = types.ModuleType("harness.private")
    fake.register = register  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "harness.private", fake)
    monkeypatch.setattr(cli, "PRIVATE_LANES", [])
    root = typer.Typer()
    assert cli.mount_private_lanes(root) == ["bespoke"]
    assert [g.name for g in root.registered_groups] == ["bespoke"]
    assert cli.PRIVATE_LANES == ["bespoke"]


def test_module_without_register_is_ignored(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setitem(sys.modules, "harness.private", types.ModuleType("harness.private"))
    monkeypatch.setattr(cli, "PRIVATE_LANES", [])
    assert cli.mount_private_lanes(typer.Typer()) == []
