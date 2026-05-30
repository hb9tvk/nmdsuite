"""Drop ``Participant.op_name``.

The field duplicated the operator's first name captured at registration
(``Participant.first_name``). The station-data form's
"Operator (Vor- und Nachname)" input has been removed; the .nmd upload
parser now ignores OPNAME / EMAIL lines from the logging software for
the same reason.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0010_alter_invalidcallsign_id"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="participant",
            name="op_name",
        ),
    ]
