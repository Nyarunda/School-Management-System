from django.conf import settings
from django.utils.module_loading import import_string


def resolve_storage_backend():
    """Pluggable storage resolution -- local filesystem by default (see
    local.py), swappable for a future cloud backend purely via settings,
    with no change to services.py or any caller.
    """
    backend_path = getattr(
        settings, "DOCUMENT_STORAGE_BACKEND", "apps.documents.storage.local.LocalFilesystemBackend",
    )
    backend_class = import_string(backend_path)
    return backend_class()
