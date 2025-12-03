from django.shortcuts import render


def home(request):
    """Render the site home page."""
    return render(request, "ntx/home.html")
