from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError
from django.db.models import Avg
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.generics import ListAPIView, ListCreateAPIView, RetrieveAPIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.academics.models import ClassGroup, Subject, Term
from apps.students.models import Student
from apps.tenancy.services import require_permission

from .models import (
    Assessment,
    AssessmentGradingBand,
    AssessmentResult,
    AssessmentType,
    GradingBand,
    GradingScheme,
    MarkStatus,
)
from .services import (
    add_grading_band,
    approve_assessment,
    create_assessment,
    publish_assessment,
    record_assessment_marks,
    reject_assessment_submission,
    reopen_approved_assessment,
    submit_assessment_for_approval,
)


class AssessmentPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100


def resolve_assessment_tenant(request, permission):
    slug = request.headers.get("X-Tenant-Slug")
    if not slug:
        raise NotFound("Tenant context is required")
    try:
        return require_permission(user=request.user, tenant_slug=slug, permission=permission).tenant
    except DjangoValidationError as error:
        raise PermissionDenied(error.messages) from error


def resolve_tenant_object(queryset, pk):
    try:
        return get_object_or_404(queryset, pk=pk)
    except DjangoValidationError as error:
        raise NotFound("No matching record for the given identifier") from error


def api_validation_error(error):
    return Response({"detail": error.messages}, status=status.HTTP_400_BAD_REQUEST)


class AssessmentTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = AssessmentType
        fields = ["id", "name", "code", "is_active"]
        read_only_fields = ["id"]


