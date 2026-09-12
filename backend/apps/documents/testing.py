import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings


class TemporaryDocumentStorageMixin:
    """Redirects DOCUMENT_STORAGE_ROOT to a fresh temp directory for the
    duration of each test, so tests that exercise real file uploads never
    write into the repo-local document_storage/ directory and never leak
    files between test runs.
    """

    def setUp(self):
        super().setUp()
        self._document_storage_dir = tempfile.mkdtemp()
        override = override_settings(DOCUMENT_STORAGE_ROOT=self._document_storage_dir)
        override.enable()
        self.addCleanup(override.disable)
        self.addCleanup(shutil.rmtree, self._document_storage_dir, ignore_errors=True)


def make_upload(*, name="document.pdf", content=b"%PDF-1.4 test content", content_type="application/pdf"):
    return SimpleUploadedFile(name, content, content_type=content_type)
