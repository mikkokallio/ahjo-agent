"""Local UI backend for the Ahjo Meeting Agent."""

import asyncio
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from azure.identity import AzureCliCredential
from bs4 import BeautifulSoup
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from cache_db import get_agenda as db_get_agenda, put_agenda as db_put_agenda
from cache_db import get_attachments as db_get_attachments, put_attachments as db_put_attachments
from cache_db import invalidate_attachments as db_invalidate_attachments
from cache_db import get_summary as db_get_summary, put_summary as db_put_summary

logger = logging.getLogger("ahjo-ui")

app = FastAPI(title="Ahjo Agent UI")

BASE_URL = "https://paatokset.hel.fi"
AGENT_ENDPOINT = os.environ.get(
    "AGENT_ENDPOINT",
    "https://ai-account-mapx4sxw6thvg.services.ai.azure.com/api/projects/ai-project-ahjo-agent/agents/ahjo-meetings/endpoint/protocols/openai/responses?api-version=v1",
)

_credential = None
_cached_token: str | None = None
_token_expires_at: float = 0  # epoch seconds
_threads: dict[str, list] = {}  # meeting_key -> list of messages
_agent_client: httpx.AsyncClient | None = None


def _get_credential():
    global _credential
    if _credential is None:
        _credential = AzureCliCredential(process_timeout=60)
    return _credential


def _get_token_sync() -> str:
    """Acquire token, using cache if still valid (with 5-min buffer)."""
    global _cached_token, _token_expires_at
    now = time.time()
    if _cached_token and now < _token_expires_at - 300:
        return _cached_token
    cred = _get_credential()
    token = cred.get_token("https://ml.azure.com/.default")
    _cached_token = token.token
    _token_expires_at = token.expires_on
    logger.info("Token acquired, expires in %.0fs", token.expires_on - now)
    return _cached_token


async def _get_token() -> str:
    """Non-blocking token acquisition (runs sync call in thread pool)."""
    return await asyncio.to_thread(_get_token_sync)


def _get_agent_client() -> httpx.AsyncClient:
    """Get or create a persistent HTTP client for the agent API."""
    global _agent_client
    if _agent_client is None or _agent_client.is_closed:
        _agent_client = httpx.AsyncClient(
            timeout=httpx.Timeout(600, connect=30),
            http2=True,
        )
    return _agent_client


