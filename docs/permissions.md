# Permissions (developer notes)

See [quickstart](quickstart.md#roles) for the roles from a user's point of view.

## Where the rules live

- Every logged-in user can view all projects and their related objects (the `EVERYONE` rule).
- `ProjectMembership` (in `ntx/models.py`) stores each collaborator's role (`editor`, `manager`) per project. Roles only matter for adding, changing and deleting.
- `ntx/permissions.py` holds the rules (`PROJECT_RULES`) and `ProjectRoleBackend`, an authentication backend that answers `user.has_perm("ntx.<action>_<model>", obj)` from the user's role in the object's project.
- Project-scoped models declare `project_path` (lookup path to their `Project`) and, where creators get extra rights, `owner_path`. Their managers provide `visible_to(user)` and `with_role(user, minimum_role)`.

The admin and the frontend both use these, so they always agree:

- Lists: `Model.objects.visible_to(request.user)` (everything, for logged-in users). "My projects" uses `with_collaborator(request.user)`: only projects where the user has a membership, ignoring admin status.
- Single objects: `user_can(request.user, Action.CHANGE, Model, obj)` (a typed wrapper around `has_perm`).
- "May the user do this anywhere in this project?" (e.g. add an ingest): `can_in_project(...)`.

Without an object, `ProjectRoleBackend` answers "could the user's roles allow this action somewhere?".
The admin uses that to decide which models, lists and buttons to show. `user.has_perm(...)` also consults Django's `ModelBackend`, so a global user or group permission can make an objectless check succeed even without a project role.
Always check the specific object or target project before performing a project-scoped action.

## Admin

Project-scoped admins inherit `ProjectScopedAdmin` (`ntx/admin.py`).
It checks permissions per object, limits `project` dropdowns to projects where the user may add the object, and validates the chosen project.
Inlines defer to their parent object's permissions.

## Frontend

`ntx.middleware.LoginRequiredMiddleware` requires login everywhere except views marked `@login_not_required`.
JSON endpoints marked `@json_api` get a `401` JSON response instead of a redirect.
Views look up projects with `get_project_or_404`.

## Global permissions (Django groups)

Django's built-in permissions do not need to be added to individual users (so they should not be assigned manually).
Project creation and editing chemicals/concentration units use normal Django permissions, granted to the **Researchers** group (created after `migrate`, see `ntx/groups.py`).

The **Admins** group has the same permissions plus the custom `ntx.manage_all_projects` permission (declared on `Project`).
`is_project_admin(user)` checks it; for admins `role_in()` answers `manager` in every project and `with_role()` returns all rows, so every rule that a manager passes, an admin passes too.
`project_roles()` still returns only actual memberships (shown as "Your role"), and only actual managers count for "a project always keeps at least one manager".
Superusers have every permission, so they are admins as well.

With the configured authentication backends, ordinary global user/group grants affect checks without an object.
Django's `ModelBackend` returns no permissions when an object is supplied, so those grants do not override `ProjectRoleBackend`'s object-specific checks.
For example, an active non-admin user with the global `ntx.change_project` permission but no role in a project gets `True` from `user.has_perm("ntx.change_project")` and `False` from `user.has_perm("ntx.change_project", project)`.

Use project memberships for project-scoped rights.
Keep global grants to project creation (`add_project`), the shared chemical/concentration-unit models, and `manage_all_projects`.
Other project-scoped grants can expose admin controls or satisfy objectless checks without granting access to the corresponding objects.
Only `manage_all_projects` is a deliberate exception: the role layer explicitly checks it without an object and gives the user manager rights in every project.

## Users

All users are staff (`is_staff=True` by default, also via `create_user`), so everyone can log into the Django admin; project roles decide what they can change there.
