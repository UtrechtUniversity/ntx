from django.urls import path

from . import views


app_name = "ntx"

urlpatterns = [
    path("", views.home, name="home"),
]