@app.get("/")
async def index():
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.get("/api/meetings")
async def get_meetings(days_back: int = 30, days_ahead: int = 14):
    """Fetch meetings from Ahjo API, grouped by date."""
    from_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    target_date = (datetime.now() + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(f"{BASE_URL}/fi/ahjo_api/meetings", params={"from": from_date})
        resp.raise_for_status()
        raw = resp.json()

    data = raw.get("data", raw) if isinstance(raw, dict) else {}

    grouped = {}
    for date_str, day_meetings in (data.items() if isinstance(data, dict) else []):
        if date_str > target_date:
            continue
        meetings_for_day = []
        for m in day_meetings:
            if m.get("meeting_cancelled"):
                continue
            meetings_for_day.append({
                "policymaker": m.get("policymaker", ""),
                "policymaker_name": m.get("policymaker_name", ""),
                "start_time": m.get("start_time", ""),
                "status": m.get("status", ""),
                "meeting_link": m.get("decision_link", "") or m.get("minutes_link", ""),
                "date": date_str,
            })
        if meetings_for_day:
            grouped[date_str] = meetings_for_day

    return JSONResponse(grouped)


@app.get("/api/threads")
async def get_threads():
    """List threads that have messages."""
    return JSONResponse({k: len(v) for k, v in _threads.items() if v})


_TOOL_LABELS = {
    "get_meetings": "Haen kokouksia...",
    "get_meeting_agenda": "Haen asialistaa...",
    "get_item_attachments": "Haen liitteitä...",
    "check_attachment_cache": "Tarkistan välimuistia...",
    "read_attachment": "Luen liitteitä...",
    "get_attachment_results": "Haen tuloksia...",
}


def _extract_progress(tool_name: str, output: str) -> str | None:
    """Extract a user-friendly progress message from tool output."""
    if not output:
        return None
    # read_attachment returns job info
    if tool_name == "read_attachment" and "processing" in output.lower():
        return "Liitteitä käsitellään..."
    # get_attachment_results returns progress
    if tool_name == "get_attachment_results":
        if "still processing" in output.lower() or "processing" in output.lower():
            # Try to extract progress numbers
            import re as _re
            m = _re.search(r"(\d+)/(\d+)", output)
            if m:
                return f"Käsitellään liitteitä... {m.group(1)}/{m.group(2)} valmis"
            return "Liitteitä käsitellään..."
        if "completed" in output.lower():
            return "Liitteet luettu, muodostan vastausta..."
    # check_attachment_cache result
    if tool_name == "check_attachment_cache":
        if "all published attachments are cached" in output.lower():
            return "Kaikki liitteet välimuistissa"
        import re as _re
        m = _re.search(r"(\d+) already cached", output)
        if m:
            return f"{m.group(1)} liitettä välimuistissa"
    # get_meeting_agenda result
    if tool_name == "get_meeting_agenda":
        import re as _re
        m = _re.search(r"Agenda \((\d+) items\)", output)
        if m:
            return f"Asialista: {m.group(1)} kohtaa"
    return None


@app.post("/api/chat")
async def chat(request: Request):
    """Send a message to the agent with streaming progress via SSE."""
    body = await request.json()
    user_message = body.get("message", "")
    meeting_key = body.get("meeting_key", "")
    meeting_context = body.get("meeting_context", {})
    selection_context = body.get("selection_context")

    if not user_message:
        return JSONResponse({"error": "message required"}, status_code=400)

    # Build thread history
    if meeting_key not in _threads:
        _threads[meeting_key] = []

    thread = _threads[meeting_key]

    # Inject meeting context as system context into the first user message
    context_prefix = ""
    if meeting_context:
        context_prefix = (
            f"[MEETING CONTEXT — this is always the active meeting, do NOT ask the user "
            f"to identify it again. Meeting: {meeting_context.get('policymaker_name', '')} "
            f"on {meeting_context.get('date', '')}. "
            f"Policymaker code: {meeting_context.get('policymaker', '')}. "
            f"RULES: "
            f"1) When the user says 'this meeting', 'tämä kokous', 'tämän kokouksen', "
            f"or refers to agenda/attachments without specifying which meeting, "
            f"ALWAYS use this meeting. NEVER ask which meeting they mean. "
            f"2) NEVER ask for confirmation before proceeding. If the user asks you to "
            f"list, fetch, summarize, or count something, DO IT IMMEDIATELY. "
            f"Do not ask 'do you want all or just some?' — default to ALL. "
            f"3) If a task requires multiple tool calls, proceed with all of them "
            f"without asking for permission.]\n\n"
        )

    # Add selection context if user has something selected in the right pane
    selection_prefix = ""
    if selection_context:
        if selection_context.get("type") == "attachment":
            selection_prefix = (
                f"[The user has selected attachment {selection_context.get('attachment_num')} "
                f"'{selection_context.get('attachment_name', '')}' "
                f"from agenda item '{selection_context.get('agenda_item', '')}'. "
                f"Case URL: {selection_context.get('case_url', '')}. "
                f"When they say 'this', 'summarize', etc., they mean this specific attachment.]\n\n"
            )
        elif selection_context.get("type") == "agenda_item":
            selection_prefix = (
                f"[The user has selected agenda item '{selection_context.get('agenda_item', '')}'. "
                f"Case URL: {selection_context.get('case_url', '')}. "
                f"When they say 'summarize' without specifying, they likely mean all attachments for this item.]\n\n"
            )

    thread.append({"role": "user", "content": user_message})

    # Build input for the agent (Responses API format)
    input_messages = []
    for msg in thread:
        if msg["role"] == "user":
            input_messages.append({"type": "message", "role": "user", "content": msg["content"]})
        elif msg["role"] == "assistant":
            input_messages.append({"type": "message", "role": "assistant", "content": msg["content"]})

    # Prepend meeting context to the first user message
    if context_prefix and input_messages:
        first_user = next((m for m in input_messages if m["role"] == "user"), None)
        if first_user:
            first_user["content"] = context_prefix + first_user["content"]

    # Prepend selection context to the latest user message
    if selection_prefix and input_messages:
        last_user = next((m for m in reversed(input_messages) if m["role"] == "user"), None)
        if last_user:
            last_user["content"] = selection_prefix + last_user["content"]

    # Inject pre-fetched agenda into context to avoid redundant tool calls
    agenda_items = body.get("agenda_items")
    if agenda_items:
        agenda_lines = []
        for i, it in enumerate(agenda_items):
            line = f"{it.get('num', i+1)}. {it.get('title', '')}"
            if it.get('case_url'):
                line += f" [case_url: {it['case_url']}]"
            agenda_lines.append(line)
        agenda_text = "\n".join(agenda_lines)

        # Look up cached attachments for each agenda item
        attachments_text = ""
        for it in agenda_items:
            case_url = it.get("case_url", "")
            if not case_url:
                continue
            cached_atts = db_get_attachments(case_url)
            if cached_atts:
                published = [a for a in cached_atts if a.get("published")]
                if published:
                    att_lines = []
                    for a in published:
                        info = f"  - Liite {a.get('num')}: {a.get('name', '')}"
                        if a.get("pages"):
                            info += f" ({a['pages']} sivua"
                            if a.get("tokens"):
                                info += f", {a['tokens']} tokenia"
                            info += ")"
                        if a.get("category"):
                            info += f" [{a['category']}]"
                        att_lines.append(info)
                    attachments_text += f"\n{it.get('num', '')}. {it.get('title', '')}:\n" + "\n".join(att_lines)

        agenda_note = (
            f"[PRE-FETCHED AGENDA ({len(agenda_items)} items):\n{agenda_text}\n"
            f"You do NOT need to call get_meeting_agenda — the data is already here. "
            f"You can use the case_url values above directly with get_item_attachments.]\n\n"
        )
        if attachments_text:
            agenda_note += (
                f"[PRE-FETCHED ATTACHMENTS (metadata from cache):{attachments_text}\n"
                f"You do NOT need to call get_item_attachments for basic info — "
                f"the attachment names, page counts, and categories are listed above. "
                f"Only call get_item_attachments or read_attachment if you need the FULL TEXT "
                f"of a specific attachment that is not provided here.]\n\n"
            )

        # Add after meeting context in the first user message
        if input_messages:
            first_user = next((m for m in input_messages if m["role"] == "user"), None)
            if first_user:
                first_user["content"] = agenda_note + first_user["content"]

    payload = {"input": input_messages, "stream": True}

    try:
        token = await _get_token()
    except Exception as e:
        logger.exception("Token acquisition failed")
        return JSONResponse({"error": f"Token error: {e}"}, status_code=502)

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    async def event_stream():
        assistant_text = ""
        try:
            client = _get_agent_client()
            async with client.stream(
                "POST", AGENT_ENDPOINT, json=payload, headers=headers
            ) as resp:
                if resp.status_code != 200:
                    await resp.aread()
                    error_msg = f"Agent returned {resp.status_code}"
                    yield f"data: {json.dumps({'type': 'error', 'text': error_msg})}\n\n"
                    thread.append({"role": "assistant", "content": f"[Error: {error_msg}]"})
                    return

                # Signal that connection is established
                yield f"data: {json.dumps({'type': 'status', 'text': 'Käsitellään...'})}\n\n"

                current_tool = None
                tool_call_count = 0
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    raw = line[6:]
                    if raw == "[DONE]":
                        break
                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    etype = event.get("type", "")

                    # Tool call started
                    if etype == "response.output_item.added":
                        item = event.get("item", {})
                        if item.get("type") == "function_call":
                            tool_name = item.get("name", "")
                            current_tool = tool_name
                            tool_call_count += 1
                            label = _TOOL_LABELS.get(tool_name, f"Kutsun: {tool_name}...")
                            yield f"data: {json.dumps({'type': 'status', 'text': label})}\n\n"
                        elif item.get("type") == "function_call_output":
                            # Parse tool output for progress hints
                            output_text = item.get("output", "")
                            progress_msg = _extract_progress(current_tool, output_text)
                            if progress_msg:
                                yield f"data: {json.dumps({'type': 'status', 'text': progress_msg})}\n\n"

                    # Text delta (final response streaming)
                    elif etype == "response.output_text.delta":
                        delta = event.get("delta", "")
                        if delta:
                            assistant_text += delta
                            yield f"data: {json.dumps({'type': 'delta', 'text': delta})}\n\n"

                    # Response completed
                    elif etype == "response.completed":
                        pass

        except Exception as e:
            error_msg = str(e)
            yield f"data: {json.dumps({'type': 'error', 'text': error_msg})}\n\n"
            if not assistant_text:
                thread.append({"role": "assistant", "content": f"[Virhe: {error_msg}]"})
                return

        if assistant_text:
            thread.append({"role": "assistant", "content": assistant_text})
        else:
            if tool_call_count > 0:
                timeout_msg = (
                    f"Agentti teki {tool_call_count} työkalukutsua mutta ei tuottanut vastausta. "
                    f"Pyyntö oli liian raskas agentille. Kokeile pyytää vähemmän kerralla."
                )
            else:
                timeout_msg = "Agentti ei tuottanut vastausta. Yritä uudelleen."
            thread.append({"role": "assistant", "content": f"[{timeout_msg}]"})
            yield f"data: {json.dumps({'type': 'delta', 'text': timeout_msg})}\n\n"

        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


PDF_PROCESSOR_URL = os.environ.get(
    "PDF_PROCESSOR_URL", "https://func-pdf-processor-ahjo.azurewebsites.net"
)
PDF_PROCESSOR_KEY = os.environ.get(
    "PDF_PROCESSOR_KEY", "REMOVED_FUNCTION_KEY"
)


@app.get("/api/agenda")
async def get_agenda(meeting_link: str):
    """Fetch and parse agenda items from a meeting page URL."""
    if not meeting_link:
        return JSONResponse({"items": []})

    # Return from cache if available
    cached_items = db_get_agenda(meeting_link)
    if cached_items is not None:
        return JSONResponse({"items": cached_items, "meeting_url": meeting_link, "cached": True})

    full_url = meeting_link if meeting_link.startswith("http") else BASE_URL + meeting_link
    full_url = full_url.split("#")[0]

    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(full_url)
            resp.raise_for_status()
    except Exception as e:
        return JSONResponse({"error": str(e), "items": []}, status_code=502)

    soup = BeautifulSoup(resp.text, "html.parser")
    items = []

    # Try h3 headings first (common format)
    for h3 in soup.find_all("h3"):
        text = h3.get_text(strip=True)
        if text and re.match(r"^\d+", text):
            case_url = ""
            div = h3.find_next_sibling("div")
            if div:
                link = div.find("a", href=True)
                if link:
                    case_url = link.get("href", "")
            num_match = re.match(r"^(\d+)", text)
            num = int(num_match.group(1)) if num_match else len(items) + 1
            items.append({"num": num, "title": text, "case_url": case_url})

    # Fallback: direct links to /fi/asia/
    if not items:
        seen = set()
        for link in soup.find_all("a", href=re.compile(r"/fi/asia/")):
            case_url = link.get("href", "")
            if case_url in seen:
                continue
            seen.add(case_url)
            text = link.get_text(strip=True)
            if text:
                items.append({"num": len(items) + 1, "title": text, "case_url": case_url})

    # Cache permanently (agendas don't change once published)
    if items:
        db_put_agenda(meeting_link, items)

    return JSONResponse({"items": items, "meeting_url": full_url})


@app.post("/api/attachments-bulk")
async def get_attachments_bulk(request: Request):
    """Return all cached attachments for multiple case_urls instantly.
    Uncached items are skipped — the frontend fetches those on-demand."""
    body = await request.json()
    case_urls = body.get("case_urls", [])
    if not case_urls:
        return JSONResponse({"results": {}})

    results = {}
    for url in case_urls:
        cached = db_get_attachments(url)
        if cached is not None:
            results[url] = cached

    # Kick off background prefetch of summaries from blob
    if results:
        asyncio.create_task(_prefetch_summaries_from_blob(results))

    return JSONResponse({"results": results})


@app.get("/api/attachments")
async def get_attachments(case_url: str):
    """Get attachments for a case URL, including cache status from PDF processor."""
    if not case_url:
        return JSONResponse({"attachments": []})

    # Return from cache if available
    cached_atts = db_get_attachments(case_url)
    if cached_atts is not None:
        return JSONResponse({"attachments": cached_atts, "case_url": case_url, "cached": True})

    attachments = await _scrape_attachments(case_url)
    return JSONResponse({"attachments": attachments, "case_url": case_url})


async def _scrape_attachments(case_url: str) -> list:
    """Scrape attachments from a case page and cache the result."""
    full_url = case_url if case_url.startswith("http") else BASE_URL + case_url

    # Parse attachments from the case page
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(full_url)
            resp.raise_for_status()
    except Exception:
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    attachments = []

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
                        num = int(num_match.group(1)) if num_match else len(attachments) + 1
                        name = num_match.group(2) if num_match else name_text
                        attachments.append({"num": num, "name": name, "pdf_url": pdf_url, "published": True})
                    else:
                        full_text = att_div.get_text(strip=True)
                        full_text = full_text.replace("Liitettä ei julkaista internetissä.", "").strip()
                        num_match = re.match(r"^(\d+)\.\s*(.+)", full_text)
                        num = int(num_match.group(1)) if num_match else len(attachments) + 1
                        name = num_match.group(2) if num_match else full_text
                        attachments.append({"num": num, "name": name, "pdf_url": "", "published": False})
            break

    # Check cache status directly from blob (no Azure Function dependency)
    if attachments:
        try:
            token = await _get_storage_token()
            client = _get_blob_client()
            sem = asyncio.Semaphore(6)

            async def check_one(att):
                if not att.get("pdf_url"):
                    return
                async with sem:
                    blob_key = _pdf_cache_key(att["pdf_url"])
                    try:
                        resp = await client.get(
                            f"/{BLOB_CONTAINER_CACHE}/{blob_key}",
                            headers={"Authorization": f"Bearer {token}", "x-ms-version": "2023-11-03"},
                        )
                        if resp.status_code == 200:
                            blob_data = json.loads(resp.text)
                            att["cached"] = True
                            att["pages"] = blob_data.get("pages")
                            att["tokens"] = blob_data.get("tokens")
                            att["category"] = blob_data.get("category")
                        else:
                            att["cached"] = False
                    except Exception:
                        att["cached"] = False

            await asyncio.gather(*[check_one(att) for att in attachments if att.get("published")])
        except Exception:
            pass  # Cache check is best-effort

    # Cache permanently (attachment metadata doesn't change)
    # Store even empty lists so we don't re-scrape items with no attachments
    db_put_attachments(case_url, attachments)

    return attachments


@app.post("/api/process-attachment")
async def process_attachment(request: Request):
    """Trigger processing of a single attachment via the PDF processor."""
    body = await request.json()
    case_url = body.get("case_url", "")
    attachment_num = body.get("attachment_num")

    if not case_url or attachment_num is None:
        return JSONResponse({"error": "case_url and attachment_num required"}, status_code=400)

    process_url = f"{PDF_PROCESSOR_URL}/api/process"
    if PDF_PROCESSOR_KEY:
        process_url += f"?code={PDF_PROCESSOR_KEY}"

    payload = {
        "case_url": case_url,
        "attachment_numbers": str(attachment_num),
    }

    try:
        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(process_url, json=payload)
            result = resp.json()
            # Invalidate attachment cache so next load gets fresh data
            db_invalidate_attachments(case_url)

            # Fetch the job status to get the actual summary text
            if resp.status_code == 200 and result.get("job_id"):
                status_url = f"{PDF_PROCESSOR_URL}/api/status/{result['job_id']}"
                if PDF_PROCESSOR_KEY:
                    status_url += f"?code={PDF_PROCESSOR_KEY}"
                status_resp = await client.get(status_url)
                if status_resp.status_code == 200:
                    status_data = status_resp.json()
                    # Find the processed attachment in results
                    for att_result in status_data.get("results", []):
                        if att_result.get("num") == attachment_num:
                            result["summary"] = att_result.get("text", "")
                            result["pages"] = att_result.get("pages")
                            result["tokens"] = att_result.get("tokens")
                            result["category"] = att_result.get("category")
                            break

            return JSONResponse(result, status_code=resp.status_code)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)


