"""PDF Processor - Azure Functions Flex Consumption (Python v2).

Offloads heavy PDF download/extract/summarize work from the agent container.
Two endpoints:
  POST /api/process - Process attachments (writes progress to blob)
  GET  /api/status/{job_id} - Check job progress and retrieve results
"""

import hashlib
import io
import json
import logging
import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed as _as_completed

import azure.functions as func
import httpx
import pdfplumber
from azure.ai.contentunderstanding import ContentUnderstandingClient
from azure.ai.contentunderstanding.models import AnalysisInput
from azure.identity import ManagedIdentityCredential, get_bearer_token_provider
from azure.storage.blob import BlobServiceClient
from bs4 import BeautifulSoup

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)

BASE_URL = "https://paatokset.hel.fi"
BLOB_ACCOUNT_URL = os.environ["PDF_CACHE_ACCOUNT_URL"]
BLOB_CONTAINER_CACHE = "pdf-cache"
BLOB_CONTAINER_JOBS = "jobs"
AI_ENDPOINT = os.environ["AZURE_AI_ENDPOINT"]
CONTENT_UNDERSTANDING_ENDPOINT = os.environ["AZURE_CONTENT_UNDERSTANDING_ENDPOINT"]

_credential = None
_blob_service = None


def _get_credential():
    global _credential
    if _credential is None:
        client_id = os.environ.get("AZURE_CLIENT_ID")
        logging.info(f"Initializing credential with client_id={client_id}")
        _credential = ManagedIdentityCredential(client_id=client_id)
    return _credential


def _get_blob_service():
    global _blob_service
    if _blob_service is None:
        _blob_service = BlobServiceClient(account_url=BLOB_ACCOUNT_URL, credential=_get_credential())
    return _blob_service


_cu_client = None


def _get_cu_client():
    """Get (or create) the Content Understanding client."""
    global _cu_client
    if _cu_client is None:
        _cu_client = ContentUnderstandingClient(
            endpoint=CONTENT_UNDERSTANDING_ENDPOINT,
            credential=_get_credential(),
        )
    return _cu_client


def _ocr_via_content_understanding(pdf_url: str, name: str) -> str | None:
    """Extract text from a PDF using Azure AI Content Understanding (OCR).
    Returns extracted text or None on failure."""
    try:
        client = _get_cu_client()
        poller = client.begin_analyze(
            analyzer_id="prebuilt-read",
            inputs=[AnalysisInput(url=pdf_url)],
        )
        result = poller.result()
        if result.contents and len(result.contents) > 0:
            text = result.contents[0].markdown or ""
            logging.info(f"Content Understanding OCR: {name} -> {len(text)} chars")
            return text.strip() if text.strip() else None
        return None
    except Exception as e:
        logging.error(f"Content Understanding OCR failed for {name}: {e}")
        return None


def _blob_write(container: str, blob_name: str, data: str):
    svc = _get_blob_service()
    container_client = svc.get_container_client(container)
    try:
        container_client.get_container_properties()
    except Exception:
        try:
            container_client.create_container()
        except Exception:
            pass
    blob_client = container_client.get_blob_client(blob_name)
    blob_client.upload_blob(data.encode("utf-8"), overwrite=True)


def _blob_read(container: str, blob_name: str) -> str | None:
    try:
        client = _get_blob_service().get_blob_client(container, blob_name)
        return client.download_blob().readall().decode("utf-8")
    except Exception:
        return None


def _pdf_cache_key(pdf_url: str) -> str:
    return hashlib.sha256(pdf_url.encode()).hexdigest()[:16]


MODEL_CONTEXT_TOKENS = 1_050_000
MODEL_MAX_INPUT_TOKENS = 922_000
MODEL_MAX_OUTPUT_TOKENS = 128_000
SUMMARY_CHUNK_TOKENS = 12_000
SUMMARY_COMPLETION_BUDGETS = (32_768, 65_536)


