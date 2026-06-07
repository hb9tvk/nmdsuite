"""Filesystem operations for the participant report's picture uploads (F3.1).

Pictures land under the bind-mounted ``/data`` volume (next to the
SQLite file) at::

    /data/<year>/<callsign>/<callsign>_<idx>.<ext>

Originals are stored at their uploaded resolution. The portal renders
them at small sizes via CSS — there's no thumbnail cache.

The model (:class:`core.models.ParticipantPicture`) is the source of
truth for "what's uploaded"; this module turns that into bytes on disk.
"""
from __future__ import annotations

import io
import re
import tarfile
from pathlib import Path

from django.conf import settings
from django.core.files.uploadedfile import UploadedFile

from core.models import (
    PICTURE_MAX_SLOTS,
    Participant,
    ParticipantPicture,
    ParticipantReport,
)


MAX_PICTURE_BYTES = 5 * 1024 * 1024  # 5 MB per image

_CONTENT_TYPE_EXT = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}

ALLOWED_CONTENT_TYPES = frozenset(_CONTENT_TYPE_EXT)


class PictureUploadError(Exception):
    """Surfaceable upload error (size, type, no free slot, …)."""


# --- paths -----------------------------------------------------------------------------------


def _data_root() -> Path:
    """Bind-mounted volume root (``/data`` in the container). Pinned by
    ``NMD_DATA_ROOT`` in settings; defaults next to the SQLite file so
    one /data volume carries both in production."""
    return Path(settings.NMD_DATA_ROOT)


_SAFE_CALLSIGN = re.compile(r"[^A-Z0-9_-]")


def _safe_callsign(callsign: str) -> str:
    """Strip / and other path-y characters from a callsign for use as a
    directory or filename segment. Country prefixes like ``OE/HB9TVK``
    become ``OE_HB9TVK`` on disk; the model field is unchanged."""
    return _SAFE_CALLSIGN.sub("_", (callsign or "").upper())


def _participant_dir(participant: Participant) -> Path:
    return (
        _data_root()
        / str(participant.contest.year)
        / _safe_callsign(participant.callsign)
    )


def picture_path(picture: ParticipantPicture) -> Path:
    """Absolute filesystem path for ``picture``'s on-disk file."""
    name = (
        f"{_safe_callsign(picture.participant.callsign)}"
        f"_{picture.idx}.{picture.extension}"
    )
    return _participant_dir(picture.participant) / name


# --- queries ---------------------------------------------------------------------------------


def get_or_create_report(participant: Participant) -> ParticipantReport:
    report, _ = ParticipantReport.objects.get_or_create(participant=participant)
    return report


def picture_slots(participant: Participant) -> list[tuple[int, ParticipantPicture | None]]:
    """Return ``[(idx, picture-or-None)]`` for slots 1..PICTURE_MAX_SLOTS,
    in order. Lets the template render filled + empty cells uniformly."""
    pictures = {p.idx: p for p in participant.pictures.all()}
    return [(i, pictures.get(i)) for i in range(1, PICTURE_MAX_SLOTS + 1)]


def next_empty_slot(participant: Participant) -> int | None:
    used = set(participant.pictures.values_list("idx", flat=True))
    for i in range(1, PICTURE_MAX_SLOTS + 1):
        if i not in used:
            return i
    return None


# --- mutations -------------------------------------------------------------------------------


def save_captions(
    participant: Participant, captions: dict[int, str],
) -> None:
    """Update the ``caption`` field on each picture identified by idx.

    ``captions`` is a ``{idx: caption}`` mapping; unknown idxs are
    silently skipped (the user can only post fields for slots they're
    seeing). Captions are truncated at 50 chars as a defensive backstop;
    the form's ``maxlength`` is the primary guard.
    """
    for idx, caption in captions.items():
        participant.pictures.filter(idx=idx).update(
            caption=(caption or "")[:50],
        )


def save_text(participant: Participant, text: str) -> ParticipantReport:
    """Persist the report text. Truncates at the model's max_length to
    keep this resilient even if the form lets slightly-long input
    through; the form is the primary guard."""
    report = get_or_create_report(participant)
    report.text = (text or "")[:4096]
    report.save(update_fields=["text", "updated_at"])
    return report


def add_picture(participant: Participant, upload: UploadedFile) -> ParticipantPicture:
    """Validate + persist an uploaded image into the next free slot.

    Raises :class:`PictureUploadError` for any rejected input — the
    portal view turns those into a flash message.
    """
    if upload.size > MAX_PICTURE_BYTES:
        raise PictureUploadError("Image larger than 5 MB; please resize it.")
    content_type = (upload.content_type or "").lower()
    extension = _CONTENT_TYPE_EXT.get(content_type)
    if extension is None:
        raise PictureUploadError("Only JPEG, PNG and WebP images are accepted.")
    slot = next_empty_slot(participant)
    if slot is None:
        raise PictureUploadError(
            f"All {PICTURE_MAX_SLOTS} picture slots are full; delete one first."
        )

    target_dir = _participant_dir(participant)
    target_dir.mkdir(parents=True, exist_ok=True)

    picture = ParticipantPicture.objects.create(
        participant=participant,
        idx=slot,
        extension=extension,
        original_filename=upload.name[:255],
        content_type=content_type,
        file_size=upload.size,
    )
    with picture_path(picture).open("wb") as out:
        for chunk in upload.chunks():
            out.write(chunk)
    return picture


def delete_picture(participant: Participant, idx: int) -> bool:
    """Remove the on-disk file and the DB row for the slot at ``idx``.
    Returns True if a picture was deleted, False if the slot was empty."""
    try:
        picture = participant.pictures.get(idx=idx)
    except ParticipantPicture.DoesNotExist:
        return False
    path = picture_path(picture)
    if path.is_file():
        path.unlink()
    picture.delete()
    return True


def delete_everything(participant: Participant) -> None:
    """Used by the cancel flow: drop the report row, all picture rows,
    every on-disk file, and the participant's directory if it's now
    empty. Safe to call when nothing was ever uploaded."""
    ParticipantReport.objects.filter(participant=participant).delete()
    pictures = list(participant.pictures.all())
    for picture in pictures:
        path = picture_path(picture)
        if path.is_file():
            path.unlink()
    ParticipantPicture.objects.filter(participant=participant).delete()
    pdir = _participant_dir(participant)
    if pdir.is_dir():
        try:
            pdir.rmdir()  # only if empty
        except OSError:
            # Leftover thumbnails or unrelated files — leave them; the
            # year directory is shared. Don't recursively delete arbitrary
            # paths under /data on a cancel action.
            pass


# --- backup ----------------------------------------------------------------------------------


def build_pictures_tarball() -> bytes:
    """Return a ``.tar.gz`` of every year-named directory under
    :func:`_data_root`. The tarball mirrors the on-disk layout
    (``<year>/<callsign>/<callsign>_<idx>.<ext>``) so it can be
    unpacked directly back into ``/data`` to restore.

    Only top-level directories whose name is all digits (a contest
    year) are included — keeps stray files (``nmdsuite.sqlite3``,
    backup ``.bak`` files, etc.) out of the archive.

    Built in memory because the picture set is small enough in practice
    (~50 participants × 6 photos × a few MB). Switch to a streaming
    generator if that stops being true.
    """
    buf = io.BytesIO()
    root = _data_root()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        if root.is_dir():
            for entry in sorted(root.iterdir()):
                if entry.is_dir() and entry.name.isdigit():
                    tar.add(str(entry), arcname=entry.name)
    return buf.getvalue()
