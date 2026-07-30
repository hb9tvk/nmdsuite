"""Create the Redaktion group for club-magazine editor accounts.

See :mod:`core.roles`. The group carries no Django permissions — access is
gated in application code (``admin_module.views._publish_tools_required``) —
so this just ensures the group exists for superusers to assign members to.
"""
from django.db import migrations

REDAKTION_GROUP = "Redaktion"


def create_group(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.get_or_create(name=REDAKTION_GROUP)


def delete_group(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name=REDAKTION_GROUP).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0013_participantpicture_caption"),
        ("auth", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_group, delete_group),
    ]
