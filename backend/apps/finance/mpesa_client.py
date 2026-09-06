import base64
from decimal import Decimal, InvalidOperation

import requests
from django.utils import timezone

from .models import MpesaEnvironment

SANDBOX_BASE_URL = "https://sandbox.safaricom.co.ke"
PRODUCTION_BASE_URL = "https://api.safaricom.co.ke"
REQUEST_TIMEOUT_SECONDS = 10


class MpesaApiError(Exception):
    pass


def whole_shilling_amount(value):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise MpesaApiError("Amount must be a positive whole number of shillings") from None
    if not amount.is_finite() or amount <= 0 or amount != amount.to_integral_value() or amount > Decimal("9999999999"):
        raise MpesaApiError("Amount must be a positive whole number of shillings, at most 9999999999")
    return amount


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
        try:
            token = response.json()["access_token"]
            if not isinstance(token, str) or not token:
                raise ValueError()
            return token
        except (ValueError, KeyError, TypeError):
            raise MpesaApiError("M-Pesa returned an invalid access-token response") from None
        # Fetched fresh per call rather than cached for its ~1hr lifetime --
        # simpler and correct; revisit only if this becomes a measured hot
        # path (e.g. worth caching in Redis once a later milestone adds it).

    def stk_push(self, *, phone_number, amount, account_reference, transaction_desc, callback_url):
        amount = whole_shilling_amount(amount)
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
        try:
            data = response.json()
            if str(data.get("ResponseCode")) != "0":
                raise ValueError()
            if any(not isinstance(data.get(key), str) or not 0 < len(data[key]) <= 60
                   for key in ("MerchantRequestID", "CheckoutRequestID")):
                raise ValueError()
            return data
        except (ValueError, KeyError, TypeError, AttributeError):
            raise MpesaApiError("M-Pesa did not return a valid accepted STK request") from None

    def query_stk(self, *, checkout_request_id):
        timestamp = timezone.localtime(timezone.now()).strftime("%Y%m%d%H%M%S")
        password = base64.b64encode(f"{self.config.shortcode}{self.config.passkey}{timestamp}".encode()).decode()
        try:
            response = requests.post(
                f"{self.base_url}/mpesa/stkpushquery/v1/query",
                json={"BusinessShortCode": self.config.shortcode, "Password": password,
                      "Timestamp": timestamp, "CheckoutRequestID": checkout_request_id},
                headers={"Authorization": f"Bearer {self._access_token()}"},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict) or data.get("CheckoutRequestID") != checkout_request_id or "ResultCode" not in data:
                raise ValueError()
            return {key: data[key] for key in ("CheckoutRequestID", "ResultCode", "ResultDesc") if key in data}
        except (requests.RequestException, ValueError, TypeError):
            raise MpesaApiError("Could not obtain a valid M-Pesa STK status response") from None
