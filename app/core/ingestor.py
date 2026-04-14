"""
Ingestor: parses CAIQ XLSX into questions and customer PDFs into text.

Responsibilities:
  - load_caiq_questions(): reads the CAIQ Excel file and extracts every
    individual question row into a structured dict.
  - load_customer_docs(): reads all PDF files for a given customer and
    returns their combined text for downstream summarization + search.
"""

import re
from pathlib import Path
from typing import List, Dict

import openpyxl
import fitz  # PyMuPDF


# --- Constants ---

# Valid CAIQ question IDs follow the pattern: DOMAIN-##.## (e.g. IAM-01.1, A&A-03.2)
VALID_ID_PATTERN = re.compile(r"^[A-Z&]+-\d+\.\d+$")


# ---------------------------------------------------------------------------
# CAIQ XLSX Parser
# ---------------------------------------------------------------------------

def load_caiq_questions(xlsx_path: str) -> List[Dict]:
    """
    Parse the CAIQ XLSX file and return a list of question dicts.

    Reads the 'CAIQv4.0.3' sheet, skips header/footer rows using
    VALID_ID_PATTERN, and extracts the question ID, domain (derived
    from the ID prefix), and cleaned question text.

    Args:
        xlsx_path: absolute or relative path to the CAIQ .xlsx file.

    Returns:
        List of dicts, each with keys:
            question_id  — e.g. "IAM-01.1"
            domain       — e.g. "IAM"  (prefix before the first dash)
            question_text — cleaned question string
            source       — stem of the source file name
    """
    # Open workbook in read-only mode for memory efficiency
    wb = openpyxl.load_workbook(xlsx_path, read_only=True)
    ws = wb["CAIQv4.0.3"]

    questions = []
    for row in ws.iter_rows(values_only=True):
        question_id = row[0]
        question_text = row[1]

        # Skip blank rows and non-question rows (headers, footers, totals)
        if not question_id or not question_text:
            continue
        if not VALID_ID_PATTERN.match(str(question_id).strip()): #check by removing whitespace and converting to string in case of numeric IDs
            continue

        # Clean up whitespace and inline newlines from the cell value
        question_id = str(question_id).strip()
        domain = question_id.split("-")[0]          # e.g. "IAM" from "IAM-01.1"
        question_text = str(question_text).strip().replace("\n", " ")

        questions.append({
            "question_id": question_id,
            "domain": domain,
            "question_text": question_text,
            "source": Path(xlsx_path).stem,         # filename without extension
        })

    wb.close()
    return questions


# ---------------------------------------------------------------------------
# Customer PDF Parser
# ---------------------------------------------------------------------------

def load_customer_docs(customer_dir: str) -> str:
    """
    Parse all PDFs and XLSX files in a customer directory and return their combined text.

    Each PDF is opened with PyMuPDF; each XLSX is read with openpyxl (all
    non-empty cell values concatenated row by row). Results are concatenated
    with a filename header so downstream code can identify which doc each
    chunk came from.

    Args:
        customer_dir: path to folder containing the customer's PDF/XLSX files.

    Returns:
        Single string with all documents concatenated, separated by headers.

    Raises:
        FileNotFoundError: if no supported files are found in the directory.
    """
    customer_path = Path(customer_dir)

    pdf_files = sorted(customer_path.glob("*.pdf"))
    xlsx_files = sorted(customer_path.glob("*.xlsx"))
    all_files = pdf_files + xlsx_files

    if not all_files:
        raise FileNotFoundError(f"No PDF or XLSX files found in {customer_dir}")

    combined_text = []

    for pdf_file in pdf_files:
        doc = fitz.open(str(pdf_file))
        text = ""
        for page in doc:
            text += page.get_text()
        doc.close()
        combined_text.append(f"=== {pdf_file.name} ===\n{text.strip()}")

    for xlsx_file in xlsx_files:
        wb = openpyxl.load_workbook(xlsx_file, read_only=True, data_only=True)
        rows_text = []
        for sheet in wb.worksheets:
            for row in sheet.iter_rows(values_only=True):
                row_values = [str(cell).strip() for cell in row if cell is not None and str(cell).strip()]
                if row_values:
                    rows_text.append(" | ".join(row_values))
        wb.close()
        combined_text.append(f"=== {xlsx_file.name} ===\n" + "\n".join(rows_text))

    return "\n\n".join(combined_text)
