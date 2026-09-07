import hashlib
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from apps.activity.models import ActivityEvent
from apps.tenancy.models import Membership, Role, Tenant, User

from .models import Document, DocumentSetup
from .services import (
    configure_document_setup,
    delete_document,
    find_orphaned_storage_keys,
    open_document_stream,
    purge_expired_documents,
    upload_document,
)
from .storage import resolve_storage_backend
from .testing import TemporaryDocumentStorageMixin, make_upload


class DocumentFoundationTests(TemporaryDocumentStorageMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.tenant = Tenant.objects.create(name="School A", slug="school-a")
        self.other_tenant = Tenant.objects.create(name="School B", slug="school-b")
        self.admin = User.objects.create_user(username="admin", password="secret")
        self.role = Role.objects.create(tenant=self.tenant, name="Admin", permissions=["documents.setup.view", "documents.setup.manage"])
        Membership.objects.create(tenant=self.tenant, user=self.admin, role=self.role)


class UploadDocumentTests(DocumentFoundationTests):
    def test_upload_records_actual_streamed_size_and_checksum(self):
        content = b"%PDF-1.4 hello world"
        document = upload_document(
            tenant=self.tenant, uploaded_by=self.admin, file_obj=make_upload(content=content),
            original_filename="doc.pdf", content_type="application/pdf",
        )
        self.assertEqual(document.size_bytes, len(content))
        self.assertEqual(document.checksum_sha256, hashlib.sha256(content).hexdigest())
        self.assertEqual(document.original_filename, "doc.pdf")

    def test_unsupported_content_type_is_rejected(self):
        with self.assertRaises(ValidationError):
            upload_document(
                tenant=self.tenant, uploaded_by=self.admin, file_obj=make_upload(content_type="application/zip"),
                original_filename="doc.zip", content_type="application/zip",
            )
        self.assertEqual(Document.objects.count(), 0)

    def test_extension_content_type_mismatch_is_rejected(self):
        with self.assertRaises(ValidationError):
            upload_document(
                tenant=self.tenant, uploaded_by=self.admin, file_obj=make_upload(name="doc.png", content_type="application/pdf"),
                original_filename="doc.png", content_type="application/pdf",
            )
        self.assertEqual(Document.objects.count(), 0)

    def test_oversized_upload_is_rejected_and_leaves_no_orphaned_file(self):
        from . import services

        original_max = services.MAX_UPLOAD_SIZE_BYTES
        services.MAX_UPLOAD_SIZE_BYTES = 10
        try:
            with self.assertRaises(ValidationError):
                upload_document(
                    tenant=self.tenant, uploaded_by=self.admin,
                    file_obj=make_upload(content=b"this content is definitely over ten bytes"),
                    original_filename="doc.pdf", content_type="application/pdf",
                )
        finally:
            services.MAX_UPLOAD_SIZE_BYTES = original_max
        self.assertEqual(Document.objects.count(), 0)
        self.assertEqual(find_orphaned_storage_keys(tenant=self.tenant), [])

    def test_filename_is_sanitized(self):
        document = upload_document(
            tenant=self.tenant, uploaded_by=self.admin, file_obj=make_upload(),
            original_filename="../../etc/passwd\r\n.pdf", content_type="application/pdf",
        )
        self.assertNotIn("/", document.original_filename)
        self.assertNotIn("\\", document.original_filename)
        self.assertNotIn("\r", document.original_filename)
        self.assertNotIn("\n", document.original_filename)

    def test_failed_row_creation_cleans_up_the_physical_file(self):
        with patch("apps.documents.services.Document.objects.create", side_effect=IntegrityError("boom")):
            with self.assertRaises(IntegrityError):
                upload_document(
                    tenant=self.tenant, uploaded_by=self.admin, file_obj=make_upload(),
                    original_filename="doc.pdf", content_type="application/pdf",
                )
        self.assertEqual(Document.objects.count(), 0)
        self.assertEqual(find_orphaned_storage_keys(tenant=self.tenant), [])


class OpenAndDeleteDocumentTests(DocumentFoundationTests):
    def test_open_document_stream_returns_the_stored_bytes(self):
        content = b"%PDF-1.4 stream content"
        document = upload_document(
            tenant=self.tenant, uploaded_by=self.admin, file_obj=make_upload(content=content),
            original_filename="doc.pdf", content_type="application/pdf",
        )
        with open_document_stream(document=document) as stream:
            self.assertEqual(stream.read(), content)

    def test_delete_document_removes_the_row_and_the_physical_file(self):
        document = upload_document(
            tenant=self.tenant, uploaded_by=self.admin, file_obj=make_upload(),
            original_filename="doc.pdf", content_type="application/pdf",
        )
        storage_key = document.storage_key
        delete_document(document=document)
        self.assertEqual(Document.objects.count(), 0)
        self.assertNotIn(storage_key, resolve_storage_backend().list_keys(tenant=self.tenant))


class StorageTenantIsolationTests(DocumentFoundationTests):
    def test_two_tenants_with_the_same_key_do_not_collide(self):
        backend = resolve_storage_backend()
        backend.save(tenant=self.tenant, key="shared.pdf", chunks=[b"tenant-a-bytes"])
        backend.save(tenant=self.other_tenant, key="shared.pdf", chunks=[b"tenant-b-bytes"])
        with backend.open_for_read(tenant=self.tenant, key="shared.pdf") as handle:
            self.assertEqual(handle.read(), b"tenant-a-bytes")
        with backend.open_for_read(tenant=self.other_tenant, key="shared.pdf") as handle:
            self.assertEqual(handle.read(), b"tenant-b-bytes")

    def test_path_traversal_key_is_rejected(self):
        backend = resolve_storage_backend()
        with self.assertRaises(ValueError):
            backend.save(tenant=self.tenant, key="../../../etc/passwd", chunks=[b"malicious"])


class ConfigureDocumentSetupTests(DocumentFoundationTests):
    def test_configure_and_read_back(self):
        setup = configure_document_setup(user=self.admin, tenant=self.tenant, default_retention_days=30)
        self.assertEqual(setup.default_retention_days, 30)
        self.assertEqual(DocumentSetup.objects.get(tenant=self.tenant).default_retention_days, 30)

    def test_requires_permission(self):
        viewer = User.objects.create_user(username="viewer", password="secret")
        Membership.objects.create(
            tenant=self.tenant, user=viewer,
            role=Role.objects.create(tenant=self.tenant, name="Viewer", permissions=["documents.setup.view"]),
        )
        with self.assertRaises(ValidationError):
            configure_document_setup(user=viewer, tenant=self.tenant, default_retention_days=30)


class RetentionTests(DocumentFoundationTests):
    def test_no_setup_means_no_retention_expiry(self):
        document = upload_document(
            tenant=self.tenant, uploaded_by=self.admin, file_obj=make_upload(),
            original_filename="doc.pdf", content_type="application/pdf",
        )
        self.assertIsNone(document.retention_expires_at)

    def test_retention_is_snapshotted_and_not_retroactive(self):
        configure_document_setup(user=self.admin, tenant=self.tenant, default_retention_days=30)
        first = upload_document(
            tenant=self.tenant, uploaded_by=self.admin, file_obj=make_upload(),
            original_filename="a.pdf", content_type="application/pdf",
        )
        self.assertIsNotNone(first.retention_expires_at)

        configure_document_setup(user=self.admin, tenant=self.tenant, default_retention_days=None)
        second = upload_document(
            tenant=self.tenant, uploaded_by=self.admin, file_obj=make_upload(),
            original_filename="b.pdf", content_type="application/pdf",
        )
        self.assertIsNone(second.retention_expires_at)

        first.refresh_from_db()
        self.assertIsNotNone(first.retention_expires_at)


class PurgeExpiredDocumentsTests(DocumentFoundationTests):
    def _make_expired_document(self, name="expired.pdf"):
        document = upload_document(
            tenant=self.tenant, uploaded_by=self.admin, file_obj=make_upload(name=name),
            original_filename=name, content_type="application/pdf",
        )
        Document.objects.filter(pk=document.pk).update(retention_expires_at=timezone.now() - timezone.timedelta(days=1))
        document.refresh_from_db()
        return document

    def test_purge_deletes_the_row_and_the_physical_file(self):
        document = self._make_expired_document()
        storage_key = document.storage_key
        purged = purge_expired_documents()
        self.assertEqual(purged, 1)
        self.assertEqual(Document.objects.count(), 0)
        self.assertNotIn(storage_key, resolve_storage_backend().list_keys(tenant=self.tenant))

    def test_purge_records_an_audit_trail_before_deleting(self):
        document = self._make_expired_document()
        checksum = document.checksum_sha256
        purge_expired_documents()
        event = ActivityEvent.objects.get(action="document.purged")
        self.assertEqual(event.metadata["checksum_sha256"], checksum)
        self.assertEqual(event.metadata["original_filename"], "expired.pdf")

    def test_documents_not_yet_expired_are_left_alone(self):
        configure_document_setup(user=self.admin, tenant=self.tenant, default_retention_days=30)
        upload_document(
            tenant=self.tenant, uploaded_by=self.admin, file_obj=make_upload(),
            original_filename="not-expired.pdf", content_type="application/pdf",
        )
        purged = purge_expired_documents()
        self.assertEqual(purged, 0)
        self.assertEqual(Document.objects.count(), 1)


class FindOrphanedStorageKeysTests(DocumentFoundationTests):
    def test_a_file_with_no_matching_row_is_reported_as_orphaned(self):
        backend = resolve_storage_backend()
        backend.save(tenant=self.tenant, key="orphan.pdf", chunks=[b"orphan bytes"])
        self.assertEqual(find_orphaned_storage_keys(tenant=self.tenant), ["orphan.pdf"])

    def test_a_file_with_a_matching_row_is_not_reported(self):
        upload_document(
            tenant=self.tenant, uploaded_by=self.admin, file_obj=make_upload(),
            original_filename="doc.pdf", content_type="application/pdf",
        )
        self.assertEqual(find_orphaned_storage_keys(tenant=self.tenant), [])
