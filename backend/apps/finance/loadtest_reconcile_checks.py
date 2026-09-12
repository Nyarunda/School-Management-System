"""Pure comparison logic for the Milestone 5E reconciliation invariant --
the hard pass/fail gate for a load-test run. Deliberately has no Django ORM
or DB dependency: every function takes plain dicts/lists (either the
harness's own record of what it sent, or values the management command
extracted from the database), so this logic is unit-testable with synthetic
fixtures without needing a live run. See apps/finance/management/commands/
loadtest_reconcile.py for the thin wrapper that gathers real data and calls
these functions, and apps/finance/test_loadtest_reconcile.py for the tests
that prove each check actually detects the failure mode it claims to.

No check here assumes a student's ledger sums to zero -- a student with a
genuine outstanding balance has a non-zero, still-correct balance. Every
comparison is against a value the harness itself derived from what it did,
never against a hardcoded universal formula.
"""
from collections import defaultdict
from decimal import Decimal


class ReconciliationViolation:
    def __init__(self, check, detail):
        self.check = check
        self.detail = detail

    def __repr__(self):
        return f"ReconciliationViolation({self.check!r}, {self.detail!r})"

    def __eq__(self, other):
        return isinstance(other, ReconciliationViolation) and (self.check, self.detail) == (other.check, other.detail)

    def __hash__(self):
        return hash((self.check, tuple(sorted(self.detail.items()))))


def _dec(value):
    return Decimal(str(value))


def check_no_duplicate_or_missing_payments(*, events, incoming_payments):
    """events: [{"tenant", "trans_id", "expect_payment": bool}, ...] -- the
    harness's own record of what it sent. A duplicate-storm burst resends
    the *same* trans_id repeatedly, so grouping by (tenant, trans_id) is
    exactly right: it must resolve to exactly one IncomingPayment row,
    never zero (a payment silently lost) and never more than one
    (duplicate money), regardless of how many times that trans_id arrived.
    incoming_payments: [{"tenant", "external_transaction_id"}, ...]
    """
    violations = []
    by_key = defaultdict(list)
    for row in incoming_payments:
        by_key[(row["tenant"], row["external_transaction_id"])].append(row)
    expected_keys = {(event["tenant"], event["trans_id"]) for event in events if event.get("expect_payment", True)}
    for key in expected_keys:
        rows = by_key.get(key, [])
        if len(rows) == 0:
            violations.append(ReconciliationViolation("missing_payment", {"tenant": key[0], "trans_id": key[1]}))
        elif len(rows) > 1:
            violations.append(ReconciliationViolation("duplicate_payment", {"tenant": key[0], "trans_id": key[1], "count": len(rows)}))
    return violations


def check_payment_amounts_match(*, events, incoming_payments):
    """Every processed provider event's resulting IncomingPayment must carry
    the exact amount the harness sent -- not silently truncated/rounded.
    """
    violations = []
    expected_amount = {(event["tenant"], event["trans_id"]): _dec(event["amount"]) for event in events}
    for row in incoming_payments:
        key = (row["tenant"], row["external_transaction_id"])
        if key not in expected_amount:
            continue
        if _dec(row["amount"]) != expected_amount[key]:
            violations.append(ReconciliationViolation("amount_mismatch", {
                "tenant": key[0], "trans_id": key[1], "expected": str(expected_amount[key]), "actual": str(row["amount"]),
            }))
    return violations


def check_allocation_sums(*, payments):
    """payments: [{"tenant", "payment_id", "amount", "allocated_total", "unapplied"}, ...]
    Existing per-payment invariant (allocations + unapplied cash == the
    payment's own amount), re-checked at load-test scale.
    """
    violations = []
    for payment in payments:
        total = _dec(payment["allocated_total"]) + _dec(payment["unapplied"])
        if total != _dec(payment["amount"]):
            violations.append(ReconciliationViolation("allocation_sum_mismatch", {
                "tenant": payment["tenant"], "payment_id": payment["payment_id"],
                "expected": str(payment["amount"]), "actual": str(total),
            }))
    return violations


def check_student_balances(*, expected_balances, actual_balances):
    """expected_balances / actual_balances: [{"tenant", "student_id", "balance"}, ...]
    `expected_balances` is the harness's own derivation (invoiced total
    minus payments it caused to be allocated to that student);
    `actual_balances` comes from selectors.student_balance(). Deliberately
    never asserts either side is zero.
    """
    violations = []
    actual_by_key = {(row["tenant"], row["student_id"]): _dec(row["balance"]) for row in actual_balances}
    for row in expected_balances:
        key = (row["tenant"], row["student_id"])
        expected = _dec(row["balance"])
        actual = actual_by_key.get(key)
        if actual is None:
            violations.append(ReconciliationViolation("missing_student_balance", {"tenant": key[0], "student_id": key[1]}))
        elif actual != expected:
            violations.append(ReconciliationViolation("student_balance_mismatch", {
                "tenant": key[0], "student_id": key[1], "expected": str(expected), "actual": str(actual),
            }))
    return violations


def check_aggregate_receivable(*, per_student_balances, per_invoice_outstanding):
    """Independent cross-check: the sum of every student's balance should
    equal the sum of every invoice's outstanding balance, computed via a
    completely separate path (_bulk_outstanding_balances vs
    selectors.student_balance). per_invoice_outstanding:
    [{"tenant", "invoice_id", "outstanding"}, ...]
    """
    total_student = sum((_dec(row["balance"]) for row in per_student_balances), Decimal("0"))
    total_invoice = sum((_dec(row["outstanding"]) for row in per_invoice_outstanding), Decimal("0"))
    if total_student != total_invoice:
        return [ReconciliationViolation("aggregate_receivable_mismatch", {
            "per_student_total": str(total_student), "per_invoice_total": str(total_invoice),
        })]
    return []


def check_tenant_ownership(*, rows, expected_tenant_by_key):
    """rows: [{"tenant", "key": <hashable>}, ...] for Payment/IncomingPayment/
    StudentLedgerEntry rows the harness can trace back to a specific event.
    expected_tenant_by_key: {key: expected_tenant_slug}. A single misrouted
    row is a hard failure, not a warning.
    """
    violations = []
    for row in rows:
        expected_tenant = expected_tenant_by_key.get(row["key"])
        if expected_tenant is not None and row["tenant"] != expected_tenant:
            violations.append(ReconciliationViolation("cross_tenant_contamination", {
                "key": row["key"], "expected_tenant": expected_tenant, "actual_tenant": row["tenant"],
            }))
    return violations


def run_all_checks(*, events, incoming_payments, payments, expected_balances, actual_balances,
                   per_invoice_outstanding, ownership_rows, ownership_expected_tenant_by_key):
    violations = []
    violations += check_no_duplicate_or_missing_payments(events=events, incoming_payments=incoming_payments)
    violations += check_payment_amounts_match(events=events, incoming_payments=incoming_payments)
    violations += check_allocation_sums(payments=payments)
    violations += check_student_balances(expected_balances=expected_balances, actual_balances=actual_balances)
    violations += check_aggregate_receivable(per_student_balances=actual_balances, per_invoice_outstanding=per_invoice_outstanding)
    violations += check_tenant_ownership(rows=ownership_rows, expected_tenant_by_key=ownership_expected_tenant_by_key)
    return violations
