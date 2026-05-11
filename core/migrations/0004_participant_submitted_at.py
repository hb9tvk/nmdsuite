from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0003_drop_qah_m"),
    ]

    operations = [
        migrations.AddField(
            model_name="participant",
            name="submitted_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                help_text="Set when the operator finalises and locks their log + station description",
            ),
        ),
        migrations.RemoveField(
            model_name="stationdescription",
            name="submitted",
        ),
        migrations.RemoveField(
            model_name="stationdescription",
            name="submitted_at",
        ),
    ]