def _chunk_text(text: str, max_tokens: int = SUMMARY_CHUNK_TOKENS) -> list:
    words = text.split()
    words_per_chunk = int(max_tokens / 1.3)
    chunks = []
    for i in range(0, len(words), words_per_chunk):
        chunk = " ".join(words[i:i + words_per_chunk])
        if chunk.strip():
            chunks.append(chunk)
    return chunks if chunks else [text]


def _llm_summarize(text: str, doc_name: str, chunk_num: int, total_chunks: int, token_provider) -> str:
    import time as _time

    system_prompt = (
        "You are a document summarizer for Helsinki municipal decision-making materials. "
        "Summarize the following document section concisely and accurately. "
        "Preserve decision-relevant factual claims, numbers, dates, named entities, monetary amounts, and obligations. "
        "Note any internal contradictions or ambiguities. "
        "Write in the same language as the source material. "
        "Be concise, do not omit important facts, and end with a complete sentence."
    )
    user_prompt = f"Document: {doc_name} (section {chunk_num}/{total_chunks})\n\n{text}"

    deployment = os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-5.4")
    max_retries = 4

    for completion_budget in SUMMARY_COMPLETION_BUDGETS:
        for attempt in range(max_retries):
            token = token_provider()
            resp = httpx.post(
                f"{AI_ENDPOINT}/openai/deployments/{deployment}/chat/completions?api-version=2024-12-01-preview",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "max_completion_tokens": completion_budget,
                    "reasoning_effort": "minimal",
                },
                timeout=180,
            )
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("retry-after", 2 ** (attempt + 1)))
                logging.warning(f"Rate limited on {doc_name} chunk {chunk_num}, waiting {retry_after}s (attempt {attempt+1})")
                _time.sleep(retry_after)
                continue

            resp.raise_for_status()
            response_data = resp.json()
            choice = response_data["choices"][0]
            finish_reason = choice.get("finish_reason")
            usage = response_data.get("usage", {})
            completion_details = usage.get("completion_tokens_details", {})
            logging.info(
                "Summarized %s chunk %s/%s: finish_reason=%s completion_tokens=%s reasoning_tokens=%s budget=%s",
                doc_name,
                chunk_num,
                total_chunks,
                finish_reason,
                usage.get("completion_tokens"),
                completion_details.get("reasoning_tokens"),
                completion_budget,
            )

            if finish_reason == "length":
                logging.warning(
                    "Summary hit completion limit for %s chunk %s/%s at %s tokens; retrying with a larger budget",
                    doc_name,
                    chunk_num,
                    total_chunks,
                    completion_budget,
                )
                break
            if finish_reason != "stop":
                raise RuntimeError(f"Unexpected summary finish reason: {finish_reason}")

            content = choice.get("message", {}).get("content", "").strip()
            if not content:
                raise RuntimeError("Model returned an empty summary")
            return content
        else:
            raise RuntimeError(f"Rate limit retries exhausted for section {chunk_num}")

    raise RuntimeError(
        f"Summary remained incomplete after {SUMMARY_COMPLETION_BUDGETS[-1]} completion tokens for section {chunk_num}"
    )


def _update_job_status(job_id: str, status: str, progress: int, total: int, results: list | None = None):
    payload = {
        "status": status,
        "progress": progress,
        "total": total,
    }
    if results is not None:
        payload["results"] = results
    _blob_write(BLOB_CONTAINER_JOBS, f"{job_id}/status.json", json.dumps(payload, ensure_ascii=False))


