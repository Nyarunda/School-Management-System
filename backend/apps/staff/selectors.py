from .models import Employee


def employee_register_rows(*, tenant, campus_id=None, status=None):
    queryset = Employee.objects.for_tenant(tenant).select_related("campus").order_by("employee_number")
    if campus_id:
        queryset = queryset.filter(campus_id=campus_id)
    if status:
        queryset = queryset.filter(status=status)
    return [
        {
            "employee_number": employee.employee_number, "full_name": employee.full_name,
            "job_title": employee.job_title, "department": employee.department,
            "employment_type": employee.employment_type, "status": employee.status,
            "campus": employee.campus.name if employee.campus_id else "", "hire_date": employee.hire_date,
        }
        for employee in queryset
    ]
