"""Shared text helpers — HTML → readable plain text via the stdlib parser.

Lifted from `harness.finance.filing` (which keeps a re-export) when the career lane needed the
same converter for job descriptions and application-form question text. API-over-library rule:
no bs4/lxml/html2text dependency for what `html.parser` handles — the HTML these lanes meet
(press-release exhibits, ATS job content) is presentation-heavy but structurally shallow.
"""

from __future__ import annotations

from html.parser import HTMLParser


class _TextExtractor(HTMLParser):
    """HTML → readable plain text.

    Block boundaries become newlines (paragraph flow survives), table cells join with ` | ` inside
    their row (KPI/guidance tables stay scannable as rows), script/style/head content is dropped.
    Everything else passes through as text."""

    # ix:header = the hidden inline-XBRL block every 10-K/10-Q carries up top (contexts, units,
    # hidden facts) — a wall of tokens before the first real sentence.
    _SKIP = {"script", "style", "head", "title", "ix:header"}
    _BLOCK = {"p", "div", "br", "tr", "table", "h1", "h2", "h3", "h4", "h5", "h6", "li", "hr"}
    _CELL = {"td", "th"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip_depth = 0
        self._in_row = False
        self._row_cells: list[str] = []
        self._cell_chunks: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP:
            self._skip_depth += 1
            return
        if tag == "tr":
            self._in_row = True
            self._row_cells = []
        elif tag in self._CELL and self._in_row:
            self._cell_chunks = []
        elif tag in self._BLOCK and not self._in_row:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag in self._CELL and self._cell_chunks is not None:
            cell = " ".join("".join(self._cell_chunks).split())
            self._row_cells.append(cell)
            self._cell_chunks = None
        elif tag == "tr":
            # Emit the row as ` | `-joined cells; skip rows that are pure whitespace/layout.
            row = " | ".join(self._row_cells).strip(" |")
            if any(c for c in self._row_cells):
                self._chunks.append("\n" + row)
            self._in_row = False
            self._row_cells = []
        elif tag in self._BLOCK and not self._in_row:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._cell_chunks is not None:
            self._cell_chunks.append(data)
        elif not self._in_row:
            self._chunks.append(data)

    def text(self) -> str:
        raw = "".join(self._chunks)
        # Collapse intra-line whitespace runs (presentational HTML is &nbsp;-ridden), then
        # collapse blank-line runs to one — readable paragraphs, no vertical sprawl.
        lines = [" ".join(line.split()) for line in raw.splitlines()]
        out: list[str] = []
        for line in lines:
            if line:
                out.append(line)
            elif out and out[-1]:
                out.append("")
        return "\n".join(out).strip()


def html_to_text(html: str) -> str:
    """Convert HTML to readable plain text (tables as ` | `-joined rows)."""
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    return parser.text()