BLOB_ACCOUNT_URL = "https://stpdfprocahjo.blob.core.windows.net"
BLOB_CONTAINER_CACHE = "pdf-cache"

_cached_storage_token = None
_storage_token_expires_at = 0
_blob_client: httpx.AsyncClient | None = None


def _get_storage_token_sync() -> str:
    """Acquire storage-scoped token with caching."""
    global _cached_storage_token, _storage_token_expires_at
    now = time.time()
    if _cached_storage_token and now < _storage_token_expires_at - 300:
        return _cached_storage_token
    cred = _get_credential()
    token = cred.get_token("https://storage.azure.com/.default")
    _cached_storage_token = token.token
    _storage_token_expires_at = token.expires_on
    return _cached_storage_token


async def _get_storage_token() -> str:
    return await asyncio.to_thread(_get_storage_token_sync)


def _get_blob_client() -> httpx.AsyncClient:
    """Persistent async client for blob storage (reuses TCP/TLS connection)."""
    global _blob_client
    if _blob_client is None:
        _blob_client = httpx.AsyncClient(
            base_url=BLOB_ACCOUNT_URL,
            timeout=30,
            http2=True,
        )
    return _blob_client


def _pdf_cache_key(pdf_url: str) -> str:
    import hashlib
    return hashlib.sha256(pdf_url.encode()).hexdigest()[:16]


