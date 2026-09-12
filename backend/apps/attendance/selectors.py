from django.db.models import Count, Q

from .models import AttendanceRecord, AttendanceStatus


def absence_summary_rows(*, tenant, start_date, end_date, campus_id=None, limit=None):
    records = (
        AttendanceRecord.objects.for_tenant(tenant)
        .filter(session__session_date__gte=start_date, session__session_date__lte=end_date)
    )
    if campus_id:
        # Campus scoping goes through the session's class group, not
        # Student.campus -- attendance rosters are class-based, and a
        # student's own `campus` field isn't necessarily kept in sync with
        # which class/campus they actually attend (StudentEnrollment is
        # the source of truth there).
        records = records.filter(session__class_group__campus_id=campus_id)
    aggregated = (
        records.values("student__admission_number", "student__first_name", "student__last_name")
        .annotate(
            total_sessions=Count("id"),
            absent_count=Count("id", filter=Q(status=AttendanceStatus.ABSENT)),
            late_count=Count("id", filter=Q(status=AttendanceStatus.LATE)),
        )
        .order_by("student__admission_number")
    )
    # As with collections_summary_rows, this bounds the aggregated row count
    # but not the underlying scan cost of the GROUP BY itself.
    if limit is not None:
        aggregated = aggregated[:limit]
    return [
        {
            "admission_number": row["student__admission_number"],
            "full_name": f"{row['student__first_name']} {row['student__last_name']}",
            "total_sessions": row["total_sessions"], "absent_count": row["absent_count"], "late_count": row["late_count"],
        }
        for row in aggregated
    ]
