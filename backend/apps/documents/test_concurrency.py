"""Real PostgreSQL transactions; SQLite deliberately cannot validate this.
Mirrors apps.activity.test_durable_work_concurrency's "two concurrent
claimants never double-claim the same row" coverage, applied to
purge_expired_documents' own SELECT...FOR UPDATE SKIP LOCKED batching
(Document isn't a DurableWorkModel, so it doesn't go through claim_due,
but uses the identical locking idiom directly).
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless

from django.db import connection, connections
from django.test import TransactionTestCase
from django.utils import timezone

from apps.tenancy.models import Tenant, User

from .models import Document
from .services import purge_expired_documents, upload_document
from .storage import resolve_storage_backend
from .testing import TemporaryDocumentStorageMixin, make_upload


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL transaction semantics")
class PurgeExpiredDocumentsConcurrencyTests(TemporaryDocumentStorageMixin, TransactionTestCase):
    def setUp(self):
        super().setUp()
        self.tenant = Tenant.objects.create(name="Test School", slug="test-school")
        self.uploader = User.objects.create_user(username="uploader", password="secret")
        self.document_count = 6
        for index in range(self.document_count):
            document = upload_document(
                tenant=self.tenant, uploaded_by=self.uploader, file_obj=make_upload(name=f"doc-{index}.pdf"),
                original_filename=f"doc-{index}.pdf", content_type="application/pdf",
            )
            Document.objects.filter(pk=document.pk).update(retention_expires_at=timezone.now() - timezone.timedelta(days=1))

    def _with_bounded_connection(self, barrier, fn):
        connections.close_all()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout = '8s'")
                cursor.execute("SET lock_timeout = '6s'")
            barrier.wait(timeout=6)
            return fn()
        finally:
            connections.close_all()

    def test_two_concurrent_purge_batches_never_double_delete_the_same_document(self):
        barrier = Barrier(2)

        def purge_one_at_a_time():
            return purge_expired_documents(limit=1)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self._with_bounded_connection, barrier, purge_one_at_a_time) for _ in range(2)]
            results = [future.result(timeout=15) for future in futures]

        self.assertEqual(sum(results), self.document_count)
        self.assertEqual(Document.objects.filter(tenant=self.tenant).count(), 0)
        self.assertEqual(resolve_storage_backend().list_keys(tenant=self.tenant), [])
