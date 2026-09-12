from django.test import SimpleTestCase

from .loadtest_reconcile_checks import (
    ReconciliationViolation,
    check_aggregate_receivable,
    check_allocation_sums,
    check_no_duplicate_or_missing_payments,
    check_payment_amounts_match,
    check_student_balances,
    check_tenant_ownership,
    run_all_checks,
)


class NoDuplicateOrMissingPaymentsTests(SimpleTestCase):
    def test_happy_path_one_event_one_incoming_payment(self):
        events = [{"tenant": "t1", "trans_id": "A1", "amount": "500.00"}]
        incoming_payments = [{"tenant": "t1", "external_transaction_id": "A1", "amount": "500.00"}]
        self.assertEqual(check_no_duplicate_or_missing_payments(events=events, incoming_payments=incoming_payments), [])

    def test_detects_a_genuinely_missing_payment(self):
        events = [{"tenant": "t1", "trans_id": "A1", "amount": "500.00"}]
        incoming_payments = []
        violations = check_no_duplicate_or_missing_payments(events=events, incoming_payments=incoming_payments)
        self.assertEqual(violations, [ReconciliationViolation("missing_payment", {"tenant": "t1", "trans_id": "A1"})])

    def test_detects_a_genuine_duplicate(self):
        events = [{"tenant": "t1", "trans_id": "A1", "amount": "500.00"}]
        incoming_payments = [
            {"tenant": "t1", "external_transaction_id": "A1", "amount": "500.00"},
            {"tenant": "t1", "external_transaction_id": "A1", "amount": "500.00"},
        ]
        violations = check_no_duplicate_or_missing_payments(events=events, incoming_payments=incoming_payments)
        self.assertEqual(violations, [ReconciliationViolation("duplicate_payment", {"tenant": "t1", "trans_id": "A1", "count": 2})])

    def test_duplicate_storm_of_repeated_trans_id_resolves_to_exactly_one(self):
        # A duplicate-storm burst resends the SAME trans_id many times --
        # this must not be flagged as "duplicate" once, and only once.
        events = [{"tenant": "t1", "trans_id": "STORM-1", "amount": "500.00"} for _ in range(20)]
        incoming_payments = [{"tenant": "t1", "external_transaction_id": "STORM-1", "amount": "500.00"}]
        self.assertEqual(check_no_duplicate_or_missing_payments(events=events, incoming_payments=incoming_payments), [])

    def test_ignores_events_not_expected_to_produce_a_payment(self):
        events = [{"tenant": "t1", "trans_id": "A1", "amount": "500.00", "expect_payment": False}]
        self.assertEqual(check_no_duplicate_or_missing_payments(events=events, incoming_payments=[]), [])


class PaymentAmountsMatchTests(SimpleTestCase):
    def test_matching_amount_passes(self):
        events = [{"tenant": "t1", "trans_id": "A1", "amount": "500.00"}]
        incoming_payments = [{"tenant": "t1", "external_transaction_id": "A1", "amount": "500.00"}]
        self.assertEqual(check_payment_amounts_match(events=events, incoming_payments=incoming_payments), [])

    def test_detects_an_amount_mismatch(self):
        events = [{"tenant": "t1", "trans_id": "A1", "amount": "500.00"}]
        incoming_payments = [{"tenant": "t1", "external_transaction_id": "A1", "amount": "499.00"}]
        violations = check_payment_amounts_match(events=events, incoming_payments=incoming_payments)
        self.assertEqual(violations, [ReconciliationViolation("amount_mismatch", {
            "tenant": "t1", "trans_id": "A1", "expected": "500.00", "actual": "499.00",
        })])


class AllocationSumTests(SimpleTestCase):
    def test_allocated_plus_unapplied_equals_amount_passes(self):
        payments = [{"tenant": "t1", "payment_id": "p1", "amount": "500.00", "allocated_total": "300.00", "unapplied": "200.00"}]
        self.assertEqual(check_allocation_sums(payments=payments), [])

    def test_detects_an_allocation_sum_mismatch(self):
        payments = [{"tenant": "t1", "payment_id": "p1", "amount": "500.00", "allocated_total": "300.00", "unapplied": "0.00"}]
        violations = check_allocation_sums(payments=payments)
        self.assertEqual(violations, [ReconciliationViolation("allocation_sum_mismatch", {
            "tenant": "t1", "payment_id": "p1", "expected": "500.00", "actual": "300.00",
        })])


