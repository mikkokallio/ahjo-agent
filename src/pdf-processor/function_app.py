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
from azure.identity import ManagedIdentityCredential, get_bearer_token_provider
from azure.storage.blob import BlobServiceClient
from bs4 import BeautifulSoup

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)

BASE_URL = "https://paatokset.hel.fi"
BLOB_ACCOUNT_URL = "https://stpdfprocahjo.blob.core.windows.net"
BLOB_CONTAINER_CACHE = "pdf-cache"
BLOB_CONTAINER_JOBS = "jobs"
AI_ENDPOINT = "https://ai-account-mapx4sxw6thvg.services.ai.azure.com"

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


def _chunk_text(text: str, max_tokens: int = 3000) -> list:
    words = text.split()
    words_per_chunk = int(max_tokens / 1.3)
    chunks = []
    for i in range(0, len(words), words_per_chunk):
        chunk = " ".join(words[i:i + words_per_chunk])
        if chunk.strip():
            chunks.append(chunk)
    return chunks if chunks else [text]


# Rate limit config: 450K TPM = ~7500 tokens/sec
# Each summarization call uses ~4000 tokens (3000 in + 1000 out)
# Safe throughput: ~1.8 calls/sec, or ~0.55s between calls
TPM_LIMIT = 450_000
TOKENS_PER_CALL = 4000  # ~3000 input + ~1000 output
MAX_CALLS_PER_MINUTE = TPM_LIMIT // TOKENS_PER_CALL  # ~112
MIN_DELAY_BETWEEN_CALLS = 60.0 / MAX_CALLS_PER_MINUTE  # ~0.53s


def _llm_summarize(text: str, doc_name: str, chunk_num: int, total_chunks: int, token_provider) -> str:
    import time as _time

    system_prompt = (
        "You are a document summarizer for Helsinki municipal decision-making materials. "
        "Summarize the following document section concisely and accurately. "
        "Preserve ALL factual claims, numbers, dates, named entities, monetary amounts, and obligations. "
        "Note any internal contradictions or ambiguities. "
        "Write in the same language as the source material. "
        "Be concise but do not omit important facts."
    )
    user_prompt = f"Document: {doc_name} (section {chunk_num}/{total_chunks})\n\n{text}"

    deployment = os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-5.4")
    max_retries = 4

    for attempt in range(max_retries):
        try:
            token = token_provider()
            resp = httpx.post(
                f"{AI_ENDPOINT}/openai/deployments/{deployment}/chat/completions?api-version=2024-12-01-preview",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "max_completion_tokens": 1000,
                    "temperature": 0.2,
                },
                timeout=90,
            )
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("retry-after", 2 ** (attempt + 1)))
                logging.warning(f"Rate limited on {doc_name} chunk {chunk_num}, waiting {retry_after}s (attempt {attempt+1})")
                _time.sleep(retry_after)
                continue
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except Exception as e:
            if attempt < max_retries - 1 and "429" in str(e):
                wait = 2 ** (attempt + 1)
                logging.warning(f"Rate limit error on {doc_name} chunk {chunk_num}, retrying in {wait}s")
                _time.sleep(wait)
                continue
            logging.error(f"LLM summarization failed for {doc_name} section {chunk_num}: {e}")
            return f"[Summarization failed for section {chunk_num}: {e}]"

    return f"[Summarization failed for section {chunk_num}: max retries exceeded]"


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

    # Skip very large files (>20MB) - likely image-heavy maps that will hang pdfplumber
    pdf_size_mb = len(pdf_bytes) / (1024 * 1024)
    if pdf_size_mb > 20:
        logging.warning(f"Skipping extraction for {att['name']}: {pdf_size_mb:.1f}MB exceeds 20MB limit")
        result = {"num": att["num"], "name": att["name"], "text": f"[Document too large for text extraction ({pdf_size_mb:.1f}MB) - likely image/map content]", "tokens": 0, "pages": 0, "category": "skipped"}
        try:
            _blob_write(BLOB_CONTAINER_CACHE, cache_key, json.dumps(result, ensure_ascii=False))
        except Exception:
            pass
        del pdf_bytes
        return result

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
                logging.warning(f"PDF extraction timed out for {att['name']} ({pdf_size_mb:.1f}MB)")
                result = {"num": att["num"], "name": att["name"], "text": "[PDF extraction timed out - document too complex for text extraction]", "tokens": 0, "pages": 0, "category": "skipped"}
                try:
                    _blob_write(BLOB_CONTAINER_CACHE, cache_key, json.dumps(result, ensure_ascii=False))
                except Exception:
                    pass
                del pdf_bytes
                return result
        full_text = "\n\n".join(text_parts)
        del pdf_bytes
        if not full_text.strip():
            result = {"num": att["num"], "name": att["name"], "text": "[Not machine-readable - possibly scanned/image document]", "tokens": 0, "pages": page_count, "category": "skipped"}
            # Cache the "not readable" result so we don't re-download next time
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
        import time as _time
        chunks = _chunk_text(full_text, max_tokens=3000)
        logging.info(f"Summarizing {att['name']}: {len(chunks)} chunks, ~{token_est} tokens, ~{len(chunks) * TOKENS_PER_CALL} TPM needed")
        summaries = []
        for i, chunk in enumerate(chunks):
            summary = _llm_summarize(chunk, att["name"], i + 1, len(chunks), token_provider)
            summaries.append(summary)
            # Throttle: wait between calls to stay within rate limit
            if i < len(chunks) - 1:
                _time.sleep(MIN_DELAY_BETWEEN_CALLS)
        final_text = "\n\n".join(summaries)
        del full_text
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

    # Test getting a token
    try:
        cred = ManagedIdentityCredential(client_id=client_id)
        token = cred.get_token("https://storage.azure.com/.default")
        results["token_obtained"] = True
        results["token_expires_on"] = token.expires_on
    except Exception as e:
        results["token_obtained"] = False
        results["token_error"] = f"{type(e).__name__}: {e}"

    # Test blob access to stahjoagentcache - test actual container operations
    try:
        cred2 = ManagedIdentityCredential(client_id=client_id)
        svc = BlobServiceClient(account_url=BLOB_ACCOUNT_URL, credential=cred2)
        container_client = svc.get_container_client("jobs")
        try:
            container_client.get_container_properties()
            results["blob_access"] = True
            results["container_exists"] = True
        except Exception as ce:
            if "ContainerNotFound" in str(ce):
                container_client.create_container()
                results["blob_access"] = True
                results["container_created"] = True
            else:
                raise
    except Exception as e:
        results["blob_access"] = False
        results["blob_error"] = f"{type(e).__name__}: {e}"
        results["blob_traceback"] = traceback.format_exc()[-500:]

    # Test blob access to own storage (stpdfprocahjo)
    try:
        own_url = f"https://{os.environ.get('AzureWebJobsStorage__accountName', 'stpdfprocahjo')}.blob.core.windows.net"
        svc2 = BlobServiceClient(account_url=own_url, credential=cred2)
        props2 = svc2.get_service_properties()
        results["own_blob_access"] = True
    except Exception as e:
        results["own_blob_access"] = False
        results["own_blob_error"] = f"{type(e).__name__}: {e}"

    return func.HttpResponse(json.dumps(results, default=str), mimetype="application/json")
