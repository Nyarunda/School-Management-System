from .models import Student


def list_students(*, tenant):
    return (
        Student.objects.for_tenant(tenant)
        .select_related("campus")
        .only("id", "tenant", "admission_number", "first_name", "last_name", "status", "campus__name")
        .order_by("admission_number")
    )


def enrollment_register_rows(*, tenant, campus_id=None, status=None):
    # A dedicated query rather than reusing list_students(): that selector's
    # .only() is tuned for the list-view shape and deliberately excludes
    # date_of_birth, which this report needs -- reusing it would silently
    # trigger a per-row deferred-field query for every student.
    queryset = Student.objects.for_tenant(tenant).select_related("campus").order_by("admission_number")
    if campus_id:
        queryset = queryset.filter(campus_id=campus_id)
    if status:
        queryset = queryset.filter(status=status)
    return [
        {
            "admission_number": student.admission_number, "full_name": student.full_name, "status": student.status,
            "campus": student.campus.name if student.campus_id else "", "date_of_birth": student.date_of_birth,
        }
        for student in queryset
    ]