class StudentBalanceTests(SimpleTestCase):
    def test_matching_non_zero_balance_passes(self):
        # A student with a genuine outstanding balance has a non-zero,
        # still-correct balance -- this must not be flagged.
        expected = [{"tenant": "t1", "student_id": "s1", "balance": "20000.00"}]
        actual = [{"tenant": "t1", "student_id": "s1", "balance": "20000.00"}]
        self.assertEqual(check_student_balances(expected_balances=expected, actual_balances=actual), [])

    def test_detects_an_outstanding_balance_mismatch_on_a_non_zero_balance(self):
        expected = [{"tenant": "t1", "student_id": "s1", "balance": "20000.00"}]
        actual = [{"tenant": "t1", "student_id": "s1", "balance": "15000.00"}]
        violations = check_student_balances(expected_balances=expected, actual_balances=actual)
        self.assertEqual(violations, [ReconciliationViolation("student_balance_mismatch", {
            "tenant": "t1", "student_id": "s1", "expected": "20000.00", "actual": "15000.00",
        })])

    def test_zero_balance_is_a_valid_expected_value_not_a_default(self):
        expected = [{"tenant": "t1", "student_id": "s1", "balance": "0.00"}]
        actual = [{"tenant": "t1", "student_id": "s1", "balance": "0.00"}]
        self.assertEqual(check_student_balances(expected_balances=expected, actual_balances=actual), [])


class AggregateReceivableTests(SimpleTestCase):
    def test_matching_totals_pass(self):
        per_student = [{"tenant": "t1", "student_id": "s1", "balance": "20000.00"}]
        per_invoice = [{"tenant": "t1", "invoice_id": "i1", "outstanding": "20000.00"}]
        self.assertEqual(check_aggregate_receivable(per_student_balances=per_student, per_invoice_outstanding=per_invoice), [])

    def test_detects_a_mismatch_between_the_two_independent_totals(self):
        per_student = [{"tenant": "t1", "student_id": "s1", "balance": "20000.00"}]
        per_invoice = [{"tenant": "t1", "invoice_id": "i1", "outstanding": "18000.00"}]
        violations = check_aggregate_receivable(per_student_balances=per_student, per_invoice_outstanding=per_invoice)
        self.assertEqual(violations, [ReconciliationViolation("aggregate_receivable_mismatch", {
            "per_student_total": "20000.00", "per_invoice_total": "18000.00",
        })])


class TenantOwnershipTests(SimpleTestCase):
    def test_matching_tenant_passes(self):
        rows = [{"tenant": "t1", "key": "A1"}]
        self.assertEqual(check_tenant_ownership(rows=rows, expected_tenant_by_key={"A1": "t1"}), [])

    def test_detects_a_cross_tenant_misrouted_row(self):
        rows = [{"tenant": "t2", "key": "A1"}]
        violations = check_tenant_ownership(rows=rows, expected_tenant_by_key={"A1": "t1"})
        self.assertEqual(violations, [ReconciliationViolation("cross_tenant_contamination", {
            "key": "A1", "expected_tenant": "t1", "actual_tenant": "t2",
        })])


class RunAllChecksTests(SimpleTestCase):
    def test_a_fully_consistent_run_produces_no_violations(self):
        violations = run_all_checks(
            events=[{"tenant": "t1", "trans_id": "A1", "amount": "500.00"}],
            incoming_payments=[{"tenant": "t1", "external_transaction_id": "A1", "amount": "500.00"}],
            payments=[{"tenant": "t1", "payment_id": "p1", "amount": "500.00", "allocated_total": "500.00", "unapplied": "0.00"}],
            expected_balances=[{"tenant": "t1", "student_id": "s1", "balance": "9500.00"}],
            actual_balances=[{"tenant": "t1", "student_id": "s1", "balance": "9500.00"}],
            per_invoice_outstanding=[{"tenant": "t1", "invoice_id": "i1", "outstanding": "9500.00"}],
            ownership_rows=[{"tenant": "t1", "key": "A1"}],
            ownership_expected_tenant_by_key={"A1": "t1"},
        )
        self.assertEqual(violations, [])

    def test_a_single_bad_check_still_surfaces_among_the_rest(self):
        violations = run_all_checks(
            events=[{"tenant": "t1", "trans_id": "A1", "amount": "500.00"}],
            incoming_payments=[{"tenant": "t1", "external_transaction_id": "A1", "amount": "500.00"}],
            payments=[{"tenant": "t1", "payment_id": "p1", "amount": "500.00", "allocated_total": "500.00", "unapplied": "0.00"}],
            expected_balances=[{"tenant": "t1", "student_id": "s1", "balance": "9500.00"}],
            actual_balances=[{"tenant": "t1", "student_id": "s1", "balance": "9500.00"}],
            per_invoice_outstanding=[{"tenant": "t1", "invoice_id": "i1", "outstanding": "9500.00"}],
            ownership_rows=[{"tenant": "t2", "key": "A1"}],
            ownership_expected_tenant_by_key={"A1": "t1"},
        )
        self.assertEqual(violations, [ReconciliationViolation("cross_tenant_contamination", {
            "key": "A1", "expected_tenant": "t1", "actual_tenant": "t2",
        })])
