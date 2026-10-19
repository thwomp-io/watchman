"""The pocket agent — a phone-reachable, read-only chat with an agent lane on the always-on node.

A runner process drives the operator's installed Claude Code CLI headless (`claude -p`, the
operator's own subscription login) inside a sealed permission set, and the served console's Chat
tab talks to it through a spool directory. Spec + threat model: docs/POCKET.md.
"""