async def _read_blob_summary(pdf_url: str) -> dict | None:
    """Read summary directly from blob storage (bypasses Azure Function)."""
    blob_key = _pdf_cache_key(pdf_url)
    token = await _get_storage_token()
    try:
        client = _get_blob_client()
        resp = await client.get(
            f"/{BLOB_CONTAINER_CACHE}/{blob_key}",
            headers={"Authorization": f"Bearer {token}", "x-ms-version": "2023-11-03"},
        )
        if resp.status_code == 200:
            return json.loads(resp.text)
    except Exception as e:
        logger.warning(f"Direct blob read failed for {blob_key}: {e}")
    return None


async def _prefetch_summaries_from_blob(results: dict):
    """Background task: pull all blob-cached summaries into local cache."""
    try:
        token = await _get_storage_token()
    except Exception:
        return

    to_fetch = []  # (case_url, att_num, pdf_url)
    for case_url, attachments in results.items():
        for att in attachments:
            if att.get("pdf_url"):
                # Skip if already in local summary cache
                if db_get_summary(case_url, att["num"]) is None:
                    to_fetch.append((case_url, att["num"], att["pdf_url"]))

    if not to_fetch:
        return

    logger.info(f"Prefetching {len(to_fetch)} summaries from blob...")
    client = _get_blob_client()
    sem = asyncio.Semaphore(6)

    async def fetch_one(case_url, att_num, pdf_url):
        async with sem:
            blob_key = _pdf_cache_key(pdf_url)
            try:
                resp = await client.get(
                    f"/{BLOB_CONTAINER_CACHE}/{blob_key}",
                    headers={"Authorization": f"Bearer {token}", "x-ms-version": "2023-11-03"},
                )
                if resp.status_code == 200:
                    blob_data = json.loads(resp.text)
                    summary_data = {
                        "summary": blob_data.get("text", ""),
                        "pages": blob_data.get("pages"),
                        "tokens": blob_data.get("tokens"),
                        "category": blob_data.get("category"),
                    }
                    db_put_summary(case_url, att_num, summary_data)
                    # Fix stale attachment metadata
                    atts = db_get_attachments(case_url)
                    if atts:
                        for a in atts:
                            if a.get("num") == att_num and not a.get("cached"):
                                a["cached"] = True
                                a["pages"] = blob_data.get("pages")
                                a["tokens"] = blob_data.get("tokens")
                                a["category"] = blob_data.get("category")
                                db_put_attachments(case_url, atts)
                                break
            except Exception:
                pass

    await asyncio.gather(*[fetch_one(cu, an, pu) for cu, an, pu in to_fetch])
    logger.info(f"Prefetch complete: {len(to_fetch)} summaries attempted")


