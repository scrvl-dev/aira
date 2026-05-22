"""
Generate synthetic MTR document sets for testing the agent pipeline.

Produces, in OUT_DIR:
  Property A (Newbridge)  — full 5 docs; survey + questionnaire are *scanned*
                            (image-only PDFs) to exercise vision OCR.
  Property B (Tallaght)   — 4 docs (no condition survey) to test incomplete batches.
  random_notes.pdf        — junk with no property identity → should be UNASSIGNED.

xlsx = digital (submission, works); valuation = text PDF; survey/questionnaire =
image-only PDFs (no embedded text).
"""
import io
import os

import fitz  # PyMuPDF
import openpyxl
from PIL import Image, ImageDraw, ImageFont

OUT_DIR = os.environ.get("OUT_DIR", "/tmp/aira_testdata")
os.makedirs(OUT_DIR, exist_ok=True)


def _font(size):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def text_to_scanned_pdf(lines: list[str]) -> bytes:
    """Render text lines onto a white image, embed as image-only PDF (no text layer)."""
    W, H = 1240, 1754  # ~A4 at 150dpi
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    title_f, body_f = _font(40), _font(26)
    y = 60
    for i, line in enumerate(lines):
        f = title_f if i == 0 else body_f
        d.text((70, y), line, fill=(20, 20, 20), font=f)
        y += 64 if i == 0 else 44
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    imgdoc = fitz.open(stream=buf.getvalue(), filetype="png")
    return imgdoc.convert_to_pdf()


