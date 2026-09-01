import pdfplumber
from pypdf import PdfReader
from fin_pipeline.utils.ocr_engine import extract_text_via_ocr

def parse_pdf_layout(file_path: str) -> dict:
    """Performs lightning-fast digital-native text extractions, with fallbacks to OCR if empty."""
    extracted_text = []
    extracted_tables = []
    table_cnt = 0

    reader = PdfReader(file_path)
    is_digital = any(page.extract_text().strip() for page in reader.pages[:2])

    if is_digital:
        with pdfplumber.open(file_path) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                text = page.extract_text()
                if text: extracted_text.append(text)
                
                for table in (page.extract_tables() or []):
                    if not table: continue
                    headers = [str(c).strip() for c in table if c]
                    rows = ["| " + " | ".join([str(cell).replace("\n", " ").strip() if cell else "" for cell in r]) + " |" for r in table]
                    rows.insert(1, "| " + " | ".join(["---"] * len(table)) + " |")
                    
                    extracted_tables.append({
                        "tableIndex": table_cnt,
                        "pageNumber": page_num,
                        "headers": headers,
                        "rowCount": len(table) - 1,
                        "markdown": "\n".join(rows)
                    })
                    table_cnt += 1
        return {"text": "\n".join(extracted_text), "tables": extracted_tables, "table_cnt": table_cnt, "reason": "Digital Native"}
    else:
        return {"text": extract_text_via_ocr(file_path), "tables": [], "table_cnt": 0, "reason": "OCR Scanner Fallback"}
