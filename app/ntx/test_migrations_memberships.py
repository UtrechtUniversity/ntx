from __future__ import annotations

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

pytestmark = pytest.mark.django_db(transaction=True)

BEFORE = [
    ("ntx", "0009_project_membership_and_owners"),
    ("ntx_users", "0002_user_is_staff_default"),
]
AFTER = [("ntx", "0011_project_collaborators_through_membership")]


def _migrate(targets):
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(targets)
    return executor.loader.project_state(targets).apps


def test_only_project_creators_become_managers():
    try:
        old_apps = _migrate(BEFORE)
        User = old_apps.get_model("ntx_users", "User")
        Project = old_apps.get_model("ntx", "Project")

        creator = User.objects.create(username="creator")
        collaborator = User.objects.create(username="collaborator")
        project = Project.objects.create(name="P", slug="p", created_by=creator)
        orphan = Project.objects.create(name="Orphan", slug="orphan")
        project.collaborators.add(creator, collaborator)
        orphan.collaborators.add(collaborator)

        new_apps = _migrate(AFTER)
        Membership = new_apps.get_model("ntx", "ProjectMembership")
        roles = {
            (m.project.slug, m.user.username): m.role
            for m in Membership.objects.select_related("project", "user")
        }
        assert roles == {("p", "creator"): "manager"}
    finally:
        _migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())
