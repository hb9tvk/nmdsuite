from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import render


@login_required
@user_passes_test(lambda u: u.is_staff)
def placeholder(request):
    return render(request, "placeholder.html", {"module": "Administration"})
