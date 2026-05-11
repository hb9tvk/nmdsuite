"""Make QsoEntry permissive: store invalid entries verbatim.

- utc_time becomes nullable; the canonical raw input lives in utc_raw.
- mode becomes blank-able; we leave it empty when RSTS doesn't parse.
- All previously-required CharFields get blank=True default="" so a half-filled
  entry can persist while the operator finishes typing.

Existing rows are upgraded by copying utc_time → utc_raw (HHMM).
"""
from __future__ import annotations

from django.db import migrations, models


def backfill_utc_raw(apps, schema_editor):
    QsoEntry = apps.get_model("core", "QsoEntry")
    for q in QsoEntry.objects.all():
        if q.utc_time and not q.utc_raw:
            q.utc_raw = q.utc_time.strftime("%H%M")
            q.save(update_fields=["utc_raw"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="qsoentry",
            name="utc_raw",
            field=models.CharField(blank=True, default="", max_length=8),
        ),
        migrations.AlterField(
            model_name="qsoentry",
            name="utc_time",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="qsoentry",
            name="mode",
            field=models.CharField(
                blank=True,
                choices=[("CW", "CW"), ("SSB", "SSB")],
                default="",
                max_length=3,
            ),
        ),
        migrations.AlterField(
            model_name="qsoentry",
            name="remote_call",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
        migrations.AlterField(
            model_name="qsoentry",
            name="rsts",
            field=models.CharField(blank=True, default="", max_length=3),
        ),
        migrations.AlterField(
            model_name="qsoentry",
            name="rstr",
            field=models.CharField(blank=True, default="", max_length=3),
        ),
        migrations.AlterField(
            model_name="qsoentry",
            name="remark",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AlterModelOptions(
            name="qsoentry",
            options={"ordering": ["utc_raw", "id"]},
        ),
        migrations.RunPython(backfill_utc_raw, reverse_code=migrations.RunPython.noop),
    ]
