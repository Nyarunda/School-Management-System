from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import models


class EncryptedCharField(models.CharField):
    """Transparently Fernet-encrypts on write, decrypts on read, keyed by
    settings.FIELD_ENCRYPTION_KEY. Ciphertext is stored as base64 text in a
    plain CharField column -- size it generously (Fernet overhead is
    roughly 1.5x plaintext plus a fixed header). Not filterable/searchable
    by value, by design: these fields (consumer key/secret, passkey) are
    never queried on, only read back for outbound API calls.

    Key rotation is not solved here: changing FIELD_ENCRYPTION_KEY makes
    every existing encrypted value undecryptable (raises ValueError below).
    Documented as an operational limitation, not addressed in this slice --
    rotating it would require re-entering every tenant's M-Pesa credentials
    (a manageable, infrequent, manual operation at current scale).
    """

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if not value:
            return value
        return Fernet(settings.FIELD_ENCRYPTION_KEY).encrypt(value.encode()).decode()

    def from_db_value(self, value, expression, connection):
        if not value:
            return value
        try:
            return Fernet(settings.FIELD_ENCRYPTION_KEY).decrypt(value.encode()).decode()
        except InvalidToken as error:
            raise ValueError("Could not decrypt field -- FIELD_ENCRYPTION_KEY may have changed") from error
