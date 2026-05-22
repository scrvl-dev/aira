"""
OCR helpers for scanned / image-only PDFs.

pdfplumber only extracts *embedded* text, so scanned or photographed documents
(common for Condition Surveys and handwritten Property Questionnaires) come back
empty or garbled. This module detects those and renders their pages to images so
the extractor can read them with Claude's vision model.
"""
import base64
import io

try:
    import fitz  # PyMuPDF
    HAS_FITZ = True
except ImportError:
    HAS_FITZ = False

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


# A digital page typically yields hundreds of characters. Anything under this
# (averaged per page) is treated as scanned/image-only.
MIN_CHARS_PER_PAGE = 80


def pdf_is_scanned(file_bytes: bytes, extracted_text: str = "") -> bool:
    """Heuristic: a PDF is 'scanned' when it has almost no embedded text.

    Uses the already-extracted text when provided (cheap), falling back to a
    direct page count via PyMuPDF.
    """
    if not HAS_FITZ:
        # Without a renderer we can't OCR anyway; rely on text length alone.
        return len((extracted_text or "").strip()) < MIN_CHARS_PER_PAGE

    try:
        with fitz.open(stream=file_bytes, filetype="pdf") as doc:
            page_count = max(doc.page_count, 1)
    except Exception:
        return False

    return len((extracted_text or "").strip()) < MIN_CHARS_PER_PAGE * page_count


def render_pdf_to_images(file_bytes: bytes, max_pages: int = 15,
                         max_dim: int = 1600) -> list[bytes]:
    """Render PDF pages to PNG bytes, capped in count and pixel size.

    Capping pages and downscaling keeps vision token cost and latency bounded.
    """
    if not HAS_FITZ:
        raise ImportError("PyMuPDF (fitz) not installed — cannot render scanned PDF")

    images: list[bytes] = []
    with fitz.open(stream=file_bytes, filetype="pdf") as doc:
        for page in doc[:max_pages]:
            # Choose a zoom that targets ~max_dim on the long edge (no upscaling
            # beyond 3x, no downscaling below 1x — Pillow trims the rest).
            long_pts = max(page.rect.width, page.rect.height) or 1
            zoom = min(3.0, max(1.0, max_dim / long_pts))
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
            png = pix.tobytes("png")
            images.append(_downscale_png(png, max_dim))
    return images


def _downscale_png(png_bytes: bytes, max_dim: int) -> bytes:
    """Shrink a PNG so its long edge <= max_dim. No-op if PIL is missing."""
    if not HAS_PIL:
        return png_bytes
    img = Image.open(io.BytesIO(png_bytes))
    if max(img.size) <= max_dim:
        return png_bytes
    scale = max_dim / max(img.size)
    new_size = (max(1, int(img.width * scale)), max(1, int(img.height * scale)))
    img = img.convert("RGB").resize(new_size, Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()


def png_to_base64(png_bytes: bytes) -> str:
    return base64.standard_b64encode(png_bytes).decode("ascii")
