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

## STUDENT-GAP-02 — No student search/lookup filter

**Status:** Open  
**Impact:** `StudentListView` (`apps/students/api.py`) has no filter backend at all beyond the implicit campus scope in `list_students` (`apps/students/selectors.py`) — not `search`, not `id`/`id__in`. Two separate consequences:
1. `StudentsPage`'s own search box sends `?search=...`, which the backend silently ignores — the control renders and accepts input but never actually filters anything.
2. There is no bounded way to resolve an arbitrary student UUID to a name from the frontend (compounding `STUDENT-GAP-01`): fetching `page_size:100` and hoping the student is in the first page (as `finance.tsx`'s `InvoicesPage`/`PaymentsPage` briefly did, corrected in the Notifications/Documents/Reporting slice) is silently wrong for any tenant with more than 100 students. Every "select a student" dropdown across the app (`AssignmentsPage`, `PaymentsPage`'s record-payment dialog, `mpesa.tsx`'s STK dialog) shares this same ceiling today; a Reporting `student_id` parameter also can't offer a picker for the same reason.

Needs a real `search`/`id__in` filter on `StudentListView` before any of the above can be built as more than "works for the first 100 students."

