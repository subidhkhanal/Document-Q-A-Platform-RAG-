"""Generates the Sample Library for the public demo (demo/library) — a fictional
company's policy documents in every supported format. The same files form the
evaluation corpus (backend/evaluation) together with hand-written adversarial docs.

Dev-only dependencies:  pip install reportlab python-docx
Run:                    python demo/build_samples.py
"""

from pathlib import Path

import docx
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

OUT = Path(__file__).parent / "library"
STYLES = getSampleStyleSheet()
GRID = TableStyle([
    ("GRID", (0, 0), (-1, -1), 0.6, colors.black),
    ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
])


def p(text: str, style: str = "Normal") -> Paragraph:
    return Paragraph(text, STYLES[style])


def table(rows) -> Table:
    t = Table(rows)
    t.setStyle(GRID)
    return t


def handbook() -> None:
    doc = SimpleDocTemplate(str(OUT / "Northwind_Employee_Handbook.pdf"), pagesize=letter,
                            title="Northwind Labs Employee Handbook")
    doc.build([
        p("Northwind Labs Employee Handbook", "Title"),
        p("Working Hours", "Heading2"),
        p("Northwind Labs uses flexible hours with core collaboration hours from 10:00 to 16:00 local time, "
          "Monday to Friday. Meetings should be scheduled within core hours whenever possible."),
        p("Remote Work", "Heading2"),
        p("Employees may work remotely up to three days per week with their manager's approval. "
          "Fully remote arrangements require approval from a Vice President and are reviewed every six months."),
        PageBreak(),
        p("Time Off", "Heading1"),
        p("Full-time employees receive 22 days of paid time off (PTO) per calendar year, accrued at 1.83 days per month. "
          "Up to 5 unused PTO days carry over into the next year and expire if not used by March 31. "
          "Sick leave is separate from PTO: employees receive 10 paid sick days per year, which do not carry over."),
        Spacer(1, 10),
        p("Company Holidays 2026", "Heading2"),
        table([
            ["Holiday", "Date"],
            ["New Year's Day", "January 1"],
            ["Memorial Day", "May 25"],
            ["Independence Day", "July 4"],
            ["Labor Day", "September 7"],
            ["Thanksgiving", "November 26-27"],
            ["Winter Break", "December 24-31"],
        ]),
        PageBreak(),
        p("Code of Conduct", "Heading1"),
        p("Gifts or hospitality from vendors worth more than 75 USD must be reported to the Compliance team "
          "within 5 business days. Concerns about misconduct can be raised anonymously through the ethics hotline "
          "at 1-800-555-0199."),
        p("Performance Reviews", "Heading2"),
        p("Performance reviews take place twice a year, in April and October. Promotions are decided in the "
          "October cycle."),
    ])


def benefits() -> None:
    doc = SimpleDocTemplate(str(OUT / "Northwind_Benefits_Guide_2026.pdf"), pagesize=letter,
                            title="Northwind Labs Benefits Guide 2026")
    doc.build([
        p("Northwind Labs Benefits Guide 2026", "Title"),
        p("Parental Leave", "Heading2"),
        p("Primary caregivers receive 18 weeks of fully paid parental leave and secondary caregivers receive "
          "8 weeks. Leave must be taken within 12 months of the birth, adoption or placement."),
        p("Retirement", "Heading2"),
        p("The 401(k) plan matches 100% of employee contributions up to 5% of base salary. Employer contributions "
          "vest over three years at 33% per year."),
        p("Learning Budget", "Heading2"),
        p("Every employee has an annual learning budget of 1,200 USD for courses, conferences and books."),
        PageBreak(),
        p("Health Plans", "Heading1"),
        p("Employees choose one medical plan during open enrollment, which runs from November 3 to November 21."),
        Spacer(1, 10),
        table([
            ["Plan", "Monthly premium", "Deductible", "Out-of-pocket max"],
            ["Bronze HDHP", "$40", "$3,200", "$7,000"],
            ["Silver PPO", "$135", "$1,400", "$4,500"],
            ["Gold PPO", "$240", "$450", "$2,500"],
        ]),
        Spacer(1, 10),
        p("Dental coverage is provided by Delta Dental and includes two cleanings per year. Vision coverage includes "
          "a 150 USD allowance for frames every 24 months."),
    ])