class AssessmentTypeListCreateView(ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = AssessmentTypeSerializer
    pagination_class = AssessmentPagination

    def get_queryset(self):
        return AssessmentType.objects.for_tenant(resolve_assessment_tenant(self.request, "assessment.setup.view")).order_by("name")

    def perform_create(self, serializer):
        tenant = resolve_assessment_tenant(self.request, "assessment.setup.manage")
        serializer.save(tenant=tenant)


class GradingBandSerializer(serializers.ModelSerializer):
    class Meta:
        model = GradingBand
        fields = ["id", "grade_label", "min_percentage", "max_percentage", "remark"]
        read_only_fields = ["id"]


class GradingSchemeSerializer(serializers.ModelSerializer):
    bands = GradingBandSerializer(many=True, read_only=True)

    class Meta:
        model = GradingScheme
        fields = ["id", "name", "academic_level", "is_active", "bands"]
        read_only_fields = ["id", "bands"]


class GradingSchemeListCreateView(ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = GradingSchemeSerializer
    pagination_class = AssessmentPagination

    def get_queryset(self):
        tenant = resolve_assessment_tenant(self.request, "assessment.setup.view")
        return GradingScheme.objects.for_tenant(tenant).select_related("academic_level").prefetch_related("bands").order_by("name")

    def perform_create(self, serializer):
        tenant = resolve_assessment_tenant(self.request, "assessment.setup.manage")
        serializer.save(tenant=tenant)


class GradingBandCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, scheme_id):
        tenant = resolve_assessment_tenant(request, "assessment.setup.manage")
        scheme = get_object_or_404(GradingScheme.objects.for_tenant(tenant), pk=scheme_id)
        try:
            band = add_grading_band(
                user=request.user, tenant=tenant, scheme=scheme,
                grade_label=request.data["grade_label"],
                min_percentage=Decimal(str(request.data["min_percentage"])),
                max_percentage=Decimal(str(request.data["max_percentage"])),
                remark=request.data.get("remark", ""),
            )
        except (KeyError, DjangoValidationError, ValueError, InvalidOperation, IntegrityError) as error:
            return api_validation_error(error if isinstance(error, DjangoValidationError) else DjangoValidationError(str(error)))
        return Response(GradingBandSerializer(band).data, status=status.HTTP_201_CREATED)


class AssessmentGradingBandSerializer(serializers.ModelSerializer):
    class Meta:
        model = AssessmentGradingBand
        fields = ["id", "grade_label", "min_percentage", "max_percentage", "remark"]
        read_only_fields = fields


class AssessmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Assessment
        fields = [
            "id", "term", "assessment_type", "class_group", "subject", "name", "max_marks", "scheduled_date",
            "grading_scheme", "created_by", "status", "submitted_at", "approved_at", "published_at", "last_amended_at",
        ]
        read_only_fields = fields


class AssessmentResultSerializer(serializers.ModelSerializer):
    class Meta:
        model = AssessmentResult
        fields = ["id", "student", "mark_status", "score", "grade", "remarks", "recorded_by", "updated_at"]
        read_only_fields = fields


class AssessmentOpenSerializer(serializers.Serializer):
    term = serializers.UUIDField()
    class_group = serializers.UUIDField()
    subject = serializers.UUIDField()
    assessment_type = serializers.UUIDField()
    name = serializers.CharField(max_length=120)
    max_marks = serializers.DecimalField(max_digits=6, decimal_places=2)
    scheduled_date = serializers.DateField()


class AssessmentOpenView(APIView):
    """Creates the assessment, its grading-band snapshot, and its roster
    snapshot (one AssessmentResult per active enrollment) in one call --
    mirrors apps/attendance/api.py's SessionOpenView.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        tenant = resolve_assessment_tenant(request, "assessment.manage")
        serializer = AssessmentOpenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        term = resolve_tenant_object(Term.objects.for_tenant(tenant), str(data["term"]))
        class_group = resolve_tenant_object(ClassGroup.objects.for_tenant(tenant), str(data["class_group"]))
        subject = resolve_tenant_object(Subject.objects.for_tenant(tenant), str(data["subject"]))
        assessment_type = resolve_tenant_object(AssessmentType.objects.for_tenant(tenant), str(data["assessment_type"]))
        try:
            assessment, results = create_assessment(
                user=request.user, tenant=tenant, term=term, class_group=class_group, subject=subject,
                assessment_type=assessment_type, name=data["name"], max_marks=data["max_marks"],
                scheduled_date=data["scheduled_date"],
            )
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response({
            "assessment": AssessmentSerializer(assessment).data,
            "grading_bands": AssessmentGradingBandSerializer(assessment.grading_bands.all(), many=True).data,
            "results": AssessmentResultSerializer(results, many=True).data,
        }, status=status.HTTP_200_OK)


class AssessmentDetailView(RetrieveAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = AssessmentSerializer
    lookup_url_kwarg = "assessment_id"

    def get_queryset(self):
        tenant = resolve_assessment_tenant(self.request, "assessment.record.view")
        return Assessment.objects.filter(tenant=tenant)

    def retrieve(self, request, *args, **kwargs):
        assessment = self.get_object()
        results = AssessmentResult.objects.filter(tenant=assessment.tenant, assessment=assessment).select_related("student")
        return Response({
            "assessment": AssessmentSerializer(assessment).data,
            "grading_bands": AssessmentGradingBandSerializer(assessment.grading_bands.all(), many=True).data,
            "results": AssessmentResultSerializer(results, many=True).data,
        })


class AssessmentEntrySerializer(serializers.Serializer):
    student = serializers.UUIDField()
    mark_status = serializers.ChoiceField(choices=MarkStatus.choices)
    score = serializers.DecimalField(max_digits=6, decimal_places=2, required=False, allow_null=True, default=None)
    remarks = serializers.CharField(max_length=240, required=False, allow_blank=True, default="")


class AssessmentMarksSerializer(serializers.Serializer):
    entries = AssessmentEntrySerializer(many=True)


class AssessmentMarksView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, assessment_id):
        tenant = resolve_assessment_tenant(request, "assessment.record.view")
        assessment = resolve_tenant_object(Assessment.objects.filter(tenant=tenant), assessment_id)
        serializer = AssessmentMarksSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        entries = []
        for entry in serializer.validated_data["entries"]:
            student = resolve_tenant_object(Student.objects.for_tenant(tenant), str(entry["student"]))
            entries.append({
                "student": student, "mark_status": entry["mark_status"],
                "score": entry.get("score"), "remarks": entry.get("remarks", ""),
            })

        try:
            results = record_assessment_marks(user=request.user, tenant=tenant, assessment=assessment, entries=entries)
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response(AssessmentResultSerializer(results, many=True).data, status=status.HTTP_200_OK)


class AssessmentReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=240, required=False, allow_blank=True, default="")


def _transition_view(service_func, needs_reason=False):
    class _View(APIView):
        permission_classes = [IsAuthenticated]

        def post(self, request, assessment_id):
            tenant = resolve_assessment_tenant(request, "assessment.record.view")
            assessment = resolve_tenant_object(Assessment.objects.filter(tenant=tenant), assessment_id)
            kwargs = {}
            if needs_reason:
                serializer = AssessmentReasonSerializer(data=request.data)
                serializer.is_valid(raise_exception=True)
                kwargs["reason"] = serializer.validated_data["reason"]
            try:
                updated = service_func(user=request.user, tenant=tenant, assessment=assessment, **kwargs)
            except DjangoValidationError as error:
                return api_validation_error(error)
            return Response(AssessmentSerializer(updated).data, status=status.HTTP_200_OK)

    return _View


AssessmentSubmitView = _transition_view(submit_assessment_for_approval)
AssessmentRejectView = _transition_view(reject_assessment_submission, needs_reason=True)
AssessmentApproveView = _transition_view(approve_assessment)
AssessmentReopenView = _transition_view(reopen_approved_assessment, needs_reason=True)
AssessmentPublishView = _transition_view(publish_assessment)


class AssessmentListView(ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = AssessmentSerializer
    pagination_class = AssessmentPagination

    def get_queryset(self):
        tenant = resolve_assessment_tenant(self.request, "assessment.record.view")
        queryset = Assessment.objects.filter(tenant=tenant).order_by("-scheduled_date")
        class_group_id = self.request.query_params.get("class_group")
        if class_group_id:
            queryset = queryset.filter(class_group_id=class_group_id)
        term_id = self.request.query_params.get("term")
        if term_id:
            queryset = queryset.filter(term_id=term_id)
        subject_id = self.request.query_params.get("subject")
        if subject_id:
            queryset = queryset.filter(subject_id=subject_id)
        return queryset


class StudentAssessmentSummaryView(APIView):
    """Bounded snapshot, mirrors StudentAttendanceSummaryView /
    StudentFinanceView: recent results + aggregate counts within a default
    window, never full history (that's the separate paginated endpoint).
    """
    permission_classes = [IsAuthenticated]
    RECENT_LIMIT = 10
    SUMMARY_WINDOW_DAYS = 180

    def get(self, request, student_id):
        tenant = resolve_assessment_tenant(request, "assessment.record.view")
        student = resolve_tenant_object(Student.objects.for_tenant(tenant), student_id)

        since = timezone.now().date() - timedelta(days=self.SUMMARY_WINDOW_DAYS)
        window_results = AssessmentResult.objects.filter(
            tenant=tenant, student=student, assessment__scheduled_date__gte=since,
        )
        counts = {choice: 0 for choice, _ in MarkStatus.choices}
        for mark_status_value in window_results.values_list("mark_status", flat=True):
            counts[mark_status_value] = counts.get(mark_status_value, 0) + 1

        scored = window_results.filter(mark_status=MarkStatus.SCORED).select_related("assessment")
        average_percentage = None
        scored_with_pct = [
            (result.score / result.assessment.max_marks) * Decimal("100")
            for result in scored if result.assessment.max_marks
        ]
        if scored_with_pct:
            average_percentage = sum(scored_with_pct) / len(scored_with_pct)

        recent = scored.order_by("-assessment__scheduled_date")[: self.RECENT_LIMIT]
        return Response({
            "student": {"id": student.id, "admission_number": student.admission_number, "name": student.full_name},
            "window_days": self.SUMMARY_WINDOW_DAYS,
            "mark_status_counts": counts,
            "average_percentage": average_percentage,
            "recent_results": AssessmentResultSerializer(recent, many=True).data,
        })


class StudentAssessmentResultListSerializer(serializers.ModelSerializer):
    scheduled_date = serializers.DateField(source="assessment.scheduled_date", read_only=True)
    subject = serializers.PrimaryKeyRelatedField(source="assessment.subject", read_only=True)
    assessment_name = serializers.CharField(source="assessment.name", read_only=True)

    class Meta:
        model = AssessmentResult
        fields = ["id", "scheduled_date", "subject", "assessment_name", "mark_status", "score", "grade", "remarks", "updated_at"]
        read_only_fields = fields


class StudentAssessmentResultListView(ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = StudentAssessmentResultListSerializer
    pagination_class = AssessmentPagination

    def get_queryset(self):
        tenant = resolve_assessment_tenant(self.request, "assessment.record.view")
        student = resolve_tenant_object(Student.objects.for_tenant(tenant), self.kwargs["student_id"])
        return (
            AssessmentResult.objects.filter(tenant=tenant, student=student)
            .select_related("assessment")
            .order_by("-assessment__scheduled_date")
        )
