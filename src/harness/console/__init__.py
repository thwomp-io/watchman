"""Web-console HTTP API — the browser form-factor's backend half.

The RPC door (`/api/invoke/{cmd}`) that mirrors the Tauri shell's `commands.rs` surface in Python,
mounted on the shipped bus server (`hn bus serve --console`). See `api.py` for the contract and
the read-only gating rationale.
"""
