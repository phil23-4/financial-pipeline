import pytesseract
from pdf2image import convert_from_path
from loguru import logger as log

def extract_text_via_ocr(file_path: str) -> str:
    """Transforms unparsed scanned image pages into string buffers via OCR processing loops.
    
    Args:
        file_path: Path to PDF file
        
    Returns:
        Concatenated OCR text from all pages, with empty strings for failed pages
    """
    pages = convert_from_path(file_path, dpi=150)
    texts = []
    for i, page in enumerate(pages):
        try:
            text = pytesseract.image_to_string(page)
            texts.append(text)
        except Exception as e:
            log.warning(f"OCR failed for page {i} in {file_path}: {e}")
            texts.append("")
    return "\n".join(texts)
