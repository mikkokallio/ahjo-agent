"""Test Helsinki City Strategy PDF for text extractability."""
import pdfplumber
import time

path = r"grounding\Kaupunkistrategia-2025-2029.pdf"
start = time.time()

with pdfplumber.open(path) as pdf:
    pages = len(pdf.pages)
    total_chars = 0
    sample_pages = {}
    for i, page in enumerate(pdf.pages):
        text = page.extract_text() or ""
        total_chars += len(text)
        if i < 3 or i == pages - 1:
            sample_pages[i + 1] = text[:300]

elapsed = time.time() - start
words = total_chars / 5.5  # rough Finnish word estimate

print(f"Pages: {pages}")
print(f"Total chars: {total_chars:,}")
print(f"Estimated words: {int(words):,}")
print(f"Estimated tokens (Finnish ~3.8 chars/token): {int(total_chars / 3.8):,}")
print(f"Extraction time: {elapsed:.1f}s")
status = "TEXT_OK" if total_chars > 1000 else "NEEDS_OCR"
print(f"Status: {status}")
print()

for pg, text in sample_pages.items():
    print(f"--- Page {pg} ---")
    print(text)
    print()
