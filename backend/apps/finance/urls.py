from django.urls import path

from .api import (
    AllocationReversalView,
    AssignmentListCreateView,
    CreditNoteListCreateView,
    FeeCategoryListCreateView,
    FeeItemListCreateView,
    FeeStructureApproveView,
    FeeStructureLineCreateView,
    FeeStructureListCreateView,
    FinanceSetupView,
    InvoiceGenerateView,
    InvoiceIssueView,
    InvoiceListView,
    PaymentAllocateView,
    PaymentListCreateView,
    StudentFinanceView,
    StudentLedgerListView,
)


urlpatterns = [
    path("setup/", FinanceSetupView.as_view(), name="finance-setup"),
    path("fee-categories/", FeeCategoryListCreateView.as_view(), name="fee-category-list"),
    path("fee-items/", FeeItemListCreateView.as_view(), name="fee-item-list"),
    path("fee-structures/", FeeStructureListCreateView.as_view(), name="fee-structure-list"),
    path("fee-structures/<uuid:structure_id>/lines/", FeeStructureLineCreateView.as_view(), name="fee-structure-line-create"),
    path("fee-structures/<uuid:structure_id>/approve/", FeeStructureApproveView.as_view(), name="fee-structure-approve"),
    path("student-fee-assignments/", AssignmentListCreateView.as_view(), name="student-fee-assignment-list"),
    path("student-fee-assignments/<uuid:assignment_id>/generate-invoice/", InvoiceGenerateView.as_view(), name="invoice-generate"),
    path("invoices/", InvoiceListView.as_view(), name="invoice-list"),
    path("invoices/<uuid:invoice_id>/issue/", InvoiceIssueView.as_view(), name="invoice-issue"),
    path("credit-notes/", CreditNoteListCreateView.as_view(), name="credit-note-list"),
    path("payments/", PaymentListCreateView.as_view(), name="payment-list"),
    path("payments/<uuid:payment_id>/allocate/", PaymentAllocateView.as_view(), name="payment-allocate"),
    path("payment-allocations/<uuid:allocation_id>/reverse/", AllocationReversalView.as_view(), name="payment-allocation-reverse"),
    path("ledger-entries/", StudentLedgerListView.as_view(), name="ledger-entry-list"),
    path("students/<uuid:student_id>/finance/", StudentFinanceView.as_view(), name="student-finance"),
]