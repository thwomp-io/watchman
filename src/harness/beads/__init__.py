"""The beads lane — a read-only board over the tracker's `.beads/issues.jsonl` passive export.

The coordination bus (bd/Dolt) stays the write surface; this lane exists so the CONSOLE can render
the backlog (the BACKLOG tab) without shelling to `bd` — the export is already a
plain-file contract sitting in the corpus. Zero writes, zero bd dependency, calm-empty when the
file is absent (a demo pack / fresh clone has no beads db).
"""
