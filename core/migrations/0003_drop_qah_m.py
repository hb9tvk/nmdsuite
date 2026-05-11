from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0002_permissive_qso"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="stationdescription",
            name="qah_m",
        ),
    ]
