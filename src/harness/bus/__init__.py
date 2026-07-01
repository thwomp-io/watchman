"""harness.bus — the message bus: durable human-event layer for standing agents.

Producers (launchd-scheduled `hn` commands) publish events; delivery surfaces (the tray app;
later transports like ntfy) consume + mark them. Zero model in the loop. Contract: docs/BUS.md.
"""
