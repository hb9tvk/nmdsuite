"""Add a 50-char ``caption`` to ``ParticipantPicture`` so participants
can label each uploaded image. CR on top of F3.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0012_participant_report_and_pictures"),
    ]

    operations = [
        migrations.AddField(
            model_name="participantpicture",
            name="caption",
            field=models.CharField(blank=True, max_length=50),
        ),
    ]
