import fitz  # PyMuPDF
import pytesseract
from pdf2image import convert_from_path
from PIL import Image
from concurrent.futures import ThreadPoolExecutor, as_completed
from loguru import logger as log


def _process_page(file_path: str, page_num: int) -> tuple[int, str]:
    """Renders a single page using PyMuPDF and extracts text via Tesseract."""
    try:
        # Open a thread-safe document handle or load page independently
        with fitz.open(file_path) as doc:
            page = doc.load_page(page_num)
            # Render page to pixmap at 200 DPI for optimal OCR balance of speed and clarity
            pix = page.get_pixmap(dpi=200)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

            # Convert to grayscale to improve Tesseract text recognition accuracy
            gray_img = img.convert("L")

            text = pytesseract.image_to_string(gray_img)
            return page_num, text
    except Exception as e:
        log.warning(f"OCR failed for page {page_num + 1} in {file_path}: {e}")
        return page_num, ""


def extract_text_via_ocr(file_path: str, max_workers: int = 4) -> str:
    """High-performance concurrent OCR pipeline using PyMuPDF rendering and Tesseract. Transforms unparsed scanned image pages into string buffers via OCR processing loops.

    Args:
        file_path: Path to PDF file
        max_workers: Maximum number of concurrent OCR processes

    Returns:
        Concatenated OCR text from all pages, with empty strings for failed pages
    """
    try:
        # Get total page count first without loading images into RAM
        with fitz.open(file_path) as doc:
            total_pages = len(doc)

        texts = [""] * total_pages

        # Process pages concurrently using threads to drastically cut down execution time
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(_process_page, file_path, i): i
                for i in range(total_pages)
            }

            for future in as_completed(futures):
                page_num, text = future.result()
                texts[page_num] = text

        return "\n".join(texts)

    except Exception as e:
        log.warning(f"OCR failed for page {i} in {file_path}: {e}")
        return ""
