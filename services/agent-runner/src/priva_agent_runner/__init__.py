"""Priva Cloud agent-runner.

The agent runtime + execution faces, runnable as a standalone single-account
process behind a signed ``X-Priva-Runner-Token`` header (code-split §13 dev
mode A). See ``app.py`` for the FastAPI app and ``entry.py`` for the launcher
entry-point.
"""
