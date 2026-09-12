from .models import Student


def list_students(*, tenant):
    return (
        Student.objects.for_tenant(tenant)
        .select_related("campus")
        .only("id", "tenant", "admission_number", "first_name", "last_name", "status", "campus__name")
        .order_by("admission_number")
    )