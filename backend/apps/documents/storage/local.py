import os
from pathlib import Path

from django.conf import settings

from .base import DocumentStorageBackend


class LocalFilesystemBackend(DocumentStorageBackend):
    """Namespaces every key under <DOCUMENT_STORAGE_ROOT>/<tenant.id>/ --
    callers never pass a tenant-prefixed key, so a caller bug can't smuggle
    a path outside its own tenant's directory. Every method re-resolves
    the final path and asserts it is still inside the tenant's root before
    touching disk -- defense in depth on top of storage_key always being
    an internally-generated UUID that can't contain a path separator.
    """

    def _tenant_root(self, tenant):
        root = Path(settings.DOCUMENT_STORAGE_ROOT) / str(tenant.id)
        root.mkdir(parents=True, exist_ok=True)
        return root.resolve()

    def _resolve_path(self, *, tenant, key):
        root = self._tenant_root(tenant)
        path = (root / key).resolve()
        if path != root and root not in path.parents:
            raise ValueError(f"Refusing to access a path outside the tenant's storage root: {key}")
        return path

    def save(self, *, tenant, key, chunks):
        path = self._resolve_path(tenant=tenant, key=key)
        tmp_path = path.parent / f"{path.name}.partial"
        try:
            with open(tmp_path, "wb") as handle:
                for chunk in chunks:
                    handle.write(chunk)
            os.replace(tmp_path, path)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

    def open_for_read(self, *, tenant, key):
        path = self._resolve_path(tenant=tenant, key=key)
        return open(path, "rb")

    def delete(self, *, tenant, key):
        path = self._resolve_path(tenant=tenant, key=key)
        path.unlink(missing_ok=True)

    def list_keys(self, *, tenant):
        root = self._tenant_root(tenant)
        return [item.name for item in root.iterdir() if item.is_file() and not item.name.endswith(".partial")]