def _process_single_pdf(att: dict, token_provider) -> dict:
    LARGE_THRESHOLD = 1500

    cache_key = _pdf_cache_key(att["pdf_url"])

    # Check cache
    cached = _blob_read(BLOB_CONTAINER_CACHE, cache_key)
    if cached:
        data = json.loads(cached)
        data["cached"] = True
        logging.info(f"Cache HIT: {att['name']} ({cache_key})")
        return data

    # Download
    try:
        with httpx.Client(timeout=30, follow_redirects=True) as client:
            pdf_resp = client.get(att["pdf_url"])
            pdf_resp.raise_for_status()
            pdf_bytes = pdf_resp.content
    except Exception as e:
        logging.error(f"Download failed: {att['name']}: {e}")
        return {"num": att["num"], "name": att["name"], "text": f"[Download failed: {e}]", "tokens": 0}

    # Very large files (>20MB) - skip pdfplumber (hangs), try Content Understanding OCR directly
    pdf_size_mb = len(pdf_bytes) / (1024 * 1024)
    full_text = None
    page_count = 0

    if pdf_size_mb > 20:
        logging.info(f"Large PDF ({pdf_size_mb:.1f}MB), using Content Understanding OCR: {att['name']}")
        del pdf_bytes  # Free memory before OCR call
        ocr_text = _ocr_via_content_understanding(att["pdf_url"], att["name"])
        if ocr_text:
            full_text = ocr_text
        else:
            result = {"num": att["num"], "name": att["name"], "text": f"[Document too large ({pdf_size_mb:.1f}MB) and OCR returned no text - likely image/map content]", "tokens": 0, "pages": 0, "category": "skipped"}
            try:
                _blob_write(BLOB_CONTAINER_CACHE, cache_key, json.dumps(result, ensure_ascii=False))
            except Exception:
                pass
            return result
    else:
        # Extract text with timeout protection for image-heavy PDFs
        try:
            def _extract_text(data: bytes) -> tuple[list[str], int]:
                parts = []
                with pdfplumber.open(io.BytesIO(data)) as pdf:
                    pages = len(pdf.pages)
                    for page in pdf.pages:
                        try:
                            page_text = page.extract_text()
                            if page_text:
                                parts.append(page_text)
                        except Exception:
                            pass
                return parts, pages

            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_extract_text, pdf_bytes)
                try:
                    text_parts, page_count = future.result(timeout=60)
                except FuturesTimeoutError:
                    logging.warning(f"PDF extraction timed out for {att['name']} ({pdf_size_mb:.1f}MB), trying Content Understanding OCR")
                    del pdf_bytes
                    ocr_text = _ocr_via_content_understanding(att["pdf_url"], att["name"])
                    if ocr_text:
                        full_text = ocr_text
                    else:
                        result = {"num": att["num"], "name": att["name"], "text": "[PDF extraction timed out and OCR returned no text]", "tokens": 0, "pages": 0, "category": "skipped"}
                        try:
                            _blob_write(BLOB_CONTAINER_CACHE, cache_key, json.dumps(result, ensure_ascii=False))
                        except Exception:
                            pass
                        return result
            if full_text is None:
                full_text = "\n\n".join(text_parts)
                del pdf_bytes
                if len(full_text.strip()) < 200:
                    # pdfplumber found no/minimal text - try Content Understanding OCR (handles scanned/image PDFs)
                    logging.info(f"pdfplumber minimal ({len(full_text.strip())} chars), trying Content Understanding OCR: {att['name']}")
                    ocr_text = _ocr_via_content_understanding(att["pdf_url"], att["name"])
                    if ocr_text and len(ocr_text.strip()) >= 200:
                        full_text = ocr_text
                    else:
                        result = {"num": att["num"], "name": att["name"], "text": "[Minimal text content - map/image document]", "tokens": 0, "pages": page_count, "category": "skipped"}
                        try:
                            _blob_write(BLOB_CONTAINER_CACHE, cache_key, json.dumps(result, ensure_ascii=False))
                        except Exception:
                            pass
                        return result
        except Exception as e:
            return {"num": att["num"], "name": att["name"], "text": f"[PDF extraction failed: {e}]", "tokens": 0, "pages": 0, "category": "error"}

    token_est = int(len(full_text.split()) * 1.3)

    # Summarize large docs
    if token_est > LARGE_THRESHOLD:
        chunks = _chunk_text(full_text)
        logging.info(f"Summarizing {att['name']}: {len(chunks)} chunks, ~{token_est} tokens")
        del full_text

        # Parallel chunk summarization (4 concurrent, with rate-limit retries in _llm_summarize)
        summaries = [None] * len(chunks)
        failed_chunks = []

        def summarize_chunk(args):
            idx, chunk = args
            return idx, _llm_summarize(chunk, att["name"], idx + 1, len(chunks), token_provider)

        with ThreadPoolExecutor(max_workers=4) as chunk_executor:
            futures = {chunk_executor.submit(summarize_chunk, (i, c)): i for i, c in enumerate(chunks)}
            for future in _as_completed(futures):
                try:
                    idx, result_text = future.result()
                    summaries[idx] = result_text
                except Exception as e:
                    idx = futures[future]
                    logging.error(f"Chunk {idx+1}/{len(chunks)} failed for {att['name']}: {e}")
                    failed_chunks.append(idx + 1)

        if failed_chunks:
            return {
                "num": att["num"],
                "name": att["name"],
                "text": f"[Summarization failed for sections: {', '.join(map(str, failed_chunks))}]",
                "tokens": token_est,
                "pages": page_count,
                "category": "error",
            }

        final_text = "\n\n".join(s for s in summaries if s)
        category = "summarized"
    else:
        final_text = full_text
        category = "full"

    # Cache immediately
    cache_entry = json.dumps({"num": att["num"], "name": att["name"], "text": final_text,
                              "tokens": token_est, "pages": page_count, "category": category}, ensure_ascii=False)
    try:
        _blob_write(BLOB_CONTAINER_CACHE, cache_key, cache_entry)
        logging.info(f"Cached: {att['name']} ({cache_key}, {len(cache_entry)} bytes)")
    except Exception as e:
        logging.error(f"Cache write failed: {att['name']}: {e}")

    return {"num": att["num"], "name": att["name"], "text": final_text,
            "tokens": token_est, "pages": page_count, "category": category}


