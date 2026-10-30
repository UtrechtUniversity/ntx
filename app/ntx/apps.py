from django.apps import AppConfig
from django.db.models.signals import post_migrate


class NtxConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "ntx"

    def ready(self) -> None:
        from .groups import ensure_groups

        post_migrate.connect(
            ensure_groups,
            sender=self,
            dispatch_uid="ntx.ensure_groups",
        )
