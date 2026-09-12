from django.core.exceptions import ValidationError as DjangoValidationError
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.generics import ListAPIView, ListCreateAPIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.academics.models import ClassGroup, Subject, Term
from apps.platform.services import require_module_enabled
from apps.tenancy.models import User
from apps.tenancy.services import require_permission

from .models import Period, TimetableEntry
from .services import (
    create_period,
    create_timetable_entry,
    delete_period,
    delete_timetable_entry,
    resolve_class_schedule,
    resolve_teacher_schedule,
    update_period,
    update_timetable_entry,
)


class TimetablePagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100


def resolve_timetable_tenant(request, permission):
    slug = request.headers.get("X-Tenant-Slug")
    if not slug:
        raise NotFound("Tenant context is required")
    try:
        membership = require_permission(user=request.user, tenant_slug=slug, permission=permission)
        require_module_enabled(tenant=membership.tenant, module_code="academics")
        return membership.tenant
    except DjangoValidationError as error:
        raise PermissionDenied(error.messages) from error


def resolve_tenant_object(queryset, pk):
    try:
        return get_object_or_404(queryset, pk=pk)
    except DjangoValidationError as error:
        raise NotFound("No matching record for the given identifier") from error


def api_validation_error(error):
    return Response({"detail": error.messages}, status=status.HTTP_400_BAD_REQUEST)


class PeriodSerializer(serializers.ModelSerializer):
    class Meta:
        model = Period
        fields = ["id", "name", "sequence", "starts_at", "ends_at", "is_teaching_period"]
        read_only_fields = ["id"]


class PeriodListCreateView(ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = PeriodSerializer
    pagination_class = TimetablePagination

    def get_queryset(self):
        return Period.objects.for_tenant(resolve_timetable_tenant(self.request, "timetable.setup.view")).order_by("sequence")

    def create(self, request, *args, **kwargs):
        tenant = resolve_timetable_tenant(request, "timetable.setup.manage")
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            period = create_period(user=request.user, tenant=tenant, **serializer.validated_data)
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response(self.get_serializer(period).data, status=status.HTTP_201_CREATED)


class PeriodDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, period_id):
        tenant = resolve_timetable_tenant(request, "timetable.setup.manage")
        period = resolve_tenant_object(Period.objects.for_tenant(tenant), period_id)
        serializer = PeriodSerializer(period, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        try:
            updated = update_period(user=request.user, tenant=tenant, period=period, **serializer.validated_data)
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response(PeriodSerializer(updated).data)

    def delete(self, request, period_id):
        tenant = resolve_timetable_tenant(request, "timetable.setup.manage")
        period = resolve_tenant_object(Period.objects.for_tenant(tenant), period_id)
        try:
            delete_period(user=request.user, tenant=tenant, period=period)
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response(status=status.HTTP_204_NO_CONTENT)


class TimetableEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = TimetableEntry
        fields = [
            "id", "term", "class_group", "campus", "subject", "teacher", "period", "day_of_week",
            "room", "created_by", "updated_at",
        ]
        read_only_fields = fields


class TimetableEntryCreateSerializer(serializers.Serializer):
    term = serializers.UUIDField()
    class_group = serializers.UUIDField()
    subject = serializers.UUIDField()
    teacher = serializers.UUIDField()
    period = serializers.UUIDField()
    day_of_week = serializers.IntegerField(min_value=1, max_value=7)
    room = serializers.CharField(max_length=60, required=False, allow_blank=True, default="")


class TimetableEntryCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        tenant = resolve_timetable_tenant(request, "timetable.manage")
        serializer = TimetableEntryCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        term = resolve_tenant_object(Term.objects.for_tenant(tenant), str(data["term"]))
        class_group = resolve_tenant_object(ClassGroup.objects.for_tenant(tenant), str(data["class_group"]))
        subject = resolve_tenant_object(Subject.objects.for_tenant(tenant), str(data["subject"]))
        teacher = resolve_tenant_object(User.objects.filter(memberships__tenant=tenant).distinct(), str(data["teacher"]))
        period = resolve_tenant_object(Period.objects.for_tenant(tenant), str(data["period"]))
        try:
            entry = create_timetable_entry(
                user=request.user, tenant=tenant, term=term, class_group=class_group, subject=subject,
                teacher=teacher, period=period, day_of_week=data["day_of_week"], room=data.get("room", ""),
            )
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response(TimetableEntrySerializer(entry).data, status=status.HTTP_200_OK)


class TimetableEntryUpdateSerializer(serializers.Serializer):
    teacher = serializers.UUIDField(required=False)
    room = serializers.CharField(max_length=60, required=False, allow_blank=True)


class TimetableEntryDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, entry_id):
        tenant = resolve_timetable_tenant(request, "timetable.manage")
        entry = resolve_tenant_object(TimetableEntry.objects.filter(tenant=tenant), entry_id)
        serializer = TimetableEntryUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        teacher = None
        if "teacher" in data:
            teacher = resolve_tenant_object(User.objects.filter(memberships__tenant=tenant).distinct(), str(data["teacher"]))
        try:
            updated = update_timetable_entry(user=request.user, tenant=tenant, entry=entry, teacher=teacher, room=data.get("room"))
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response(TimetableEntrySerializer(updated).data)

    def delete(self, request, entry_id):
        tenant = resolve_timetable_tenant(request, "timetable.manage")
        entry = resolve_tenant_object(TimetableEntry.objects.filter(tenant=tenant), entry_id)
        try:
            delete_timetable_entry(user=request.user, tenant=tenant, entry=entry)
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response(status=status.HTTP_204_NO_CONTENT)


class TimetableEntryListView(ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = TimetableEntrySerializer
    pagination_class = TimetablePagination

    def get_queryset(self):
        tenant = resolve_timetable_tenant(self.request, "timetable.record.view")
        queryset = TimetableEntry.objects.filter(tenant=tenant).order_by("day_of_week", "period__sequence")
        term_id = self.request.query_params.get("term")
        if term_id:
            queryset = queryset.filter(term_id=term_id)
        class_group_id = self.request.query_params.get("class_group")
        if class_group_id:
            queryset = queryset.filter(class_group_id=class_group_id)
        teacher_id = self.request.query_params.get("teacher")
        if teacher_id:
            queryset = queryset.filter(teacher_id=teacher_id)
        return queryset


class ClassScheduleView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, class_group_id):
        tenant = resolve_timetable_tenant(request, "timetable.record.view")
        class_group = resolve_tenant_object(ClassGroup.objects.for_tenant(tenant), class_group_id)
        term = resolve_tenant_object(Term.objects.for_tenant(tenant), request.query_params.get("term"))
        entries = resolve_class_schedule(tenant=tenant, term=term, class_group=class_group)
        return Response(TimetableEntrySerializer(entries, many=True).data)


class TeacherScheduleView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, teacher_id):
        tenant = resolve_timetable_tenant(request, "timetable.record.view")
        teacher = resolve_tenant_object(User.objects.filter(memberships__tenant=tenant).distinct(), teacher_id)
        term = resolve_tenant_object(Term.objects.for_tenant(tenant), request.query_params.get("term"))
        entries = resolve_teacher_schedule(tenant=tenant, term=term, teacher=teacher)
        return Response(TimetableEntrySerializer(entries, many=True).data)