@app.route(route="process", methods=["POST"])
def process(req: func.HttpRequest) -> func.HttpResponse:
    """Process PDF attachments. Writes progress to blob as each PDF completes."""

    try:
        body = req.get_json()
    except Exception:
        return func.HttpResponse(json.dumps({"error": "Invalid JSON body"}), status_code=400, mimetype="application/json")

    case_url = body.get("case_url", "")
    attachment_numbers = body.get("attachment_numbers", "all")
    job_id = body.get("job_id") or uuid.uuid4().hex[:12]

    if not case_url:
        return func.HttpResponse(json.dumps({"error": "case_url required"}), status_code=400, mimetype="application/json")

    # Write initial status BEFORE any processing so the job is always findable
    _update_job_status(job_id, "processing", 0, 0)

    try:
        return _do_process(job_id, case_url, attachment_numbers)
    except Exception as e:
        logging.exception(f"Unhandled error in process: {e}")
        _update_job_status(job_id, "failed", 0, 0, [{"error": str(e), "type": type(e).__name__}])
        return func.HttpResponse(json.dumps({"error": str(e), "type": type(e).__name__}), status_code=500, mimetype="application/json")


def _do_process(job_id: str, case_url: str, attachment_numbers: str) -> func.HttpResponse:
    # Parse the case page to find attachments
    full_case_url = case_url if case_url.startswith("http") else BASE_URL + case_url
    try:
        with httpx.Client(timeout=30, follow_redirects=True) as client:
            resp = client.get(full_case_url)
            resp.raise_for_status()
    except Exception as e:
        _update_job_status(job_id, "failed", 0, 0, [])
        return func.HttpResponse(json.dumps({"job_id": job_id, "error": f"Failed to load case page: {e}"}),
                                 status_code=500, mimetype="application/json")

    soup = BeautifulSoup(resp.text, "html.parser")
    all_attachments = []
    for h3 in soup.find_all("h3"):
        if "Liitteet" in h3.get_text():
            container = h3.find_next_sibling("div")
            if container:
                for att_div in container.find_all("div", class_="issue__attachment"):
                    link = att_div.find("a", href=True)
                    if link:
                        pdf_url = link.get("href", "")
                        name_text = link.get_text(strip=True)
                        num_match = re.match(r"^(\d+)\.\s*(.+)", name_text)
                        num = int(num_match.group(1)) if num_match else len(all_attachments) + 1
                        name = num_match.group(2) if num_match else name_text
                        all_attachments.append({"num": num, "name": name, "pdf_url": pdf_url, "published": True})
                    else:
                        full_text = att_div.get_text(strip=True)
                        full_text = full_text.replace("Liitett\u00e4 ei julkaista internetiss\u00e4.", "").strip()
                        num_match = re.match(r"^(\d+)\.\s*(.+)", full_text)
                        num = int(num_match.group(1)) if num_match else len(all_attachments) + 1
                        name = num_match.group(2) if num_match else full_text
                        all_attachments.append({"num": num, "name": name, "pdf_url": "", "published": False})
            break

    if not all_attachments:
        _update_job_status(job_id, "completed", 0, 0, [])
        return func.HttpResponse(json.dumps({"job_id": job_id, "status": "completed", "message": "No attachments found"}),
                                 mimetype="application/json")

    # Filter requested attachments
    if attachment_numbers.strip().lower() == "all":
        requested_nums = {a["num"] for a in all_attachments if a["published"]}
    else:
        requested_nums = {int(n.strip()) for n in attachment_numbers.split(",") if n.strip().isdigit()}

    to_process = [a for a in all_attachments if a["num"] in requested_nums and a["published"] and a["pdf_url"]]
    unpublished = [a for a in all_attachments if a["num"] in requested_nums and not a["published"]]

    total = len(to_process)
    _update_job_status(job_id, "processing", 0, total)

    # Process PDFs with bounded parallelism
    # Constraints:
    # - Flex Consumption: 2GB RAM, so max ~3 concurrent PDF extractions
    # - LLM: 450K TPM, ~4000 tokens/call => ~112 calls/min capacity
    #   At 3 concurrent workers, each waiting ~3s for LLM => ~60 calls/min (safe)
    # - 429 handling is in _llm_summarize with exponential backoff
    MAX_WORKERS = 3

    credential = _get_credential()
    token_provider = get_bearer_token_provider(credential, "https://cognitiveservices.azure.com/.default")

    results = []
    completed_count = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_att = {executor.submit(_process_single_pdf, att, token_provider): att for att in to_process}
        for future in _as_completed(future_to_att):
            try:
                result = future.result()
            except Exception as e:
                att = future_to_att[future]
                logging.error(f"Processing failed for {att['name']}: {e}")
                result = {"num": att["num"], "name": att["name"], "text": f"[Processing failed: {e}]", "tokens": 0, "pages": 0, "category": "error"}
            results.append(result)
            completed_count += 1
            _update_job_status(job_id, "processing", completed_count, total, results)
            logging.info(f"Job {job_id}: {completed_count}/{total} done - {result.get('name', '?')}")

    # Add unpublished info
    for att in unpublished:
        results.append({"num": att["num"], "name": att["name"], "text": "[Not published online]", "tokens": 0, "unpublished": True})

    # Sort by attachment number
    results.sort(key=lambda d: d["num"])

    # Final status
    _update_job_status(job_id, "completed", total, total, results)
    logging.info(f"Job {job_id}: completed ({total} attachments processed)")

    return func.HttpResponse(
        json.dumps({"job_id": job_id, "status": "completed", "total": total, "cached": sum(1 for r in results if r.get("cached"))}),
        mimetype="application/json"
    )


