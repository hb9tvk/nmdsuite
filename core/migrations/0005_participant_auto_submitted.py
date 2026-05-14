from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0004_participant_submitted_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="participant",
            name="auto_submitted",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "True if submitted_at was set by admin's 'close log submission' "
                    "(rather than by the operator). Used to scope the un-submit "
                    "when reverting the LOGS_CLOSED → LOGS_OPEN transition."
                ),
            ),
        ),
    ]