def text_pdf(lines: list[str]) -> bytes:
    """A normal digital PDF with a real text layer."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_textbox(fitz.Rect(54, 54, 540, 780), "\n".join(lines), fontsize=11, fontname="helv")
    return doc.tobytes()


def submission_xlsx(d: dict) -> bytes:
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Submission Sheet"
    ws.append(["Question", "Answer"])
    rows = [
        ("Lender Name", d["lender"]), ("Borrower 1", d["borrower"]),
        ("Both Borrowers in MTR", d["both_mtr"]), ("Both Consented to Sale", "Yes"),
        ("Folio", d["folio"]), ("Property Address", d["address"]), ("Eircode", d["eircode"]),
        ("Property Type", d["ptype"]), ("Number of Bedrooms", d["beds"]),
        ("Total Occupants", d["occupants"]), ("Household Composition", d["household"]),
        ("Number of Dependants", d["dependants"]), ("Open Market Value", d["omv"]),
        ("Monthly Market Rent", d["rent"]),
    ]
    for q, a in rows:
        ws.append([q, a])
    buf = io.BytesIO(); wb.save(buf); return buf.getvalue()


def works_xlsx(address: str, items: list[str]) -> bytes:
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "List of Works"
    ws.append(["List of Works Identified By Conditional Survey"])
    ws.append(["Property Address", address])
    ws.append(["#", "Works Item"])
    for i, it in enumerate(items, 1):
        ws.append([i, it])
    buf = io.BytesIO(); wb.save(buf); return buf.getvalue()


def write(name, data):
    path = os.path.join(OUT_DIR, name)
    with open(path, "wb") as f:
        f.write(data)
    print(f"  {name:40s} {len(data):>8,} bytes")


# ── Property A — Newbridge (full set; survey + questionnaire scanned) ──
A = dict(lender="Pepper", borrower="Barry Kavanagh", both_mtr="No", folio="KE51682F",
         address="9 The View, Walshestown Park, Newbridge, Co. Kildare", eircode="W12 C966",
         ptype="Semi-detached", beds="4", occupants="2", household="2 Adults",
         dependants="0", omv="€405,000", rent="€2,650")
A_works = [
    "Fit adjustable vent covers to wall vents.", "Replace mechanical vents in bathroom and ensuite.",
    "Provide interlinked smoke alarms to hall, bedrooms and landing.", "Install attic access platform.",
    "Insulate and re-fix attic vent ducting.", "Plumbing contractor to test all installations.",
    "Fit carbon monoxide alarms to living room and landing.", "Clean chimney and camera-survey flue.",
    "Repair delaminating paint in ensuite ceiling.", "Fit new fire blanket to kitchen.",
    "Clean gutters and downpipes.", "Service rear French doors.",
    "Service gas boiler and upgrade heating controls.", "Electrical inspection and certification.",
    "Service all windows; provide keyless locks.", "Roof contractor to refit lifted tiles.",
]

# ── Property B — Tallaght (no survey; tests incomplete batch) ──
B = dict(lender="PTSB", borrower="Mary O'Brien", both_mtr="Yes", folio="DN98765",
         address="14 Oak Drive, Tallaght, Dublin 24", eircode="D24 AB12",
         ptype="Terraced", beds="3", occupants="3", household="2 Adults 1 Dependant",
         dependants="1", omv="€320,000", rent="€1,950")
B_works = ["Replace cracked window pane in kitchen.", "Service gas boiler.",
           "Provide smoke and CO alarms.", "Repaint hallway.", "Repair garden boundary fence."]

print(f"Writing synthetic test data to {OUT_DIR}\n")

# Property A
write("A_submission_sheet.xlsx", submission_xlsx(A))
write("A_list_of_works.xlsx", works_xlsx(A["address"], A_works))
write("A_valuation_report.pdf", text_pdf([
    "VALUATION REPORT", f"Applicant: {A['borrower']}", f"Address: {A['address']}",
    f"Eircode: {A['eircode']}", f"Folio: {A['folio']}", f"Property type: {A['ptype']} house",
    f"Bedrooms: {A['beds']}", "Market value (at present): €405,000",
    "Rebuilding cost: €310,000", "Monthly rental: €2,650", "Valuer: J. Murphy MIPAV",
    "Inspection date: 12 March 2026",
]))
# Scanned survey — note condition rating + a fire-safety party wall issue
write("A_condition_survey_report.pdf", text_to_scanned_pdf([
    "BUILDING CONDITION SURVEY", f"Prepared for: {A['borrower']}",
    f"Property: {A['address']}", f"Eircode: {A['eircode']}", f"Folio: {A['folio']}",
    f"Property type: {A['ptype']}", f"Bedrooms: {A['beds']}",
    "Condition rating (executive summary): Fair",
    "Condition rating (signed ranking page): Fair",
    "Fire safety: timber-framed party wall requires audit.",
    "Works items required: 16", "Surveyor: T. Walsh", "Inspection date: 10 March 2026",
]))
# Scanned questionnaire — slight household difference (1 dependant vs 0) → should flag
write("A_property_questionnaire.pdf", text_to_scanned_pdf([
    "MTR PROPERTY QUESTIONNAIRE", f"Applicant: {A['borrower']}",
    f"Address: {A['address']}", f"Eircode: {A['eircode']}",
    f"Bedrooms: {A['beds']}", "Total occupants: 3", "Adults: 2", "Dependents: 1",
    "Both borrowers in MTR: No", "Consented to sale: Yes",
    "Registered owner: Barry Kavanagh", "Signed date: 14 March 2026",
]))

# Property B (no survey)
write("B_submission_sheet.xlsx", submission_xlsx(B))
write("B_list_of_works.xlsx", works_xlsx(B["address"], B_works))
write("B_valuation_report.pdf", text_pdf([
    "VALUATION REPORT", f"Applicant: {B['borrower']}", f"Address: {B['address']}",
    f"Eircode: {B['eircode']}", f"Folio: {B['folio']}", f"Property type: {B['ptype']} house",
    f"Bedrooms: {B['beds']}", "Market value (at present): €320,000",
    "Rebuilding cost: €240,000", "Monthly rental: €1,950", "Valuer: A. Byrne MIPAV",
    "Inspection date: 18 March 2026",
]))
write("B_property_questionnaire.pdf", text_to_scanned_pdf([
    "MTR PROPERTY QUESTIONNAIRE", f"Applicant: {B['borrower']}",
    f"Address: {B['address']}", f"Eircode: {B['eircode']}",
    f"Bedrooms: {B['beds']}", "Total occupants: 3", "Adults: 2", "Dependents: 1",
    "Both borrowers in MTR: Yes", "Consented to sale: Yes",
]))

# Junk file — no property identity
write("random_notes.pdf", text_pdf([
    "MEETING NOTES", "Discuss Q2 targets and office move.",
    "Action: book the boardroom for Thursday.", "No property details here.",
]))

print("\nDone.")
