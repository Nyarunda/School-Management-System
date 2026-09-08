import uuid

from django.db import models

from apps.activity.durable_work import DurableWorkModel
from apps.tenancy.models import TenantOwnedModel, User


class ReportExportJob(TenantOwnedModel, DurableWorkModel):
    """A durable, idempotent FACT that an export was requested -- mirrors
    NotificationEvent's discipline (apps.notifications.models): requesting
    an export only ever creates this row, all query execution and file
    generation is deferred to async expansion
    (tasks.generate_pending_report_exports), so a slow or misbehaving
    report can never block the requesting HTTP request. params is frozen
    at request time (including any resolved defaults, e.g. an "as_of"
    date), so a job generated minutes later by a worker reflects exactly
    what the requester asked for, not whatever "now" happens to be when
    the worker runs.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    report_code = models.CharField(max_length=80)
    params = models.JSONField(default=dict)
    requested_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    row_count = models.PositiveIntegerField(null=True, blank=True)
    # OneToOne, not ForeignKey: one export produces exactly one artifact --
    # this encodes that invariant at the schema level rather than by convention.
    document = models.OneToOneField("documents.Document", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    idempotency_key = models.CharField(max_length=120, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["tenant", "status", "available_at"])]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "idempotency_key"],
                condition=~models.Q(idempotency_key=""),
                name="unique_report_export_idempotency_per_tenant",
            )
        ]
