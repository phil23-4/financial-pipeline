import pytesseract
from pdf2image import convert_from_path

def extract_text_via_ocr(file_path: str) -> str:
    """Transforms unparsed scanned image pages into string buffers via OCR processing loops."""
    pages = convert_from_path(file_path, dpi=150)
    return "\n".join([pytesseract.image_to_string(img) for img in pages])
