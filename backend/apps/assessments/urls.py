from django.urls import path

from .api import (
    AssessmentApproveView,
    AssessmentDetailView,
    AssessmentListView,
    AssessmentMarksView,
    AssessmentOpenView,
    AssessmentPublishView,
    AssessmentRejectView,
    AssessmentReopenView,
    AssessmentSubmitView,
    AssessmentTypeListCreateView,
    GradingBandCreateView,
    GradingSchemeListCreateView,
    StudentAssessmentResultListView,
    StudentAssessmentSummaryView,
)

urlpatterns = [
    path("types/", AssessmentTypeListCreateView.as_view(), name="assessment-type-list"),
    path("grading-schemes/", GradingSchemeListCreateView.as_view(), name="grading-scheme-list"),
    path("grading-schemes/<uuid:scheme_id>/bands/", GradingBandCreateView.as_view(), name="grading-band-create"),
    path("assessments/", AssessmentListView.as_view(), name="assessment-list"),
    path("assessments/open/", AssessmentOpenView.as_view(), name="assessment-open"),
    path("assessments/<uuid:assessment_id>/", AssessmentDetailView.as_view(), name="assessment-detail"),
    path("assessments/<uuid:assessment_id>/marks/", AssessmentMarksView.as_view(), name="assessment-marks"),
    path("assessments/<uuid:assessment_id>/submit/", AssessmentSubmitView.as_view(), name="assessment-submit"),
    path("assessments/<uuid:assessment_id>/reject/", AssessmentRejectView.as_view(), name="assessment-reject"),
    path("assessments/<uuid:assessment_id>/approve/", AssessmentApproveView.as_view(), name="assessment-approve"),
    path("assessments/<uuid:assessment_id>/reopen/", AssessmentReopenView.as_view(), name="assessment-reopen"),
    path("assessments/<uuid:assessment_id>/publish/", AssessmentPublishView.as_view(), name="assessment-publish"),
    path("students/<uuid:student_id>/summary/", StudentAssessmentSummaryView.as_view(), name="assessment-student-summary"),
    path("students/<uuid:student_id>/results/", StudentAssessmentResultListView.as_view(), name="assessment-student-results"),
]
