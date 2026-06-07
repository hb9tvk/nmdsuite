"""Add ``ParticipantReport`` (post-contest writeup) and
``ParticipantPicture`` (up to 6 attached photos per participant). F3.1.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0011_remove_participant_op_name"),
    ]

    operations = [
        migrations.CreateModel(
            name="ParticipantReport",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("text", models.CharField(blank=True, max_length=4096)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "participant",
                    models.OneToOneField(
                        on_delete=models.deletion.CASCADE,
                        related_name="report",
                        to="core.participant",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="ParticipantPicture",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("idx", models.PositiveSmallIntegerField()),
                ("extension", models.CharField(max_length=8)),
                ("original_filename", models.CharField(max_length=255)),
                ("content_type", models.CharField(max_length=64)),
                ("file_size", models.PositiveIntegerField()),
                ("uploaded_at", models.DateTimeField(auto_now_add=True)),
                (
                    "participant",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="pictures",
                        to="core.participant",
                    ),
                ),
            ],
            options={
                "ordering": ["idx"],
                "unique_together": {("participant", "idx")},
            },
        ),
    ]
