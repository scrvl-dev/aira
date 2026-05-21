import io

import openpyxl

from app.agents.parser import detect_doc_type, parse_excel, parse_batch


def test_detect_doc_type_from_filename():
    assert detect_doc_type("Submission_Sheet_12_Oak.xlsx") == "submission"
    assert detect_doc_type("IH_-_List_of_Works_12_Oak.xlsx") == "works"
    assert detect_doc_type("Condition_Survey_Report.pdf") == "survey"
    assert detect_doc_type("Property_Questionnaire.pdf") == "questionnaire"
    assert detect_doc_type("Valuation_12_Oak.pdf") == "valuation"
    assert detect_doc_type("random_file.txt") == "unknown"


def test_detect_doc_type_from_content():
    assert detect_doc_type("scan001.pdf", "Lender Name: Pepper") == "submission"
    assert detect_doc_type("scan002.pdf", "Building Condition Survey") == "survey"


def _xlsx_bytes(rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_parse_excel_renders_rows():
    data = _xlsx_bytes([["Lender Name", "Pepper"], ["Folio", "KE12345"]])
    text = parse_excel(data)
    assert "Lender Name" in text
    assert "KE12345" in text


def test_parse_batch_routes_by_filename():
    sub = _xlsx_bytes([["Lender Name", "Pepper"]])
    works = _xlsx_bytes([["Works Identified By Conditional Survey", "Repair roof"]])
    out = parse_batch({
        "Submission_Sheet.xlsx": sub,
        "List_of_Works.xlsx": works,
    })
    assert "submission" in out
    assert "works" in out
