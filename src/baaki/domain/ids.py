"""UUIDv7 identifiers (ARCHITECTURE.md §8 identifier strategy; A-D1: uuid6 dependency).

The application generates every primary key and passes it to writers; no table has a DEFAULT.
Swap to stdlib uuid.uuid7() when the runtime moves to Python 3.14.
"""

from __future__ import annotations

from uuid import UUID

from uuid6 import uuid7


def new_id() -> UUID:
    return uuid7()
