from django.shortcuts import render


def placeholder(request):
    return render(request, "placeholder.html", {"module": "Registration"})
