from celery import shared_task

from apps.activity.durable_work import claim_due, reap_stale

from .models import ReportExportJob
from .services import generate_report_export


@shared_task
def generate_pending_report_exports():
    for job in claim_due(ReportExportJob.objects.all(), limit=50):
        try:
            generate_report_export(job=job)
        except Exception as error:
            job.mark_failed(error)
            continue
        job.mark_processed()


@shared_task
def reap_stale_report_exports():
    reap_stale(ReportExportJob.objects.all())
