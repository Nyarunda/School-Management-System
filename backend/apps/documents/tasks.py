from celery import shared_task

from .services import purge_expired_documents


@shared_task
def purge_expired_documents_task():
    purge_expired_documents()
