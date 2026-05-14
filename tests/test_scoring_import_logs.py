"""import_logs management command — validation-fixture loader."""
from __future__ import annotations

from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command

from core.models import Participant, QsoEntry

User = get_user_model()


# A tiny but realistic .nmd file with one station-info comment line + 2 QSOs.
_NMD_HB9TVK = b"""#;OPNAME=;Peter
0612;HB9ABC;599;HB9TVK PIZ KESCH 3418M;599;HB9ABC ALPSTEIN 2502M
0700;DL1XYZ;59;;59;
"""

_NMD_HB9ABC = b"""0612;HB9TVK;599;HB9ABC ALPSTEIN 2502M;599;HB9TVK PIZ KESCH 3418M
"""


def _write_dir(tmp_path, files: dict[str, bytes]):
    for name, content in files.items():
        (tmp_path / name).write_bytes(content)
    return tmp_path


@pytest.mark.django_db
def test_import_logs_creates_users_and_participants(seeded_contest, tmp_path):
    _write_dir(tmp_path, {"HB9TVK.nmd": _NMD_HB9TVK, "HB9ABC.nmd": _NMD_HB9ABC})
    out = StringIO()
    call_command("import_logs", "--year", str(seeded_contest.year), str(tmp_path), stdout=out)

    # Two users, two participants, three QSOs total (2 + 1).
    assert User.objects.filter(username__in=["HB9TVK", "HB9ABC"]).count() == 2
    a = Participant.objects.get(callsign="HB9TVK/P")
    b = Participant.objects.get(callsign="HB9ABC/P")
    assert QsoEntry.objects.filter(participant=a).count() == 2
    assert QsoEntry.objects.filter(participant=b).count() == 1

    output = out.getvalue()
    assert "Imported 2 participant(s)" in output
    assert "3 QSOs" in output


@pytest.mark.django_db
def test_import_logs_is_idempotent(seeded_contest, tmp_path):
    _write_dir(tmp_path, {"HB9TVK.nmd": _NMD_HB9TVK})
    call_command("import_logs", "--year", str(seeded_contest.year), str(tmp_path), stdout=StringIO())
    call_command("import_logs", "--year", str(seeded_contest.year), str(tmp_path), stdout=StringIO())

    # Still exactly one User, one Participant, two QSOs after the second run.
    assert User.objects.filter(username="HB9TVK").count() == 1
    a = Participant.objects.get(callsign="HB9TVK/P")
    assert QsoEntry.objects.filter(participant=a).count() == 2


@pytest.mark.django_db
def test_import_logs_strips_portable_suffix_from_username(seeded_contest, tmp_path):
    """User.username gets the bare callsign; Participant.callsign gets /P appended."""
    _write_dir(tmp_path, {"HB9TVK.nmd": _NMD_HB9TVK})
    call_command("import_logs", "--year", str(seeded_contest.year), str(tmp_path), stdout=StringIO())
    user = User.objects.get(username="HB9TVK")
    participant = Participant.objects.get(user=user)
    assert participant.callsign == "HB9TVK/P"


@pytest.mark.django_db
def test_import_logs_no_portable_suffix_flag(seeded_contest, tmp_path):
    _write_dir(tmp_path, {"HB9TVK.nmd": _NMD_HB9TVK})
    call_command(
        "import_logs", "--year", str(seeded_contest.year), str(tmp_path),
        "--no-portable-suffix", stdout=StringIO(),
    )
    assert Participant.objects.get(user__username="HB9TVK").callsign == "HB9TVK"


@pytest.mark.django_db
def test_import_logs_unknown_year_errors(seeded_contest, tmp_path):
    with pytest.raises(CommandError, match="No contest with year=9999"):
        call_command("import_logs", "--year", "9999", str(tmp_path))


@pytest.mark.django_db
def test_import_logs_missing_directory_errors(seeded_contest):
    with pytest.raises(CommandError, match="Not a directory"):
        call_command("import_logs", "--year", str(seeded_contest.year), "/no/such/path")


@pytest.mark.django_db
def test_import_logs_empty_directory_errors(seeded_contest, tmp_path):
    with pytest.raises(CommandError, match="No \\*.nmd files"):
        call_command("import_logs", "--year", str(seeded_contest.year), str(tmp_path))


@pytest.mark.django_db
def test_import_logs_pipes_into_run_scoring(seeded_contest, tmp_path):
    """End-to-end: import + score in one go, like the validation workflow."""
    _write_dir(tmp_path, {"HB9TVK.nmd": _NMD_HB9TVK, "HB9ABC.nmd": _NMD_HB9ABC})
    call_command("import_logs", "--year", str(seeded_contest.year), str(tmp_path), stdout=StringIO())

    out = StringIO()
    call_command("run_scoring", "--year", str(seeded_contest.year), stdout=out)
    output = out.getvalue()
    # Both participants logged each other → full-match pair (2 records). HB9TVK
    # also has a DX QSO. So 3 scored rows total.
    assert "3 QSOs scored" in output
    assert "full_match" in output
