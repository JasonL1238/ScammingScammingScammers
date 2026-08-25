"""The migration machinery: ordered, checksummed, tracked schema changes.

Split in two on the stdlib boundary: :mod:`ssscammers.db.files` is stdlib-only —
discovery, validation, and checksums of ``db/migrations/`` — written to be
importable by the safety suite with zero third-party packages (the enum-sync test
converges on it in the Phase 1 redesign). :mod:`ssscammers.db.runner` needs
asyncpg (the ``db`` extra) and is only reached via ``python -m ssscammers.db``.
"""
