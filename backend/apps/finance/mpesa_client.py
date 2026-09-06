import base64

import requests
from django.utils import timezone

from .models import MpesaEnvironment

SANDBOX_BASE_URL = "https://sandbox.safaricom.co.ke"
PRODUCTION_BASE_URL = "https://api.safaricom.co.ke"
REQUEST_TIMEOUT_SECONDS = 10


class MpesaApiError(Exception):
    pass


class MpesaClient:
    def __init__(self, config):
        self.config = config
        self.base_url = PRODUCTION_BASE_URL if config.environment == MpesaEnvironment.PRODUCTION else SANDBOX_BASE_URL

    def _access_token(self):
        try:
            response = requests.get(
                f"{self.base_url}/oauth/v1/generate?grant_type=client_credentials",
                auth=(self.config.consumer_key, self.config.consumer_secret),
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
        except requests.RequestException as error:
            raise MpesaApiError(f"Could not obtain an M-Pesa access token: {error}") from error
        return response.json()["access_token"]
        # Fetched fresh per call rather than cached for its ~1hr lifetime --
        # simpler and correct; revisit only if this becomes a measured hot
        # path (e.g. worth caching in Redis once a later milestone adds it).

    def stk_push(self, *, phone_number, amount, account_reference, transaction_desc, callback_url):
        # Daraja's Timestamp/Password must be in EAT (Africa/Nairobi), not
        # whatever timezone the server process happens to run in. TIME_ZONE
        # is already "Africa/Nairobi" in settings, so timezone.localtime()
        # on an aware timezone.now() gives the right wall-clock time
        # regardless of the container's OS timezone, and is mockable in
        # tests rather than relying on real wall-clock time.
        timestamp = timezone.localtime(timezone.now()).strftime("%Y%m%d%H%M%S")
        password = base64.b64encode(f"{self.config.shortcode}{self.config.passkey}{timestamp}".encode()).decode()
        payload = {
            "BusinessShortCode": self.config.shortcode,
            "Password": password,
            "Timestamp": timestamp,
            "TransactionType": "CustomerPayBillOnline",
            "Amount": str(int(amount)),
            "PartyA": phone_number,
            "PartyB": self.config.shortcode,
            "PhoneNumber": phone_number,
            "CallBackURL": callback_url,
            "AccountReference": account_reference,
            "TransactionDesc": transaction_desc,
        }
        try:
            response = requests.post(
                f"{self.base_url}/mpesa/stkpush/v1/processrequest",
                json=payload,
                headers={"Authorization": f"Bearer {self._access_token()}"},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
        except requests.RequestException as error:
            raise MpesaApiError(f"STK push request failed: {error}") from error
        return response.json()  # {MerchantRequestID, CheckoutRequestID, ResponseCode, ...}
