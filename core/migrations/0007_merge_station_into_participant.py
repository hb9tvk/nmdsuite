"""Merge StationDescription into Participant.

The station data form and the registration form edit two halves of one
conceptual entity (the operator's contest entry). Splitting them across
two tables forced every consumer that wanted both halves to JOIN, and
forced operators through two different forms for what's logically one
record. This migration collapses them.

Steps:
  1. Add ``op_name`` / ``watt`` / ``total_weight_g`` to Participant.
  2. Add a nullable ``participant`` FK to StationComponent (the new home).
  3. Data migration: copy StationDescription's three fields onto the
     paired Participant, and point each StationComponent at the same
     Participant via the (now-resolved) station→participant chain.
  4. Drop the old ``station`` FK from StationComponent and make
     ``participant`` non-nullable.
  5. Drop StationDescription.

The data migration is reversible: the back-direction recreates a
StationDescription per Participant that has any station data, copies
the three fields back, and re-points the components.
"""
from django.db import migrations, models


def _copy_station_into_participant(apps, schema_editor):
    Participant = apps.get_model("core", "Participant")
    StationDescription = apps.get_model("core", "StationDescription")
    StationComponent = apps.get_model("core", "StationComponent")

    for station in StationDescription.objects.select_related("participant").all():
        p = station.participant
        # Only copy if the participant side is still default — guards
        # against a re-run accidentally overwriting newer edits.
        if not p.op_name:
            p.op_name = station.op_name or ""
        if not p.watt:
            p.watt = station.watt or ""
        if not p.total_weight_g:
            p.total_weight_g = station.total_weight_g or 0
        p.save(update_fields=["op_name", "watt", "total_weight_g"])

    # Point every existing StationComponent at the participant directly.
    for comp in StationComponent.objects.select_related("station__participant").all():
        comp.participant = comp.station.participant
        comp.save(update_fields=["participant"])


def _split_station_back_out(apps, schema_editor):
    """Reverse: recreate StationDescription rows so a rollback doesn't
    silently drop the equipment data. Used only when downgrading."""
    Participant = apps.get_model("core", "Participant")
    StationDescription = apps.get_model("core", "StationDescription")
    StationComponent = apps.get_model("core", "StationComponent")

    for p in Participant.objects.all():
        if not (p.op_name or p.watt or p.total_weight_g
                or p.components.exists()):
            continue
        station, _ = StationDescription.objects.get_or_create(participant=p)
        station.op_name = p.op_name or ""
        station.watt = p.watt or ""
        station.total_weight_g = p.total_weight_g or 0
        station.save()
        for comp in p.components.all():
            comp.station = station
            comp.save(update_fields=["station"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0006_move_location_text_to_participant"),
    ]

    operations = [
        # 1. New columns on Participant.
        migrations.AddField(
            model_name="participant",
            name="op_name",
            field=models.CharField(blank=True, max_length=80),
        ),
        migrations.AddField(
            model_name="participant",
            name="watt",
            field=models.CharField(blank=True, max_length=20),
        ),
        migrations.AddField(
            model_name="participant",
            name="total_weight_g",
            field=models.PositiveIntegerField(
                default=0,
                help_text=(
                    "Total station weight (grams) — used as ranking tiebreaker"
                ),
            ),
        ),
        # 2. New (nullable) FK on StationComponent. Both FKs co-exist
        # briefly so the data migration can read the old one and write
        # the new one in the same row.
        migrations.AddField(
            model_name="stationcomponent",
            name="participant",
            field=models.ForeignKey(
                null=True,
                on_delete=models.deletion.CASCADE,
                related_name="components_new",
                to="core.participant",
            ),
        ),
        # 3. Copy data.
        migrations.RunPython(
            _copy_station_into_participant,
            reverse_code=_split_station_back_out,
        ),
        # 4. Drop the old uniqueness on (station, idx) before dropping
        # the station FK — SQLite has trouble re-creating tables that
        # still carry stale unique_together references.
        migrations.AlterUniqueTogether(
            name="stationcomponent",
            unique_together=set(),
        ),
        migrations.RemoveField(
            model_name="stationcomponent",
            name="station",
        ),
        # The new FK is non-nullable from here on, and lives under the
        # canonical `components` related_name.
        migrations.AlterField(
            model_name="stationcomponent",
            name="participant",
            field=models.ForeignKey(
                on_delete=models.deletion.CASCADE,
                related_name="components",
                to="core.participant",
            ),
        ),
        migrations.AlterUniqueTogether(
            name="stationcomponent",
            unique_together={("participant", "idx")},
        ),
        # 5. Drop StationDescription.
        migrations.DeleteModel(name="StationDescription"),
    ]
