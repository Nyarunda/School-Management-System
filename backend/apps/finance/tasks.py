import datetime

from celery import shared_task
from django.utils import timezone

from .models import IncomingPayment, ReconciliationStatus, TenantMpesaConfiguration
from .services import _attempt_auto_match  # reused as-is; ingest-time auto-match logic is untouched

RESWEEP_BATCH_LIMIT = 100


@shared_task
def resweep_unmatched_incoming_payments(stale_after_seconds=3600):
    """Only tenants with an active M-Pesa configuration get re-swept:
    re-sweeping needs a system actor, and the only per-tenant system actor
    that exists yet is the M-Pesa gateway's. Bounded to RESWEEP_BATCH_LIMIT
    per run rather than an unbounded scan.

    This operates purely on already-ingested IncomingPayment rows, well
    after the M-Pesa callback verify/process boundary -- it is unrelated
    to, and does not touch, that trust boundary. Safe against a concurrent
    bursar manually matching/ignoring the same entry: _attempt_auto_match
    already locks the IncomingPayment (select_for_update) and re-checks
    its status before transitioning it (5B's existing lock-then-check-
    then-transition protocol) -- nothing new needed here, just relying on
    what 5B already guarantees.
    """
    cutoff = timezone.now() - datetime.timedelta(seconds=stale_after_seconds)
    candidates = IncomingPayment.objects.filter(
        status=ReconciliationStatus.UNMATCHED, created_at__lte=cutoff,
    ).select_related("tenant").order_by("created_at")[:RESWEEP_BATCH_LIMIT]
    for incoming in candidates:
        try:
            config = TenantMpesaConfiguration.objects.get(tenant=incoming.tenant, is_active=True)
        except TenantMpesaConfiguration.DoesNotExist:
            continue
        _attempt_auto_match(user=config.system_user, tenant=incoming.tenant, incoming=incoming)