def security_policy() -> None:
    d = docx.Document()
    d.add_heading("Northwind Labs Information Security Policy", level=1)
    d.add_heading("Device Security", level=2)
    d.add_paragraph("All laptops must use full-disk encryption and lock the screen after 5 minutes of inactivity. "
                    "Lost or stolen devices must be reported to IT immediately.")
    d.add_heading("Passwords and MFA", level=2)
    d.add_paragraph("Passwords must be at least 14 characters long and stored in the company password manager, "
                    "1Password. Multi-factor authentication is mandatory for email, VPN and GitHub.")
    d.add_heading("Data Classification", level=2)
    rows = [
        ("Level", "Examples", "Handling"),
        ("Public", "Marketing site, press releases", "No restrictions"),
        ("Internal", "Org charts, internal wikis", "Share only with employees"),
        ("Confidential", "Customer contracts, financials", "Need-to-know access, encrypted in transit"),
        ("Restricted", "Customer personal data, credentials", "Named-access only, never in email or chat"),
    ]
    t = d.add_table(rows=len(rows), cols=3)
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            t.cell(r, c).text = value
    d.add_heading("Incident Reporting", level=2)
    d.add_paragraph("Report suspected incidents to security@northwind.example or the #sec-incidents channel.")
    rows = [("Severity", "Report within"), ("Critical", "1 hour"), ("High", "4 hours"),
            ("Medium", "1 business day"), ("Low", "3 business days")]
    t = d.add_table(rows=len(rows), cols=2)
    for r, (a, b) in enumerate(rows):
        t.cell(r, 0).text, t.cell(r, 1).text = a, b
    d.add_heading("Phishing", level=2)
    d.add_paragraph("Report suspicious emails with the Report Phish button in the mail client. Never forward them.")
    d.save(OUT / "Northwind_Information_Security_Policy.docx")


TRAVEL = """# Northwind Labs Travel and Expense Policy

## Approval
Business travel must be approved by your manager at least 7 days before departure.

## Flights
Book economy class for flights under 6 hours. Premium economy is allowed for flights of 6 hours or longer.

## Per Diem and Hotels
| Item | Domestic | International |
|---|---|---|
| Daily meal per diem | 65 USD | 95 USD |

Hotel stays are capped at 275 USD per night in major cities and 180 USD per night elsewhere.
Book hotels through the corporate travel portal.

## Expenses
Use the corporate Amex card for travel costs. Receipts are required for every expense above 25 USD.
Personal car mileage is reimbursed at 0.67 USD per mile.
Submit expense reports within 30 days of returning from a trip.
"""

ONBOARDING = """# Northwind Labs Engineering Onboarding

## Day One
New engineers receive a MacBook Pro and access to the nw-platform monorepo. Complete security training in the first week.

## Code Review
Every pull request needs one approving review; the review SLA is one business day.
Production deploys require two approvals and a green CI run.

## Deploy Freeze
There are no production deploys on Fridays after 14:00 local time, or during the Winter Break.

## On-Call
The on-call rotation is weekly and hands over on Mondays at 10:00 UTC. Pages are routed through PagerDuty.
Engineers receive an on-call stipend of 400 USD per week on call. SEV1 incidents require acknowledgement within 5 minutes.
"""

RELEASE_NOTES = """Northwind Platform Release Notes - Version 3.2 (September 2026)

New features
- NW-4502: Single sign-on with Okta now supports SCIM user provisioning.
- NW-4488: Dashboards can be exported to PDF.
- The Enterprise plan API rate limit is raised to 600 requests per minute (was 300).

Fixes
- NW-4471: CSV export no longer truncates reports with more than 65,535 rows.
- NW-4493: Fixed timezone drift in scheduled reports for users in UTC+5:30.

Deprecations
- NW-4519: API v1 endpoints are deprecated and will be removed on 2027-03-01. Migrate to API v2.

Platform support
- The minimum supported Android version is now Android 11.
"""


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    handbook()
    benefits()
    security_policy()
    (OUT / "Northwind_Travel_and_Expense_Policy.md").write_text(TRAVEL, encoding="utf-8")
    (OUT / "Northwind_Engineering_Onboarding.md").write_text(ONBOARDING, encoding="utf-8")
    (OUT / "Northwind_Release_Notes_v3.2.txt").write_text(RELEASE_NOTES, encoding="utf-8")
    print("\n".join(sorted(f.name for f in OUT.iterdir())))


if __name__ == "__main__":
    main()
