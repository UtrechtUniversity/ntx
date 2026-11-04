from __future__ import annotations

from collections.abc import Callable
from typing import Any

from django.contrib.auth.middleware import (
    LoginRequiredMiddleware as DjangoLoginRequiredMiddleware,
)
from django.http import HttpRequest, HttpResponseBase, JsonResponse


def json_api[F: Callable[..., HttpResponseBase]](view: F) -> F:
    """
    Mark a view as a JSON endpoint: unauthenticated requests get a JSON 401 response
    instead of a redirect to the login page.
    """
    setattr(view, "json_api", True)
    return view


class LoginRequiredMiddleware(DjangoLoginRequiredMiddleware):
    def process_view(
        self,
        request: HttpRequest,
        view_func: Callable[..., HttpResponseBase],
        view_args: tuple[Any, ...],
        view_kwargs: dict[Any, Any],
    ) -> HttpResponseBase | None:
        response = super().process_view(request, view_func, view_args, view_kwargs)
        if response is not None and getattr(view_func, "json_api", False):
            return JsonResponse({"error": "Authentication required."}, status=401)
        return response
