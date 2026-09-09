from celery import shared_task

from .services import purge_expired_documents, purge_orphaned_documents


@shared_task
def purge_expired_documents_task():
    purge_expired_documents()


@shared_task
def purge_orphaned_documents_task():
    """RC Area 3: operationalizes the previously-manual-only
    purge_orphaned_documents management command. Safe to schedule now that
    find_orphaned_storage_keys enforces a minimum-age guard (default 1
    hour) -- a file only becomes a purge candidate once it's old enough
    that its calling transaction has certainly either committed or rolled
    back.
    """
    purge_orphaned_documents()
