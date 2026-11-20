from django.db import migrations


def creators_become_managers(apps, schema_editor):
    """
    Each project's creator becomes its manager. Other existing collaborators are not carried
    over: every logged-in user can view all projects, and managers add editors as needed.
    """
    Project = apps.get_model("ntx", "Project")
    ProjectMembership = apps.get_model("ntx", "ProjectMembership")

    for project in Project.objects.exclude(created_by__isnull=True).iterator():
        ProjectMembership.objects.update_or_create(
            project_id=project.pk,
            user_id=project.created_by_id,
            defaults={"role": "manager"},
        )


def copy_memberships_to_collaborators(apps, schema_editor):
    Project = apps.get_model("ntx", "Project")
    ProjectMembership = apps.get_model("ntx", "ProjectMembership")
    Collaborator = Project.collaborators.through

    for membership in ProjectMembership.objects.all().iterator():
        Collaborator.objects.get_or_create(
            project_id=membership.project_id,
            user_id=membership.user_id,
        )


class Migration(migrations.Migration):

    dependencies = [
        ("ntx", "0009_project_membership_and_owners"),
    ]

    operations = [
        migrations.RunPython(
            creators_become_managers,
            reverse_code=copy_memberships_to_collaborators,
        ),
    ]
