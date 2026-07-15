"""Shared corpus-reading primitives across harness lanes.

Extracted at consolidation time: the markdown reading primitives — frontmatter
loading + heading-anchored (`## `) section extraction — were travel-lane-local but are lane-agnostic.
A lane's `CorpusReader` composes these; nothing here is lane-specific (no slug/folder-note resolution,
which stays in the lane that owns that corpus layout).

Deliberately *functions*, not a forced base class: the markdown readers (travel today) genuinely
share these; the finance reader is YAML-only and has no markdown to share — so a common base would be
premature abstraction — extract at consolidation time, not prematurely.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import frontmatter


def load_doc(path: Path | str) -> tuple[dict[str, Any], str]:
    """Load a markdown doc with YAML frontmatter → (metadata, body). Thin, typed wrapper over
    `frontmatter.load` so callers get a plain dict + str (and one import seam to mock in tests)."""
    post = frontmatter.load(str(path))
    return dict(post.metadata), post.content


def split_sections(body: str) -> dict[str, str]:
    """Split a markdown body into `{H2-heading: text}` (text under each `## ` up to the next one).

    Content before the first `## ` lands under the `_preamble` key. Heading text is the line after
    `## ` (trimmed); section text is stripped.
    """
    sections: dict[str, str] = {}
    current = "_preamble"
    buf: list[str] = []
    for line in body.splitlines():
        if line.startswith("## "):
            sections[current] = "\n".join(buf).strip()
            current = line[3:].strip()
            buf = []
        else:
            buf.append(line)
    sections[current] = "\n".join(buf).strip()
    return sections


def section(sections: dict[str, str], prefix: str) -> str:
    """Return the text of the first section whose heading starts with `prefix` (case-insensitive)."""
    for heading, text in sections.items():
        if heading.lower().startswith(prefix.lower()):
            return text
    return ""
