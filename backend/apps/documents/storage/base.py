class DocumentStorageBackend:
    """Pluggable storage interface. Every method streams rather than fully
    buffering a file in memory, and is tenant-scoped -- the caller never
    passes a tenant-prefixed key, the backend owns that namespacing.
    """

    def save(self, *, tenant, key, chunks):
        """chunks: an iterable of bytes. Must fully consume it."""
        raise NotImplementedError

    def open_for_read(self, *, tenant, key):
        """Returns a file-like object suitable for streaming a response."""
        raise NotImplementedError

    def delete(self, *, tenant, key):
        """Idempotent -- deleting a missing key is not an error."""
        raise NotImplementedError

    def list_keys(self, *, tenant):
        """For orphan reconciliation (apps.documents.services.find_orphaned_storage_keys)."""
        raise NotImplementedError
