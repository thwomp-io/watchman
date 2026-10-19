"""The node-subproject resolver: env → this checkout → the worktree's main
checkout, marker-checked, never auto-installed; the viz renderer refuses by naming every path."""

from __future__ import annotations

from pathlib import Path

import pytest

from harness import paths, viz


def _worktree(tmp_path: Path) -> tuple[Path, Path]:
    main = tmp_path / "harness"
    (main / ".git" / "worktrees" / "personal").mkdir(parents=True)
    wt = tmp_path / "harness-personal"
    wt.mkdir()
    (wt / ".git").write_text(f"gitdir: {main / '.git' / 'worktrees' / 'personal'}\n")
    return main, wt


def _install(tree: Path) -> None:
    (tree / "node_modules" / "jsdom").mkdir(parents=True)
    (tree / "node_modules" / "jsdom" / "package.json").write_text("{}")
    (tree / "render.js").write_text("// stub")


def test_resolver_follows_a_worktree_to_its_main_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    main, wt = _worktree(tmp_path)
    monkeypatch.delenv("HARNESS_VIZ_DIR", raising=False)
    assert paths.git_main_checkout(wt) == main and paths.git_main_checkout(main) is None
    assert paths.node_tree_candidates("viz", "HARNESS_VIZ_DIR", repo_root=wt) == [wt / "viz", main / "viz"]
    markers = ("render.js", "node_modules/jsdom/package.json")
    tree, looked = paths.resolve_node_tree("viz", "HARNESS_VIZ_DIR", markers, repo_root=wt)
    assert tree is None and looked == [wt / "viz", main / "viz"]
    # the script alone is not an install — the worktree has render.js and no deps (the live bug)
    (wt / "viz").mkdir()
    (wt / "viz" / "render.js").write_text("// stub")
    assert paths.resolve_node_tree("viz", "HARNESS_VIZ_DIR", markers, repo_root=wt)[0] is None
    _install(main / "viz")
    assert paths.resolve_node_tree("viz", "HARNESS_VIZ_DIR", markers, repo_root=wt)[0] == main / "viz"
    # an explicit env wins, even over an installed checkout
    _install(tmp_path / "elsewhere")
    monkeypatch.setenv("HARNESS_VIZ_DIR", str(tmp_path / "elsewhere"))
    assert (
        paths.resolve_node_tree("viz", "HARNESS_VIZ_DIR", markers, repo_root=wt)[0] == tmp_path / "elsewhere"
    )


def test_viz_refuses_by_naming_every_path_when_nothing_is_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    main, wt = _worktree(tmp_path)
    monkeypatch.setattr(paths, "REPO_ROOT", wt)
    monkeypatch.delenv("HARNESS_VIZ_DIR", raising=False)
    with pytest.raises(viz.VizError) as e:
        viz.viz_render_js()
    msg = str(e.value)
    assert str(wt / "viz") in msg and str(main / "viz") in msg
    assert "npm install" in msg and "HARNESS_VIZ_DIR" in msg
    assert "never automatic" in msg
    _install(main / "viz")
    assert viz.viz_render_js() == main / "viz" / "render.js"
