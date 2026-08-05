"""
AI Voice Clone Studio — Database layer.

Owned by B2 in Wave 2. `schema.sql` is frozen in Wave 0.

Policy, decided once so it is not relitigated per-module:

  * ONE long-lived aiosqlite connection for the process.
  * PRAGMAs applied once at open, not per call.
  * An `asyncio.Lock` around writes. SQLite serializes writes regardless, so a
    connection pool buys nothing here and costs deadlocks.
  * Handed to routes via `Depends(get_db)`, so tests can substitute an
    in-memory database without patching module globals.

This replaces a per-call `aiosqlite.connect` that re-ran PRAGMAs every time and
leaked a connection on every translation (translation_service.py:164-178).
"""

from .database import Database

__all__ = ["Database"]
