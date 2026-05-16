"""Administration module — M4.5 backup / restore.

The backup_service functions touch the filesystem (the live SQLite DB
file). To keep tests from trashing the pytest-django test DB, we
monkeypatch ``backup_service._live_db_path`` to point at a temp file
the test owns. The Django ``audit()`` writes still land in the test DB
as usual.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile

from admin_module import backup_service
from core.models import AuditLog


@pytest.fixture(autouse=True)
def _skip_django_connection_close(monkeypatch):
    """The real restore_backup() closes every Django DB connection so the
    next ORM call reopens against the swapped file. In tests that would
    nuke the pytest-managed transaction; stub it out for every test in
    this file."""
    monkeypatch.setattr(backup_service, "_close_django_connections", lambda: None)

User = get_user_model()


def _make_staff_user(username: str = "STAFF") -> User:
    return User.objects.create_user(
        username=username, password="x", email=f"{username.lower()}@x.org", is_staff=True,
    )


def _make_fake_live_db(path: Path) -> None:
    """Write a minimal NMDSuite-shaped SQLite file at ``path``.

    Has the magic header + a ``core_contest`` table, which is what
    :func:`backup_service.validate_backup_bytes` checks for.
    """
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE core_contest (year INTEGER PRIMARY KEY, label TEXT)")
        conn.execute("INSERT INTO core_contest (year, label) VALUES (2025, 'NMD 2025')")
        conn.commit()
    finally:
        conn.close()


# --- validate_backup_bytes -------------------------------------------------------------------


def test_validate_rejects_non_sqlite_file():
    with pytest.raises(backup_service.RestoreError, match="not a SQLite"):
        backup_service.validate_backup_bytes(b"not a sqlite database at all")


def test_validate_rejects_sqlite_without_core_contest_table(tmp_path):
    bogus = tmp_path / "bogus.sqlite3"
    conn = sqlite3.connect(str(bogus))
    try:
        conn.execute("CREATE TABLE unrelated (id INTEGER)")
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(backup_service.RestoreError, match="core_contest"):
        backup_service.validate_backup_bytes(bogus.read_bytes())


def test_validate_accepts_valid_nmdsuite_backup(tmp_path):
    good = tmp_path / "good.sqlite3"
    _make_fake_live_db(good)
    # No exception = accepted.
    backup_service.validate_backup_bytes(good.read_bytes())


# --- create_backup ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_create_backup_produces_valid_sqlite_bytes(monkeypatch, tmp_path):
    fake_live = tmp_path / "live.sqlite3"
    _make_fake_live_db(fake_live)
    monkeypatch.setattr(backup_service, "_live_db_path", lambda: fake_live)

    staff = _make_staff_user()
    blob = backup_service.create_backup(actor=staff)

    assert blob.startswith(b"SQLite format 3\x00")
    # Round-trip: the bytes parse as SQLite and carry the row we inserted.
    rt_path = tmp_path / "rt.sqlite3"
    rt_path.write_bytes(blob)
    conn = sqlite3.connect(str(rt_path))
    try:
        row = conn.execute("SELECT year, label FROM core_contest").fetchone()
        assert row == (2025, "NMD 2025")
    finally:
        conn.close()


@pytest.mark.django_db
def test_create_backup_writes_audit_row(monkeypatch, tmp_path):
    fake_live = tmp_path / "live.sqlite3"
    _make_fake_live_db(fake_live)
    monkeypatch.setattr(backup_service, "_live_db_path", lambda: fake_live)

    staff = _make_staff_user()
    backup_service.create_backup(actor=staff)

    entry = AuditLog.objects.get(action="backup.download")
    assert entry.actor == staff


# --- restore_backup --------------------------------------------------------------------------


@pytest.mark.django_db
def test_restore_swaps_live_file_and_keeps_bak(monkeypatch, tmp_path):
    # Set up a "live" DB and an incoming backup with different contents.
    fake_live = tmp_path / "live.sqlite3"
    _make_fake_live_db(fake_live)
    original_bytes = fake_live.read_bytes()

    incoming = tmp_path / "incoming.sqlite3"
    conn = sqlite3.connect(str(incoming))
    try:
        conn.execute("CREATE TABLE core_contest (year INTEGER PRIMARY KEY, label TEXT)")
        conn.execute("INSERT INTO core_contest (year, label) VALUES (9999, 'RESTORED')")
        conn.commit()
    finally:
        conn.close()
    incoming_bytes = incoming.read_bytes()

    monkeypatch.setattr(backup_service, "_live_db_path", lambda: fake_live)

    staff = _make_staff_user()
    result = backup_service.restore_backup(file_bytes=incoming_bytes, actor=staff)

    # Live now holds the incoming data.
    assert fake_live.read_bytes() == incoming_bytes
    conn = sqlite3.connect(str(fake_live))
    try:
        row = conn.execute("SELECT year, label FROM core_contest").fetchone()
        assert row == (9999, "RESTORED")
    finally:
        conn.close()

    # .bak holds the previous live.
    bak = Path(result.backup_path)
    assert bak.exists()
    assert bak.read_bytes() == original_bytes


@pytest.mark.django_db
def test_restore_audits_before_swap(monkeypatch, tmp_path):
    fake_live = tmp_path / "live.sqlite3"
    _make_fake_live_db(fake_live)
    incoming = tmp_path / "incoming.sqlite3"
    _make_fake_live_db(incoming)

    monkeypatch.setattr(backup_service, "_live_db_path", lambda: fake_live)

    staff = _make_staff_user()
    backup_service.restore_backup(file_bytes=incoming.read_bytes(), actor=staff)

    entry = AuditLog.objects.get(action="backup.restore")
    assert entry.actor == staff
    assert entry.payload["bytes"] == len(incoming.read_bytes())


@pytest.mark.django_db
def test_restore_rejects_invalid_bytes_and_leaves_live_alone(monkeypatch, tmp_path):
    fake_live = tmp_path / "live.sqlite3"
    _make_fake_live_db(fake_live)
    original = fake_live.read_bytes()
    monkeypatch.setattr(backup_service, "_live_db_path", lambda: fake_live)

    staff = _make_staff_user()
    with pytest.raises(backup_service.RestoreError):
        backup_service.restore_backup(file_bytes=b"junk", actor=staff)

    # Live untouched, no .bak created.
    assert fake_live.read_bytes() == original
    assert not fake_live.with_name(fake_live.name + ".bak").exists()
    # No audit row for an aborted restore.
    assert not AuditLog.objects.filter(action="backup.restore").exists()


@pytest.mark.django_db
def test_restore_removes_stale_wal_and_shm(monkeypatch, tmp_path):
    """The OLD DB's WAL/SHM files would confuse SQLite when it opens the
    restored file; the service should clean them up post-swap."""
    fake_live = tmp_path / "live.sqlite3"
    _make_fake_live_db(fake_live)
    wal = fake_live.with_name(fake_live.name + "-wal")
    shm = fake_live.with_name(fake_live.name + "-shm")
    wal.write_bytes(b"stale wal")
    shm.write_bytes(b"stale shm")

    incoming = tmp_path / "incoming.sqlite3"
    _make_fake_live_db(incoming)

    monkeypatch.setattr(backup_service, "_live_db_path", lambda: fake_live)

    staff = _make_staff_user()
    backup_service.restore_backup(file_bytes=incoming.read_bytes(), actor=staff)

    assert not wal.exists()
    assert not shm.exists()


# --- view: access control --------------------------------------------------------------------


@pytest.mark.django_db
def test_backup_index_redirects_non_staff(client, seeded_contest):
    user = User.objects.create_user(username="JOE", password="x", email="j@x.org")
    client.force_login(user)
    response = client.get("/admin/backup/")
    assert response.status_code in (302, 403)


@pytest.mark.django_db
def test_backup_download_redirects_non_staff(client, seeded_contest):
    user = User.objects.create_user(username="JOE", password="x", email="j@x.org")
    client.force_login(user)
    response = client.post("/admin/backup/download/")
    assert response.status_code in (302, 403)


@pytest.mark.django_db
def test_backup_restore_redirects_non_staff(client, seeded_contest):
    user = User.objects.create_user(username="JOE", password="x", email="j@x.org")
    client.force_login(user)
    response = client.post("/admin/backup/restore/")
    assert response.status_code in (302, 403)


# --- view: index -----------------------------------------------------------------------------


@pytest.mark.django_db
def test_backup_index_renders(client, seeded_contest):
    client.force_login(_make_staff_user())
    response = client.get("/admin/backup/")
    body = response.content.decode()
    assert response.status_code == 200
    assert "Download backup" in body
    assert "Restore from file" in body


@pytest.mark.django_db
def test_backup_index_with_restored_flag_shows_restart_banner(client, seeded_contest):
    client.force_login(_make_staff_user())
    response = client.get("/admin/backup/?restored=1")
    body = response.content.decode()
    assert "docker compose restart" in body or "Restart the container" in body


# --- view: restore upload --------------------------------------------------------------------


@pytest.mark.django_db
def test_restore_view_missing_file_flashes_error(client, seeded_contest):
    client.force_login(_make_staff_user())
    response = client.post("/admin/backup/restore/")
    assert response.status_code == 302
    assert response["Location"].endswith("/admin/backup/")


@pytest.mark.django_db
def test_restore_view_invalid_file_flashes_error(client, seeded_contest, monkeypatch, tmp_path):
    """Reject a non-SQLite upload at the view boundary."""
    fake_live = tmp_path / "live.sqlite3"
    _make_fake_live_db(fake_live)
    monkeypatch.setattr(backup_service, "_live_db_path", lambda: fake_live)

    client.force_login(_make_staff_user())
    response = client.post(
        "/admin/backup/restore/",
        {"file": SimpleUploadedFile("bad.sqlite3", b"not sqlite", content_type="application/octet-stream")},
    )
    assert response.status_code == 302
    # Live untouched (still a valid SQLite header, .bak never created).
    assert fake_live.read_bytes().startswith(b"SQLite format 3\x00")
    assert not fake_live.with_name(fake_live.name + ".bak").exists()
