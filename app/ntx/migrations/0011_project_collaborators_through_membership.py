from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Django cannot add `through=` to an existing many-to-many field, so drop the auto-created
    collaborators table (project creators are already managers in ProjectMembership) and re-add
    the field.
    """

    dependencies = [
        ("ntx", "0010_project_creators_become_managers"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RemoveField(
            model_name="project",
            name="collaborators",
        ),
        migrations.AddField(
            model_name="project",
            name="collaborators",
            field=models.ManyToManyField(
                blank=True,
                related_name="collaborating_projects",
                through="ntx.ProjectMembership",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
