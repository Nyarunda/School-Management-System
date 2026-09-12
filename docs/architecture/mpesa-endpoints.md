# M-Pesa backend: verification and recovery

This change affects backend services, models, migrations, and endpoints only. No frontend changes are included.

## Trust and settlement

The callback URL token routes a delivery to a school. It does not prove that Safaricom collected money. Public C2B confirmations and STK callbacks now commit a durable inbox record and acknowledge receipt without posting a payment. Operators must independently check the provider record, record an evidence reference, and invoke processing using separate permissions. Merely typing a reference is not automated provider verification: the operator is responsible for checking the provider transaction, account, amount, receipt, and student association.

The inspected official Safaricom SDK documents the STK status query at `/mpesa/stkpushquery/v1/query`: [Safaricom SDK](https://github.com/safaricom/mpesa-php-sdk/blob/master/src/Mpesa.php). The backend implements that query. Its result is retained as evidence; a status result alone does not establish a receipt number or amount and does not mint a payment. No unsupported callback signature scheme, IP allowlist, or retry guarantee is assumed. C2B automated provider verification is not implemented; it uses the explicit operator verification workflow.

Callback processing locks the inbox item, then the STK request where applicable. Reconciliation and terminal STK state commit in one transaction. Duplicate successful deliveries with the same receipt and amount are idempotent; conflicting receipts, amounts, merchant/checkout identities, or terminal results are rejected. Distinct checkouts cannot share a confirmed receipt. Processing failures roll back financial changes while keeping inbox state, attempt count, verification evidence, and a safe error summary. Unexpected defects still surface as server errors after the failure record commits.

STK success uses the known student identity, not fuzzy reference recognition. A confirmed amount mismatch is held for manual reconciliation. Legacy completed requests without the new receipt column compare against their existing incoming payment identity.

## HTTP contract

All paths below are relative to `/api/v1/finance/`. Authenticated endpoints require an active user, school membership, `X-Tenant-Slug`, and the listed permission. Session-authenticated writes require CSRF protection. List endpoints use `page` and `page_size` (25 by default, maximum 100).

| Method | Path | Permission | Purpose / input |
|---|---|---|---|
| GET | `mpesa-config/` | `finance.mpesa.configure` | Read environment, shortcode, activation, and callback token; never credentials |
| POST | `mpesa-config/` | `finance.mpesa.configure` | Set `environment`, `shortcode`, `consumer_key`, `consumer_secret`, `passkey` |
| POST | `mpesa-config/rotate-callback-token/` | `finance.mpesa.configure` | Rotate callback routing secret; update provider registrations afterward |
| POST | `mpesa/stk-push/` | `finance.mpesa.stk_push.initiate` | `student`, `phone_number`, `amount`, **`idempotency_key`**, optional `invoice` |
| GET | `mpesa/stk-requests/` | `finance.mpesa.stk_push.view` | Paginated request history |
| GET | `mpesa/stk-requests/{request_id}/` | `finance.mpesa.stk_push.view` | Status, identifiers, query evidence, and reconciliation result |
| POST | `mpesa/stk-requests/{request_id}/identify/` | `finance.mpesa.stk_push.reconcile` | Recover a lost response using independently checked `checkout_request_id`, `merchant_request_id`, `evidence` |
| POST | `mpesa/stk-requests/{request_id}/query/` | `finance.mpesa.stk_push.query` | Ask provider for status; no financial posting |
| GET | `mpesa/callbacks/` | `finance.mpesa.callback.view` | Paginated inbox; optional validated `status` filter |
| GET | `mpesa/callbacks/{callback_id}/` | `finance.mpesa.callback.view` | Inbox details including raw payload; restrict this permission to operators needing the data |
| POST | `mpesa/callbacks/{callback_id}/verify/` | `finance.mpesa.callback.verify` | `evidence`: reference to independently checked provider record |
| POST | `mpesa/callbacks/{callback_id}/process/` | `finance.mpesa.callback.process` | Process/replay verified item; repeated processed item is a no-op |
| POST | `mpesa/callbacks/{callback_id}/reject/` | `finance.mpesa.callback.verify` | `reason`; rejection prevents later processing |
| POST | `mpesa/{callback_token}/c2b/validation/` | Public, token-routed | Record delivery and perform structural validation |
| POST | `mpesa/{callback_token}/c2b/confirmation/` | Public, token-routed | Retain unverified confirmation; acknowledge durable receipt |
| POST | `mpesa/{callback_token}/stk/callback/` | Public, token-routed | Retain legacy-format callback |
| POST | `mpesa/{callback_token}/stk/callback/{request_id}/` | Public, token-routed | Retain callback correlated to a pre-existing local intent |

New permissions are not automatically granted to existing school roles. Provision only the responsibilities each operator needs. The gateway system identity has financial processing permissions but receives no inbox-verification permission.

STK amounts must be positive whole shillings, at most 9,999,999,999; `1000.00` is accepted, `1000.50`, `NaN`, infinity, zero, and negative amounts are rejected. Credential input remains limited to 500 plaintext characters; encrypted storage is widened to 1024 characters.

Example initiation body:

```json
{"student":"<student UUID>","phone_number":"0712345678","amount":"1000.00","idempotency_key":"<stable key for this payment attempt>"}
```

Keep that key across HTTP retries. A different payload with the same key returns 400. Successful initiation/replay returns 201; unresolved initiation returns 202 with a local request ID. Do not automatically generate a new key after a timeout: it can send a second collection prompt.

## Recovery sequence

1. Local intent commits as `INITIATING` before external HTTP. The callback URL includes its UUID.
2. An accepted provider response updates it to `PENDING`. A transport/response error leaves `UNKNOWN`. Process death may leave `INITIATING`; either state is visible in history and neither is automatically resent.
3. Early callbacks remain in the inbox even before the provider response is saved. Verified processing can bind the callback checkout ID to the correlated local intent.
4. If correlation is unavailable, an operator can independently identify the checkout through `/identify/`, then query provider status and verify the retained callback.
5. `/verify/` records who checked which evidence. `/process/` atomically records money and resolves the request. `FAILED` inbox items can be replayed after their cause is fixed; `REJECTED` items cannot.

The durable inbox provides explicit endpoint-driven recovery, not a scheduled worker. No automatic refund, resend, receipt fabrication, or reprocessing of historical logs is introduced.

## Production configuration

Set `DJANGO_ENV=production`, a non-default `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, a non-default Fernet `FIELD_ENCRYPTION_KEY`, an external HTTPS `PUBLIC_BASE_URL`, and `DB_ENGINE=postgres` plus database connection variables. Missing or unsafe required values stop startup. Production gateway credentials cannot be configured in development mode. Secure session/CSRF cookies are enabled in production.

Back up the encryption key separately from the database. This change does not implement key rotation or relax the existing requirement to retain the key used to encrypt stored credentials. It also does not install TLS, a production application server, an ingress, backups, or a scheduler.

## Migration and rollout

`0008_mpesa_recovery_and_verification` is **HIGH risk by policy** because it changes finance callback/request tables. PostgreSQL SQL was reviewed: additive columns, widened encrypted credential columns, nullable checkout ID, two unique constraints, an inbox index, and an operator foreign key. No destructive column removal or data rewrite/backfill is requested. Database defaults on new non-null columns preserve old INSERT compatibility during expansion. Ordinary index and constraint creation can block writes; this migration is not a claim of zero-downtime execution on large tables.

For an existing populated deployment, inspect table size and lock budget and split index/constraint creation into a separately reviewed concurrent migration when required. Apply the schema before deploying the new backend. At gateway cutover, route callback and initiation endpoints exclusively to the new version: old workers still post public callbacks directly and must not remain in the gateway request path. Initial logs stay unverified; do not bulk-approve history. Update STK clients to provide idempotency keys and provision the operator permissions before enabling workflows.

Existing ciphertext and pending checkout records are covered by a migration regression test. After new unknown requests or longer ciphertext exist, reversing to the old schema is unsafe (non-null checkout IDs, narrower encrypted columns, and lost recovery metadata). Prefer forward fixes. Restore only from a tested backup with payment reconciliation if a rollback is unavoidable.

Verification command, from `backend/` with a dedicated PostgreSQL test configuration:

```powershell
python manage.py test apps.finance.test_mpesa apps.finance.mpesa_api_tests apps.finance.test_mpesa_recovery apps.finance.test_mpesa_concurrency apps.finance.test_mpesa_migrations config.test_production_settings --noinput
python manage.py makemigrations --check --dry-run
python manage.py migrate --plan
python manage.py sqlmigrate finance 0008
```

These checks use local/mock provider responses. Live Safaricom credentials, provider registrations, delivery verification, and network behavior require deployment-specific sandbox acceptance testing before live payments.

Implementation verification: the full selected backend suite passed **176 tests on PostgreSQL 17.6**, including concurrency and migration regression tests, with no skips. Migration drift and whitespace checks passed. This local run used the installed psycopg2 2.9.10 driver; the deployment's declared psycopg driver still needs its normal CI validation.
