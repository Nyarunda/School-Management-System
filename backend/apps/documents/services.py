import hashlib
import mimetypes
import re
import uuid
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.activity.services import record_activity
from apps.tenancy.services import require_permission

from .models import Document, DocumentSetup
from .storage import resolve_storage_backend

# Input validation only -- not file-content security. Real magic-byte
# inspection and malware scanning are a Milestone 22 (Backend-Wide
# Hardening) concern, deliberately not implied to be handled here.
ALLOWED_CONTENT_TYPES = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
}
MAX_UPLOAD_SIZE_BYTES = 10 * 1024 * 1024
CHUNK_SIZE = 64 * 1024

_UNSAFE_FILENAME_CHARS = re.compile(r"[\r\n\x00/\\]")


def _sanitize_filename(original_filename):
    name = _UNSAFE_FILENAME_CHARS.sub("", original_filename or "").strip()
    if not name:
        raise ValidationError("A valid file name is required")
    return name[:255]


def _hashing_size_checked_chunks(file_obj, *, digest, size_holder):
    total = 0
    for chunk in file_obj.chunks(CHUNK_SIZE):
        total += len(chunk)
        if total > MAX_UPLOAD_SIZE_BYTES:
            raise ValidationError(f"File exceeds the maximum upload size of {MAX_UPLOAD_SIZE_BYTES} bytes")
        digest.update(chunk)
        yield chunk
    size_holder["size"] = total


def configure_document_setup(*, user, tenant, default_retention_days=None):
    require_permission(user=user, tenant=tenant, permission="documents.setup.manage")
    setup, _ = DocumentSetup.objects.update_or_create(
        tenant=tenant, defaults={"default_retention_days": default_retention_days},
    )
    return setup


def _compute_retention_expiry(tenant):
    setup = DocumentSetup.objects.filter(tenant=tenant).first()
    if setup is None or setup.default_retention_days is None:
        return None
    return timezone.now() + timedelta(days=setup.default_retention_days)


def upload_document(*, tenant, uploaded_by, file_obj, original_filename, content_type):
    """Streams file_obj while hashing and counting bytes -- size_bytes is
    always the ACTUAL number of bytes written, never a trusted
    client-supplied size. An oversized upload is caught mid-stream (never
    fully buffered or fully written) via _hashing_size_checked_chunks.

    Wraps the physical save + row creation in a compensating cleanup: if
    anything after a successful backend.save() fails, the just-written
    file is deleted so a failed upload never leaves an orphaned physical
    file with no matching row. The remaining half of that dual-write gap
    -- a hard crash between backend.save() returning and this function's
    own return -- can't be closed transactionally; that's what
    find_orphaned_storage_keys/the purge_orphaned_documents management
    command exist for.
    """
    extension = ALLOWED_CONTENT_TYPES.get(content_type)
    if extension is None:
        raise ValidationError(f"Unsupported content type: {content_type}")
    guessed_type, _ = mimetypes.guess_type(original_filename or "")
    if guessed_type is not None and guessed_type != content_type:
        raise ValidationError("File extension does not match the declared content type")

    filename = _sanitize_filename(original_filename)
    storage_key = f"{uuid.uuid4().hex}{extension}"
    backend = resolve_storage_backend()
    digest = hashlib.sha256()
    size_holder = {}
    try:
        backend.save(
            tenant=tenant, key=storage_key,
            chunks=_hashing_size_checked_chunks(file_obj, digest=digest, size_holder=size_holder),
        )
        document = Document.objects.create(
            tenant=tenant, storage_key=storage_key, original_filename=filename, content_type=content_type,
            size_bytes=size_holder["size"], checksum_sha256=digest.hexdigest(), uploaded_by=uploaded_by,
            retention_expires_at=_compute_retention_expiry(tenant),
        )
    except Exception:
        backend.delete(tenant=tenant, key=storage_key)
        raise
    return document


def open_document_stream(*, document):
    """Returns a file-like object for FileResponse -- never buffers the
    whole file into memory.
    """
    backend = resolve_storage_backend()
    return backend.open_for_read(tenant=document.tenant, key=document.storage_key)


def delete_document(*, document):
    """Internal primitive only -- never exposed generically. Each domain
    (Staff/Students/Admissions) wraps this with its own permission/tenant/
    campus checks before calling it, exactly like they already do for
    upload_document.
    """
    backend = resolve_storage_backend()
    backend.delete(tenant=document.tenant, key=document.storage_key)
    document.delete()


def purge_expired_documents(*, limit=200):
    """Bounded SELECT...FOR UPDATE SKIP LOCKED batches, mirroring
    apps.activity.durable_work.claim_due's idiom directly against
    Document.objects -- a Document doesn't need attempts/backoff, just an
    atomic claim so two concurrent purge runs never double-delete the same
    row. Deletes the physical file BEFORE the DB row: a crash between the
    two leaves an orphaned physical file (cleaned up later by
    find_orphaned_storage_keys) rather than a DB row pointing at a file
    that's already gone. record_activity runs before the row is deleted so
    the audit trail survives the SET_NULL that follows on every domain FK
    referencing this row.
    """
    now = timezone.now()
    backend = resolve_storage_backend()
    purged = 0
    while True:
        with transaction.atomic():
            batch = list(
                Document.objects.filter(retention_expires_at__isnull=False, retention_expires_at__lte=now)
                .select_for_update(skip_locked=True)
                .order_by("retention_expires_at")[:limit]
            )
            if not batch:
                break
            for document in batch:
                record_activity(
                    tenant=document.tenant, action="document.purged", resource_type="document", resource_id=str(document.id),
                    metadata={
                        "checksum_sha256": document.checksum_sha256,
                        "original_filename": document.original_filename,
                        "content_type": document.content_type,
                        "size_bytes": document.size_bytes,
                    },
                )
                backend.delete(tenant=document.tenant, key=document.storage_key)
                document.delete()
                purged += 1
    return purged


def find_orphaned_storage_keys(*, tenant):
    """The filesystem/DB dual-write gap that can't be solved
    transactionally: a physical file can be written by upload_document
    just before the OUTER domain-attachment transaction (e.g.
    staff.services.add_employee_document) rolls back, leaving an orphaned
    file with no matching Document row. Exposed via the
    purge_orphaned_documents management command, run manually/on a
    schedule -- not solved transactionally, only reconciled after the
    fact.
    """
    backend = resolve_storage_backend()
    known_keys = set(Document.objects.filter(tenant=tenant).values_list("storage_key", flat=True))
    return [key for key in backend.list_keys(tenant=tenant) if key not in known_keys]
