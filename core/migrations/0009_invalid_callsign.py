"""Add ``InvalidCallsign`` + ``INVALID_CALL`` scoring status (M4B)."""
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0008_alter_qsoentry_txtr_alter_qsoentry_txts"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="InvalidCallsign",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("callsign", models.CharField(max_length=20)),
                ("flagged_at", models.DateTimeField(auto_now_add=True)),
                ("contest", models.ForeignKey(
                    on_delete=models.deletion.CASCADE,
                    related_name="invalid_callsigns",
                    to="core.contest",
                )),
                ("flagged_by", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=models.deletion.SET_NULL,
                    related_name="+",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                "ordering": ["callsign"],
                "unique_together": {("contest", "callsign")},
            },
        ),
        migrations.AlterField(
            model_name="scoringrecord",
            name="status",
            field=models.CharField(
                choices=[
                    ("unmatched", "Unmatched NMD QSO"),
                    ("full_match", "Full NMD match"),
                    ("text_mismatch", "NMD match with text errors"),
                    ("hb9_qso", "Swiss non-NMD QSO"),
                    ("dx_qso", "DX QSO"),
                    ("admin_accepted", "Admin-accepted unmatched"),
                    ("dupe_deducted", "Duplicate (deducted)"),
                    ("suspected_call_mismatch", "Possibly wrong remote callsign"),
                    ("invalid_call", "Admin-flagged invalid callsign"),
                ],
                default="unmatched",
                max_length=32,
            ),
        ),
        migrations.AlterField(
            model_name="scoringoverride",
            name="forced_status",
            field=models.CharField(
                choices=[
                    ("unmatched", "Unmatched NMD QSO"),
                    ("full_match", "Full NMD match"),
                    ("text_mismatch", "NMD match with text errors"),
                    ("hb9_qso", "Swiss non-NMD QSO"),
                    ("dx_qso", "DX QSO"),
                    ("admin_accepted", "Admin-accepted unmatched"),
                    ("dupe_deducted", "Duplicate (deducted)"),
                    ("suspected_call_mismatch", "Possibly wrong remote callsign"),
                    ("invalid_call", "Admin-flagged invalid callsign"),
                ],
                max_length=32,
            ),
        ),
    ]