@app.route(route="status/{job_id}", methods=["GET"])
def status(req: func.HttpRequest) -> func.HttpResponse:
    """Check job status and return results if complete."""
    job_id = req.route_params.get("job_id", "")
    if not job_id:
        return func.HttpResponse(json.dumps({"error": "job_id required"}), status_code=400, mimetype="application/json")

    status_data = _blob_read(BLOB_CONTAINER_JOBS, f"{job_id}/status.json")
    if not status_data:
        return func.HttpResponse(json.dumps({"error": "Job not found", "job_id": job_id}), status_code=404, mimetype="application/json")

    return func.HttpResponse(status_data, mimetype="application/json")


@app.route(route="cache-delete", methods=["POST"])
def cache_delete(req: func.HttpRequest) -> func.HttpResponse:
    """Delete cached entries for specific PDF URLs so they can be reprocessed."""
    try:
        body = req.get_json()
    except Exception:
        return func.HttpResponse(json.dumps({"error": "Invalid JSON body"}), status_code=400, mimetype="application/json")

    pdf_urls = body.get("pdf_urls", [])
    if not pdf_urls:
        return func.HttpResponse(json.dumps({"error": "pdf_urls array required"}), status_code=400, mimetype="application/json")

    deleted = []
    for url in pdf_urls:
        cache_key = _pdf_cache_key(url)
        try:
            client = _get_blob_service().get_blob_client(BLOB_CONTAINER_CACHE, cache_key)
            client.delete_blob()
            deleted.append(cache_key)
        except Exception:
            pass

    return func.HttpResponse(json.dumps({"deleted": len(deleted), "keys": deleted}), mimetype="application/json")


