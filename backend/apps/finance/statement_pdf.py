"""Renders a student's fee statement (apps.finance.selectors.fee_statement_rows)
as a printable PDF -- a formatted document for a parent/guardian, not the raw
JSON the reports preview endpoint returns. Kept separate from services.py:
this is presentation over already-computed ledger data, not a write-service
or a business rule.
"""

import io

from xhtml2pdf import pisa

from .selectors import fee_statement_rows

_CURRENCY = "Ksh"


def _money(amount):
    return f"{_CURRENCY} {amount:,.2f}"


def _row_html(row):
    debit = _money(row["debit"]) if row["debit"] else "—"
    credit = _money(row["credit"]) if row["credit"] else "—"
    return f"""
        <tr>
            <td>{row["entry_date"].strftime("%d %b %Y")}</td>
            <td>{row["description"] or "—"}</td>
            <td class="amount">{debit}</td>
            <td class="amount">{credit}</td>
            <td class="amount balance">{_money(row["running_balance"])}</td>
        </tr>
    """


_TEMPLATE = """
<html>
<head>
<style>
    @page {{ size: A4; margin: 2cm; }}
    body {{ font-family: Helvetica, Arial, sans-serif; font-size: 10pt; color: #1a1a1a; }}
    .header {{ margin-bottom: 18px; }}
    .school {{ font-size: 16pt; font-weight: bold; color: #3730a3; }}
    .title {{ font-size: 13pt; margin-top: 4px; color: #444; }}
    .meta {{ margin: 16px 0 20px 0; }}
    .meta table {{ width: 100%; }}
    .meta td {{ padding: 2px 0; }}
    .meta .label {{ color: #666; width: 120px; }}
    table.ledger {{ width: 100%; border-collapse: collapse; }}
    table.ledger th {{ background: #eef0fb; color: #3730a3; text-align: left; padding: 6px 8px; border-bottom: 1px solid #c7cbe8; font-size: 9pt; text-transform: uppercase; }}
    table.ledger td {{ padding: 6px 8px; border-bottom: 1px solid #eee; }}
    table.ledger td.amount {{ text-align: right; }}
    table.ledger td.balance {{ font-weight: bold; }}
    .closing {{ margin-top: 16px; text-align: right; font-size: 12pt; }}
    .closing b {{ color: #3730a3; }}
    .empty {{ color: #888; padding: 12px 0; }}
    .footer {{ margin-top: 28px; font-size: 8pt; color: #999; }}
</style>
</head>
<body>
    <div class="header">
        <div class="school">{school_name}</div>
        <div class="title">Fee Statement</div>
    </div>
    <div class="meta">
        <table>
            <tr><td class="label">Student</td><td>{student_name}</td></tr>
            <tr><td class="label">Admission No.</td><td>{admission_number}</td></tr>
            <tr><td class="label">As of</td><td>{as_of}</td></tr>
        </table>
    </div>
    {body}
    <div class="footer">Generated {generated_at}</div>
</body>
</html>
"""


def send_fee_statement_email(*, tenant, student, as_of):
    """Emails the same PDF render_fee_statement_pdf produces to the
    student's primary guardian (falling back to any guardian on file with
    an email address). apps.guardians has no API surface anywhere else in
    this codebase -- this reads the model directly rather than standing up
    a general guardian lookup endpoint, since all that's needed here is one
    email address for one student.
    """
    import smtplib

    from django.core.exceptions import ValidationError
    from django.core.mail import EmailMessage

    from apps.guardians.models import StudentGuardian

    link = (
        StudentGuardian.objects.filter(tenant=tenant, student=student)
        .exclude(guardian__email="")
        .select_related("guardian")
        .order_by("-is_primary", "id")
        .first()
    )
    if link is None:
        raise ValidationError("No guardian email is on file for this student")

    pdf_bytes = render_fee_statement_pdf(tenant=tenant, student=student, as_of=as_of)
    subject = f"{tenant.name} — Fee Statement for {student.full_name}"
    body = (
        f"Dear {link.guardian.full_name},\n\n"
        f"Please find attached the fee statement for {student.full_name} "
        f"({student.admission_number}) as of {as_of.strftime('%d %b %Y')}.\n\n"
        f"{tenant.name}"
    )
    message = EmailMessage(subject=subject, body=body, to=[link.guardian.email])
    message.attach(f"fee-statement-{student.admission_number}-{as_of.isoformat()}.pdf", pdf_bytes, "application/pdf")
    try:
        message.send(fail_silently=False)
    except (smtplib.SMTPException, OSError) as error:
        raise ValidationError("The email could not be sent -- check the server's email configuration") from error
    return link.guardian.email


def render_fee_statement_pdf(*, tenant, student, as_of):
    rows = fee_statement_rows(tenant=tenant, student_id=str(student.id), as_of=as_of)
    if rows:
        body = f"""
            <table class="ledger">
                <thead><tr><th>Date</th><th>Description</th><th>Debit</th><th>Credit</th><th>Balance</th></tr></thead>
                <tbody>{"".join(_row_html(row) for row in rows)}</tbody>
            </table>
            <div class="closing">Closing balance: <b>{_money(rows[-1]["running_balance"])}</b></div>
        """
    else:
        body = '<div class="empty">No ledger activity has posted for this student as of this date.</div>'

    from django.utils import timezone

    html = _TEMPLATE.format(
        school_name=tenant.name,
        student_name=student.full_name,
        admission_number=student.admission_number,
        as_of=as_of.strftime("%d %b %Y"),
        body=body,
        generated_at=timezone.now().strftime("%d %b %Y, %H:%M"),
    )
    buffer = io.BytesIO()
    result = pisa.CreatePDF(io.StringIO(html), dest=buffer)
    if result.err:
        from django.core.exceptions import ValidationError

        raise ValidationError("Fee statement PDF could not be generated")
    return buffer.getvalue()
