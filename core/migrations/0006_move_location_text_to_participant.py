"""Move ``location_text`` from StationDescription to Participant.

Location-name is conceptually registration data (paired with the
coordinates that already live on Participant). Moving the column
removes the awkward shape where one part of the location was on
``Participant`` and the named part was on ``StationDescription`` —
which forced every consumer that wanted both to JOIN the two.

The data migration copies any existing values; rows whose
StationDescription was empty stay blank, which is fine because
``Participant.location_text`` is ``blank=True`` at the model level
(forms enforce presence going forward).
"""
from django.db import migrations, models


def _copy_location_to_participant(apps, schema_editor):
    StationDescription = apps.get_model("core", "StationDescription")
    for station in StationDescription.objects.select_related("participant").all():
        text = (station.location_text or "").strip()
        if not text:
            continue
        participant = station.participant
        if participant.location_text:
            continue  # don't overwrite if already set somehow
        participant.location_text = text
        participant.save(update_fields=["location_text"])


def _copy_location_back_to_station(apps, schema_editor):
    """Reverse: copy Participant.location_text back onto its StationDescription
    so the rollback doesn't silently lose the data."""
    Participant = apps.get_model("core", "Participant")
    StationDescription = apps.get_model("core", "StationDescription")
    for p in Participant.objects.all():
        text = (p.location_text or "").strip()
        if not text:
            continue
        station, _ = StationDescription.objects.get_or_create(participant=p)
        station.location_text = text
        station.save(update_fields=["location_text"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0005_participant_auto_submitted"),
    ]

    operations = [
        migrations.AddField(
            model_name="participant",
            name="location_text",
            field=models.CharField(
                blank=True, max_length=120,
                help_text=(
                    "Friendly location name (SOTA ref, summit name, …) — "
                    "the named counterpart of the coordinates"
                ),
            ),
        ),
        migrations.RunPython(
            _copy_location_to_participant,
            reverse_code=_copy_location_back_to_station,
        ),
        migrations.RemoveField(
            model_name="stationdescription",
            name="location_text",
        ),
    ]
