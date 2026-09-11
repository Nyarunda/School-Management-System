# Frontend–Backend Contract Gaps

This ledger records backend representation or catalogue gaps that prevent a production frontend workflow. The frontend must not work around these gaps with raw UUID entry, synthetic display data, or per-row request fan-out.

## ACADEMIC-GAP-01 — Class-group catalogue

**Status:** Closed — `GET /academics/class-groups/` (read-only), gated by `attendance.session.manage` + the `attendance` module flag, filtered to campus scope and `TeacherAssignment`/`attendance.any_class` exactly as `open_attendance_session` already authorizes. Wired into Attendance's **Open register** dialog. No new write surface, no CRUD, no change to `ACADEMIC-GAP-02` or to opening rules.

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

## STUDENT-GAP-02 — `/students/` search exists but is only wired into some student pickers; no status or tenant-admin campus filter

**Status:** Partially closed  
**Impact:** `StudentListView.get_queryset` (`apps/students/api.py`) calls `list_students(tenant=membership.tenant, campus_id=membership.campus_id, search=request.query_params.get("search"))` — `campus_id` is still always the *caller's own membership*, never a request parameter. `list_students` (`apps/students/selectors.py`) does support `search`, matched against `admission_number`/`first_name`/`last_name` via `icontains`. This closes the original "no bounded way to resolve a student id to a name" gap for any picker that's actually wired to it:
- `finance.tsx`'s `StudentSearchSelect` (a live, debounced `?search=` lookup resolved to the real id) closes this for `IncomingPage`'s Match dialog, `AssignmentsPage`'s Assign-fees dialog and its own student filter, `InvoicesPage`'s student filter, and `PaymentsPage`'s student filter and Record-payment dialog — the last two previously used a raw exact-id `TextInput`/a `page_size:100` dropdown respectively, both closed in the same pass after a user hit "No matching record for the given identifier" pasting an admission number into the Assign-fees dialog's old raw-id field.
- `mpesa.tsx`'s STK dialog still takes a raw student id — not yet migrated to `StudentSearchSelect`.
- A Reporting `student_id` parameter also still can't offer a picker.

Remaining gaps, still open:
1. No `status` filter on this endpoint (a different selector, `enrollment_register_rows`, supports `status=` for Reporting's enrollment register — that is a separate query path, not `list_students`).
2. No client-selectable campus filter — campus is derived exclusively from the caller's own membership (an authorization boundary), not a queryable dimension. A tenant-wide admin (`membership.campus_id is None`) sees every student tenant-wide with no way to narrow by campus at all. Don't build a "Campus" dropdown against this endpoint; there's nothing for it to send.

Compounded by `STUDENT-GAP-01` still being open: `StudentSearchSelect` resolves a search result to an id fine, but there's still no `GET /students/<id>/` to resolve an arbitrary id *back* to a name outside of a live search (e.g. rendering a bare student id already stored on a row, like the Payments/Invoices table's "Student" column).

