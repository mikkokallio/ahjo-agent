"""Test all downloaded PDFs for text extractability using pdfplumber."""
import os
import pdfplumber

pdf_dir = r"output\test-00400202624"
results = []

for filename in sorted(os.listdir(pdf_dir)):
    if not filename.lower().endswith(".pdf"):
        continue
    filepath = os.path.join(pdf_dir, filename)
    size_kb = os.path.getsize(filepath) / 1024
    try:
        with pdfplumber.open(filepath) as pdf:
            pages = len(pdf.pages)
            total_chars = 0
            sample_text = ""
            for page in pdf.pages:
                text = page.extract_text() or ""
                total_chars += len(text)
                if not sample_text and text.strip():
                    sample_text = text.strip()[:120]
            
            if total_chars > 100:
                status = "TEXT_OK"
            elif total_chars > 0:
                status = "SPARSE"
            else:
                status = "NO_TEXT"
            
            results.append({
                "file": filename,
                "size_kb": round(size_kb, 1),
                "pages": pages,
                "chars": total_chars,
                "status": status,
                "sample": sample_text[:80] if sample_text else ""
            })
    except Exception as e:
        results.append({
            "file": filename,
            "size_kb": round(size_kb, 1),
            "pages": 0,
            "chars": 0,
            "status": f"ERROR: {e}",
            "sample": ""
        })

# Print summary
print(f"\n{'='*80}")
print(f"PDF TEXT EXTRACTION TEST - {len(results)} files")
print(f"{'='*80}\n")

text_ok = [r for r in results if r["status"] == "TEXT_OK"]
sparse = [r for r in results if r["status"] == "SPARSE"]
no_text = [r for r in results if r["status"] == "NO_TEXT"]
errors = [r for r in results if r["status"].startswith("ERROR")]

print(f"TEXT_OK (readable):  {len(text_ok)}")
print(f"SPARSE (< 100 chars): {len(sparse)}")
print(f"NO_TEXT (needs OCR): {len(no_text)}")
print(f"ERROR:               {len(errors)}")
print(f"\n{'─'*80}")
print(f"{'File':<45} {'KB':>7} {'Pages':>5} {'Chars':>7} {'Status':<10}")
print(f"{'─'*80}")

for r in results:
    print(f"{r['file']:<45} {r['size_kb']:>7.1f} {r['pages']:>5} {r['chars']:>7} {r['status']:<10}")

# Show samples from a few readable ones
print(f"\n{'='*80}")
print("SAMPLE TEXT (first 80 chars from readable PDFs):")
print(f"{'='*80}")
for r in text_ok[:5]:
    print(f"\n  {r['file']}:")
    print(f"    \"{r['sample']}\"")

# Show details on problematic ones
if no_text or sparse:
    print(f"\n{'='*80}")
    print("PROBLEMATIC FILES (no/sparse text - may need OCR):")
    print(f"{'='*80}")
    for r in no_text + sparse:
        print(f"  {r['file']} ({r['size_kb']} KB, {r['pages']} pages, {r['chars']} chars)")
