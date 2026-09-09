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

    def list_keys(self, *, tenant, min_age_seconds=0):
        """For orphan reconciliation (apps.documents.services.find_orphaned_storage_keys).
        min_age_seconds excludes anything written more recently than that --
        required so an unattended/scheduled sweep can never race a legitimate
        in-flight upload (file written, its Document row not committed yet).
        """
        raise NotImplementedError