@app.post("/api/attachment-summary")
async def attachment_summary(request: Request):
    """Fetch summary for an attachment. Local cache -> blob direct -> Azure Function."""
    body = await request.json()
    case_url = body.get("case_url", "")
    attachment_num = body.get("attachment_num")

    if not case_url or attachment_num is None:
        return JSONResponse({"error": "case_url and attachment_num required"}, status_code=400)

    # 1. Check local SQLite/memory cache (instant, sub-ms)
    cached = db_get_summary(case_url, attachment_num)
    if cached is not None:
        return JSONResponse(cached)

    # 2. Try reading directly from blob (works even if local metadata says cached=False)
    attachments = db_get_attachments(case_url)
    att_meta = None
    if attachments:
        for att in attachments:
            if att.get("num") == attachment_num:
                att_meta = att
                break

    if att_meta and att_meta.get("pdf_url"):
        blob_data = await _read_blob_summary(att_meta["pdf_url"])
        if blob_data:
            summary_data = {
                "summary": blob_data.get("text", ""),
                "pages": blob_data.get("pages"),
                "tokens": blob_data.get("tokens"),
                "category": blob_data.get("category"),
            }
            db_put_summary(case_url, attachment_num, summary_data)
            # Fix stale cached=False in attachment metadata
            if not att_meta.get("cached"):
                att_meta["cached"] = True
                att_meta["pages"] = blob_data.get("pages")
                att_meta["tokens"] = blob_data.get("tokens")
                att_meta["category"] = blob_data.get("category")
                db_put_attachments(case_url, attachments)
            return JSONResponse(summary_data)

    # 3. Fall back to Azure Function (slow, cold start possible)
    process_url = f"{PDF_PROCESSOR_URL}/api/process"
    if PDF_PROCESSOR_KEY:
        process_url += f"?code={PDF_PROCESSOR_KEY}"

    payload = {
        "case_url": case_url,
        "attachment_numbers": str(attachment_num),
    }

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(process_url, json=payload)
            if resp.status_code != 200:
                return JSONResponse({"error": f"Process returned {resp.status_code}"}, status_code=502)
            result = resp.json()
            job_id = result.get("job_id")
            if not job_id:
                return JSONResponse({"error": "No job_id returned"}, status_code=502)

            status_url = f"{PDF_PROCESSOR_URL}/api/status/{job_id}"
            if PDF_PROCESSOR_KEY:
                status_url += f"?code={PDF_PROCESSOR_KEY}"
            status_resp = await client.get(status_url)
            if status_resp.status_code != 200:
                return JSONResponse({"error": "Status fetch failed"}, status_code=502)
            status_data = status_resp.json()
            for att_result in status_data.get("results", []):
                if att_result.get("num") == attachment_num:
                    summary_data = {
                        "summary": att_result.get("text", ""),
                        "pages": att_result.get("pages"),
                        "tokens": att_result.get("tokens"),
                        "category": att_result.get("category"),
                    }
                    db_put_summary(case_url, attachment_num, summary_data)
                    return JSONResponse(summary_data)
            return JSONResponse({"error": "Attachment not found in results"}, status_code=404)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)


app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