@app.route(route="cache-check", methods=["POST"])
def cache_check(req: func.HttpRequest) -> func.HttpResponse:
    """Check which attachments for a case are already cached (instant) vs need processing."""

    try:
        body = req.get_json()
    except Exception:
        return func.HttpResponse(json.dumps({"error": "Invalid JSON body"}), status_code=400, mimetype="application/json")

    case_url = body.get("case_url", "")
    if not case_url:
        return func.HttpResponse(json.dumps({"error": "case_url required"}), status_code=400, mimetype="application/json")

    # Parse the case page to find attachments
    full_case_url = case_url if case_url.startswith("http") else BASE_URL + case_url
    try:
        with httpx.Client(timeout=30, follow_redirects=True) as client:
            resp = client.get(full_case_url)
            resp.raise_for_status()
    except Exception as e:
        return func.HttpResponse(json.dumps({"error": f"Failed to load case page: {e}"}),
                                 status_code=500, mimetype="application/json")

    soup = BeautifulSoup(resp.text, "html.parser")
    all_attachments = []
    for h3 in soup.find_all("h3"):
        if "Liitteet" in h3.get_text():
            container = h3.find_next_sibling("div")
            if container:
                for att_div in container.find_all("div", class_="issue__attachment"):
                    link = att_div.find("a", href=True)
                    if link:
                        pdf_url = link.get("href", "")
                        name_text = link.get_text(strip=True)
                        num_match = re.match(r"^(\d+)\.\s*(.+)", name_text)
                        num = int(num_match.group(1)) if num_match else len(all_attachments) + 1
                        name = num_match.group(2) if num_match else name_text
                        all_attachments.append({"num": num, "name": name, "pdf_url": pdf_url, "published": True})
                    else:
                        full_text = att_div.get_text(strip=True)
                        full_text = full_text.replace("Liitett\u00e4 ei julkaista internetiss\u00e4.", "").strip()
                        num_match = re.match(r"^(\d+)\.\s*(.+)", full_text)
                        num = int(num_match.group(1)) if num_match else len(all_attachments) + 1
                        name = num_match.group(2) if num_match else full_text
                        all_attachments.append({"num": num, "name": name, "pdf_url": "", "published": False})
            break

    # Check cache status for each published attachment
    cached_count = 0
    results = []
    for att in all_attachments:
        entry = {"num": att["num"], "name": att["name"], "published": att["published"]}
        if att["published"] and att["pdf_url"]:
            cache_key = _pdf_cache_key(att["pdf_url"])
            cached_data = _blob_read(BLOB_CONTAINER_CACHE, cache_key)
            if cached_data:
                cached_entry = json.loads(cached_data)
                entry["cached"] = True
                entry["tokens"] = cached_entry.get("tokens", 0)
                entry["pages"] = cached_entry.get("pages", 0)
                cached_count += 1
            else:
                entry["cached"] = False
        else:
            entry["cached"] = False
        results.append(entry)

    return func.HttpResponse(json.dumps({
        "total": len(all_attachments),
        "published": sum(1 for a in all_attachments if a["published"]),
        "cached": cached_count,
        "uncached": sum(1 for a in all_attachments if a["published"]) - cached_count,
        "attachments": results,
    }, ensure_ascii=False), mimetype="application/json")
