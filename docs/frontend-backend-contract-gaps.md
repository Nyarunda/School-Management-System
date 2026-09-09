# Frontend–Backend Contract Gaps

This ledger records backend representation or catalogue gaps that prevent a production frontend workflow. The frontend must not work around these gaps with raw UUID entry, synthetic display data, or per-row request fan-out.

## ACADEMIC-GAP-01 — Class-group catalogue

**Status:** Open  
**Impact:** The attendance API accepts a class-group UUID, but there is no authorised class-group list endpoint from which to populate the **Open Attendance Register** selector.

## ACADEMIC-GAP-02 — Attendance roster identity

**Status:** Open  
**Impact:** Attendance session records contain a student UUID without admission number or student name. Existing registers are technically editable, but teachers cannot safely identify learners.

The bounded attendance representation should include `student`, `student_admission_no`, and `student_name` on each roster row.

## ACADEMIC-GAP-03 — Assessment roster identity

**Status:** Open  
**Impact:** Assessment results contain a student UUID without admission number or student name. A dense marks register cannot safely identify candidates.

The bounded assessment representation should include `student`, `student_admission_no`, and `student_name` on each result row. The frontend must not issue one student request per result.

## ACADEMIC-GAP-04 — Assessment reference catalogues

**Status:** Open  
**Impact:** Opening an assessment requires term, class-group and subject UUIDs, but authorised catalogue endpoints are not exposed. Assessment creation therefore cannot offer safe selectors.

## STUDENT-GAP-01 — Student detail retrieve endpoint

**Status:** Open  
**Impact:** `apps/students/urls.py` exposes `StudentListView` and the document endpoints, but no `GET /students/<id>/` retrieve view. `StudentPage` (Student 360) therefore reads the row cached in `sessionStorage` by `StudentsPage`'s row click instead of fetching the record — a direct link, bookmark, or page refresh to `/students/<id>` renders an empty shell instead of the student's data. Compare `EmployeePage`, which has a real `GET /staff/employees/<id>/` to call. Do not paper over this with a second `sessionStorage`/cache trick; it needs an actual retrieve endpoint before Student 360 can be considered a complete workflow.

## STUDENT-GAP-02 — `/students/` exposes pagination only: no search, status filter, or tenant-admin campus filter

**Status:** Open  
**Impact:** `StudentListView.get_queryset` (`apps/students/api.py`) calls `list_students(tenant=membership.tenant, campus_id=membership.campus_id)` — `campus_id` here is the *caller's own membership*, never a request parameter (confirmed: no `request.query_params.get(...)` anywhere in the view). `list_students` (`apps/students/selectors.py`) itself supports no other filter. So today `/students/` genuinely offers exactly one capability: pagination. Concretely:
1. No `search`/`id`/`id__in` filter — `StudentsPage`'s former search box sent `?search=...`, which the backend silently ignored. Removed outright in the Student List Workspace slice rather than left disabled/misleading; a production ERP shouldn't show end users a control they can never operate.
2. No `status` filter on this endpoint (a different selector, `enrollment_register_rows`, supports `status=` for Reporting's enrollment register — that is a separate query path, not `list_students`).
3. No client-selectable campus filter — campus is derived exclusively from the caller's own membership (an authorization boundary), not a queryable dimension. A tenant-wide admin (`membership.campus_id is None`) sees every student tenant-wide with no way to narrow by campus at all. Don't build a "Campus" dropdown against this endpoint; there's nothing for it to send.
4. Compounding `STUDENT-GAP-01`: there is still no bounded way to resolve an arbitrary student UUID to a name from the frontend. Fetching `page_size:100` and hoping the student is in the first page (as `finance.tsx`'s `InvoicesPage`/`PaymentsPage` briefly did, corrected in the Notifications/Documents/Reporting slice) is silently wrong for any tenant with more than 100 students. Every "select a student" dropdown across the app (`AssignmentsPage`, `PaymentsPage`'s record-payment dialog, `mpesa.tsx`'s STK dialog) shares this same ceiling today; a Reporting `student_id` parameter also can't offer a picker for the same reason.

A school with thousands of students cannot reasonably operate a paginated-only directory forever, so this is a real capability gap — but it's a backend contract enhancement, and backend stays feature-frozen for RC Area 5. This belongs in the post-RC/go-live backlog unless explicitly classified as go-live-required; it is not something the frontend can or should work around with client-side filtering or an unbounded fetch.

