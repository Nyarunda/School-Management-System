from datetime import timedelta

from django.core.exceptions import ValidationError as DjangoValidationError
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.academics.models import ClassGroup
from apps.students.models import Student
from apps.tenancy.services import require_permission

from .models import AttendanceRecord, AttendanceSession, AttendanceStatus
from .services import open_attendance_session, record_attendance_bulk


class AttendancePagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100


def resolve_attendance_tenant(request, permission):
    slug = request.headers.get("X-Tenant-Slug")
    if not slug:
        raise NotFound("Tenant context is required")
    try:
        return require_permission(user=request.user, tenant_slug=slug, permission=permission).tenant
    except DjangoValidationError as error:
        raise PermissionDenied(error.messages) from error


def resolve_tenant_object(queryset, pk):
    """Mirrors apps/finance/api.py's helper of the same name -- a malformed
    identifier must 404, not surface as an unhandled 500.
    """
    try:
        return get_object_or_404(queryset, pk=pk)
    except DjangoValidationError as error:
        raise NotFound("No matching record for the given identifier") from error


def api_validation_error(error):
    return Response({"detail": error.messages}, status=status.HTTP_400_BAD_REQUEST)


class AttendanceRecordSerializer(serializers.ModelSerializer):
    class Meta:
        model = AttendanceRecord
        fields = ["id", "student", "status", "remarks", "recorded_by", "updated_at"]
        read_only_fields = fields


class AttendanceSessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = AttendanceSession
        fields = ["id", "class_group", "session_date", "opened_by", "opened_at", "last_submitted_at"]
        read_only_fields = fields


class SessionOpenSerializer(serializers.Serializer):
    class_group = serializers.UUIDField()
    session_date = serializers.DateField()
    force = serializers.BooleanField(required=False, default=False)


class SessionOpenView(APIView):
    """Opens (or fetches, idempotently) the one register for a class on a
    given day, and returns the resolved roster alongside any existing
    records -- the client renders the roster with whatever's already
    recorded, prefilled, ready for correction.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        tenant = resolve_attendance_tenant(request, "attendance.session.manage")
        serializer = SessionOpenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        class_group = resolve_tenant_object(ClassGroup.objects.for_tenant(tenant), str(data["class_group"]))
        try:
            session, roster = open_attendance_session(
                user=request.user, tenant=tenant, class_group=class_group,
                session_date=data["session_date"], force=data["force"],
            )
        except DjangoValidationError as error:
            return api_validation_error(error)
        existing_records = AttendanceRecord.objects.filter(tenant=tenant, session=session).select_related("student")
        return Response({
            "session": AttendanceSessionSerializer(session).data,
            "roster": [
                {"student_id": enrollment.student_id, "admission_number": enrollment.student.admission_number, "name": enrollment.student.full_name}
                for enrollment in roster
            ],
            "records": AttendanceRecordSerializer(existing_records, many=True).data,
        }, status=status.HTTP_200_OK)


class SessionDetailView(RetrieveAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = AttendanceSessionSerializer
    lookup_url_kwarg = "session_id"

    def get_queryset(self):
        tenant = resolve_attendance_tenant(self.request, "attendance.record.view")
        return AttendanceSession.objects.filter(tenant=tenant)

    def retrieve(self, request, *args, **kwargs):
        session = self.get_object()
        records = AttendanceRecord.objects.filter(tenant=session.tenant, session=session).select_related("student")
        return Response({
            "session": AttendanceSessionSerializer(session).data,
            "records": AttendanceRecordSerializer(records, many=True).data,
        })


class AttendanceEntrySerializer(serializers.Serializer):
    student = serializers.UUIDField()
    status = serializers.ChoiceField(choices=AttendanceStatus.choices)
    remarks = serializers.CharField(max_length=240, required=False, allow_blank=True, default="")


class SessionRecordsSerializer(serializers.Serializer):
    entries = AttendanceEntrySerializer(many=True)


class SessionRecordsView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, session_id):
        tenant = resolve_attendance_tenant(request, "attendance.session.manage")
        session = resolve_tenant_object(AttendanceSession.objects.filter(tenant=tenant), session_id)
        serializer = SessionRecordsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        entries = []
        for entry in serializer.validated_data["entries"]:
            student = resolve_tenant_object(Student.objects.for_tenant(tenant), str(entry["student"]))
            entries.append({"student": student, "status": entry["status"], "remarks": entry.get("remarks", "")})

        try:
            records = record_attendance_bulk(user=request.user, tenant=tenant, session=session, entries=entries)
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response(AttendanceRecordSerializer(records, many=True).data, status=status.HTTP_200_OK)


class SessionListView(ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = AttendanceSessionSerializer
    pagination_class = AttendancePagination

    def get_queryset(self):
        tenant = resolve_attendance_tenant(self.request, "attendance.record.view")
        queryset = AttendanceSession.objects.filter(tenant=tenant).order_by("-session_date", "-opened_at")
        class_group_id = self.request.query_params.get("class_group")
        if class_group_id:
            queryset = queryset.filter(class_group_id=class_group_id)
        date_from = self.request.query_params.get("from")
        if date_from:
            queryset = queryset.filter(session_date__gte=date_from)
        date_to = self.request.query_params.get("to")
        if date_to:
            queryset = queryset.filter(session_date__lte=date_to)
        return queryset


class StudentAttendanceSummaryView(APIView):
    """A bounded snapshot for a student's attendance -- recent records plus
    aggregate status counts over a default recent window, explicitly not
    full history. Mirrors apps/finance/api.py's StudentFinanceView exactly:
    full history is independently paginated through
    /students/{id}/records/, not returned here in full.
    """
    permission_classes = [IsAuthenticated]
    RECENT_LIMIT = 10
    SUMMARY_WINDOW_DAYS = 30

    def get(self, request, student_id):
        tenant = resolve_attendance_tenant(request, "attendance.record.view")
        student = resolve_tenant_object(Student.objects.for_tenant(tenant), student_id)

        since = timezone.now().date() - timedelta(days=self.SUMMARY_WINDOW_DAYS)
        recent_records = (
            AttendanceRecord.objects.filter(tenant=tenant, student=student, session__session_date__gte=since)
            .select_related("session")
            .order_by("-session__session_date")
        )
        counts = {choice: 0 for choice, _ in AttendanceStatus.choices}
        for status_value in recent_records.values_list("status", flat=True):
            counts[status_value] = counts.get(status_value, 0) + 1

        return Response({
            "student": {"id": student.id, "admission_number": student.admission_number, "name": student.full_name},
            "window_days": self.SUMMARY_WINDOW_DAYS,
            "status_counts": counts,
            "recent_records": AttendanceRecordSerializer(recent_records[: self.RECENT_LIMIT], many=True).data,
        })


class StudentAttendanceRecordListSerializer(serializers.ModelSerializer):
    session_date = serializers.DateField(source="session.session_date", read_only=True)
    class_group = serializers.PrimaryKeyRelatedField(source="session.class_group", read_only=True)

    class Meta:
        model = AttendanceRecord
        fields = ["id", "session_date", "class_group", "status", "remarks", "updated_at"]
        read_only_fields = fields


class StudentAttendanceRecordListView(ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = StudentAttendanceRecordListSerializer
    pagination_class = AttendancePagination

    def get_queryset(self):
        tenant = resolve_attendance_tenant(self.request, "attendance.record.view")
        student = resolve_tenant_object(Student.objects.for_tenant(tenant), self.kwargs["student_id"])
        return (
            AttendanceRecord.objects.filter(tenant=tenant, student=student)
            .select_related("session")
            .order_by("-session__session_date")
        )
