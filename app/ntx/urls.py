from django.urls import path

from . import views

app_name = "ntx"

urlpatterns = [
    path("", views.projects_overview, name="home"),
    path("projects/", views.projects_overview, name="projects"),
    path("projects/<slug:slug>/", views.project_detail, name="project_detail"),
    path("experiments/", views.experiments_list, name="experiments"),
    path("experiments/<int:pk>/", views.experiment_detail, name="experiment_detail"),
]
