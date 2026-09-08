from .models import AssessmentResult


def results_sheet_rows(*, tenant, class_group_id, term_id):
    """One row per (student, subject, assessment) -- a flat listing rather
    than a pivoted per-subject grade summary, since a class+term can have
    several assessments per subject. Suitable for a spreadsheet pivot on
    the client side without this report guessing at a specific summary
    shape (e.g. average-per-subject) that isn't universally what's wanted.
    """
    results = (
        AssessmentResult.objects.for_tenant(tenant)
        .filter(assessment__class_group_id=class_group_id, assessment__term_id=term_id)
        .select_related("student", "assessment", "assessment__subject")
        .order_by("student__admission_number", "assessment__subject__name", "assessment__name")
    )
    return [
        {
            "admission_number": result.student.admission_number, "full_name": result.student.full_name,
            "subject": result.assessment.subject.name, "assessment": result.assessment.name,
            "mark_status": result.mark_status, "score": result.score, "grade": result.grade,
        }
        for result in results
    ]
