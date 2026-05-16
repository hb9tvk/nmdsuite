"""Database backup + restore (M4.5).

SQLite is one file (`nmdsuite.sqlite3`), so a backup is the file itself
captured as a transaction-consistent snapshot. We use SQLite's
``backup`` API (via the standard ``sqlite3`` module) which works fine
concurrently with Django's WAL-mode writers and produces a clean,
defragmented copy.

Restore replaces the live DB file. Because gunicorn runs multiple
workers, the OTHER workers still hold open file handles to the OLD
file (kept alive by inode reference even after rename). They will not
see the restored data until they reconnect — i.e. until the container
is restarted. The view surfaces a prominent banner saying so; this is
honest rather than pretending we can do a live multi-worker swap.

Audit:
- ``backup.download`` — written to the LIVE DB at download time.
- ``backup.restore`` — written to the OLD DB BEFORE the swap, so the
  history of "this admin initiated a restore" survives on disk in the
  ``.bak`` file even if the restored DB doesn't know about it.
"""
from __future__ import annotations

import logging
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from django.conf import settings
from django.db import connections

from core.audit import audit
from core.models import Contest

log = logging.getLogger("nmdsuite.backup")

# SQLite database files always start with this 16-byte header.
_SQLITE_MAGIC = b"SQLite format 3\x00"


class RestoreError(Exception):
    """User-facing error from the restore flow (bad upload, wrong schema)."""


@dataclass
class RestoreResult:
    bytes_loaded: int
    backup_path: str  # filesystem path where the previous live DB was saved


def _live_db_path() -> Path:
    return Path(settings.DATABASES["default"]["NAME"])


def _close_django_connections() -> None:
    """Close every Django DB connection in this process.

    Pulled out as a seam: tests monkeypatch this to a no-op so they
    don't tear down the pytest-managed connection mid-test.
    """
    for conn in connections.all():
        conn.close()


def create_backup(*, actor: Any) -> bytes:
    """Return a transaction-consistent SQLite snapshot of the live DB.

    Writes an audit row (to the live DB) before producing the bytes so
    every download is traceable. The snapshot is written to a tempfile,
    read into memory, and the tempfile is deleted.
    """
    audit(
        action="backup.download",
        actor=actor,
        target=str(_live_db_path().name),
        contest=Contest.objects.exclude(state=Contest.State.ARCHIVED).order_by("-year").first(),
    )

    live = _live_db_path()
    fd, tmp_name = tempfile.mkstemp(prefix="nmd-backup-", suffix=".sqlite3")
    # sqlite3.Connection.backup() opens the destination itself; we just
    # need a path that doesn't exist (mkstemp creates one — close + unlink).
    import os
    os.close(fd)
    Path(tmp_name).unlink()

    try:
        src = sqlite3.connect(str(live))
        try:
            dst = sqlite3.connect(tmp_name)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        return Path(tmp_name).read_bytes()
    finally:
        Path(tmp_name).unlink(missing_ok=True)


def validate_backup_bytes(file_bytes: bytes) -> None:
    """Raise :class:`RestoreError` if ``file_bytes`` isn't a valid NMDSuite SQLite backup.

    Two checks:
    1. SQLite magic header (16 bytes).
    2. Presence of the ``core_contest`` table — NMDSuite's anchor table.
       A random SQLite DB would pass check 1 but fail this one.
    """
    if not file_bytes.startswith(_SQLITE_MAGIC):
        raise RestoreError("Uploaded file is not a SQLite database.")

    fd, tmp_name = tempfile.mkstemp(prefix="nmd-restore-validate-", suffix=".sqlite3")
    import os
    os.close(fd)
    try:
        Path(tmp_name).write_bytes(file_bytes)
        try:
            conn = sqlite3.connect(tmp_name)
        except sqlite3.Error as exc:
            raise RestoreError(f"Cannot open uploaded file as SQLite: {exc}") from exc
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='core_contest'"
            ).fetchone()
            if row is None:
                raise RestoreError("Not an NMDSuite backup (missing core_contest table).")
        finally:
            conn.close()
    finally:
        Path(tmp_name).unlink(missing_ok=True)


def restore_backup(*, file_bytes: bytes, actor: Any) -> RestoreResult:
    """Atomically swap the live DB file with ``file_bytes``.

    Order matters: validate → audit (to old DB) → close THIS worker's
    connections → swap → delete stale WAL/SHM companions. Other gunicorn
    workers will still see the OLD data until container restart; the
    view surfaces a banner saying so.

    Raises :class:`RestoreError` on validation failure. The live DB is
    untouched on failure.
    """
    validate_backup_bytes(file_bytes)

    audit(
        action="backup.restore",
        actor=actor,
        target=str(_live_db_path().name),
        contest=Contest.objects.exclude(state=Contest.State.ARCHIVED).order_by("-year").first(),
        payload={"bytes": len(file_bytes)},
    )

    live = _live_db_path()
    incoming = live.with_name(live.name + ".restoring")
    bak = live.with_name(live.name + ".bak")

    # Stage the new file next to the live one so the final rename is on
    # the same filesystem (POSIX atomic rename).
    incoming.write_bytes(file_bytes)

    # Release this worker's open SQLite connections before the swap.
    # Other gunicorn workers will keep their stale handles until restart.
    _close_django_connections()

    try:
        if bak.exists():
            bak.unlink()
        live.replace(bak)         # old live → .bak (atomic)
        incoming.replace(live)    # new file → live (atomic)
    except OSError:
        # Best-effort recovery: if the second rename failed, put the old
        # file back so we don't end up with no DB at all.
        if bak.exists() and not live.exists():
            bak.replace(live)
        incoming.unlink(missing_ok=True)
        raise

    # Stale WAL / SHM companions from the OLD DB would confuse SQLite when
    # it opens the new live file; remove them. SQLite recreates as needed.
    for suffix in ("-wal", "-shm"):
        companion = live.with_name(live.name + suffix)
        companion.unlink(missing_ok=True)

    return RestoreResult(bytes_loaded=len(file_bytes), backup_path=str(bak))
