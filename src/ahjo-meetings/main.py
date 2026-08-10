"""Ahjo Meeting Briefing Agent - using Microsoft Agent Framework.

This uses FoundryChatClient + ResponsesHostServer which handles the
Responses protocol, Playground compatibility, and streaming automatically.
"""

import json
import os
import re
from datetime import datetime, timedelta

import httpx
from agent_framework import Agent, tool
from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from pydantic import Field
from typing_extensions import Annotated

load_dotenv()

BASE_URL = "https://paatokset.hel.fi"
SEARCH_ENDPOINT = os.environ["AZURE_AI_SEARCH_ENDPOINT"]
AI_ENDPOINT = os.environ["AZURE_AI_ENDPOINT"]
EMBEDDING_DEPLOYMENT = os.environ.get("AZURE_AI_EMBEDDING_DEPLOYMENT_NAME", "embed-v-4-0")

_credential = None


def _get_credential():
    global _credential
    if _credential is None:
        _credential = DefaultAzureCredential()
    return _credential


@tool(approval_mode="never_require")
def get_meetings(
    days_ahead: Annotated[int, Field(description="Number of days ahead to look for meetings. Default 30.")] = 30,
    days_back: Annotated[int, Field(description="Number of days back to look for past meetings. Default 0 (only future). Use e.g. 60 to include meetings from the past 2 months.")] = 0,
) -> str:
    """Get meetings from Helsinki Ahjo system. Returns meeting list with dates, organizations, and IDs. Supports both past and future meetings."""
    from_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    target_date = (datetime.now() + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    url = f"{BASE_URL}/fi/ahjo_api/meetings"
    params = {"from": from_date}

    with httpx.Client(timeout=30) as client:
        resp = client.get(url, params=params)
        resp.raise_for_status()
        raw = resp.json()

    data = raw.get("data", raw) if isinstance(raw, dict) else {}

    meetings = []
    for date_str, day_meetings in (data.items() if isinstance(data, dict) else []):
        if date_str > target_date:
            continue
        for m in day_meetings:
            if m.get("meeting_cancelled"):
                continue
            meeting_link = m.get("decision_link", "") or m.get("minutes_link", "")
            policymaker = m.get("policymaker", "")
            line = (
                f"- {date_str} klo {m.get('start_time', '?')} | "
                f"{m.get('policymaker_name', 'Unknown')} | "
                f"Policymaker code: {policymaker} | "
                f"Status: {m.get('status', '')}"
            )
            if meeting_link:
                line += f" | Meeting page: {meeting_link}"
            meetings.append(line)

    meetings.sort()
    if not meetings:
        return f"No meetings found in the specified range ({days_back} days back, {days_ahead} days ahead)."
    return (
        f"Found {len(meetings)} meetings ({days_back}d back to {days_ahead}d ahead):\n"
        + "\n".join(meetings[:50])
        + "\n\nNote: To get the agenda for a meeting, use get_meeting_agenda with the policymaker code and optionally a meeting_date."
    )


@tool(approval_mode="never_require")
def get_meeting_agenda(
    policymaker_code: Annotated[str, Field(description="The policymaker code from get_meetings (e.g. 'U540100', 'U321100', '00400'). Used to find the meeting page for that body.")],
    meeting_date: Annotated[str, Field(description="Optional: specific meeting date in YYYY-MM-DD format. If omitted, returns the most recent meeting.")] = "",
) -> str:
    """Get the agenda or decisions for a meeting of a given policymaker/committee. Uses the policymaker code to look up the meeting page on paatokset.hel.fi. Optionally specify a date to get a specific past or future meeting."""

    # First, find the meeting for this policymaker from the API
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        # Get meetings - search from 2025 to cover past meetings
        resp = client.get(f"{BASE_URL}/fi/ahjo_api/meetings", params={"from": "2025-01-01"})
        resp.raise_for_status()
        raw = resp.json()
        data = raw.get("data", raw) if isinstance(raw, dict) else {}

        # Find the matching meeting for this policymaker
        meeting_url = None
        meeting_date_found = None

        def _get_meeting_link(m):
            """Get meeting page link from either decision_link or minutes_link."""
            return m.get("decision_link", "") or m.get("minutes_link", "")

        if meeting_date:
            # Look for a specific date
            if meeting_date in data and isinstance(data[meeting_date], list):
                for m in data[meeting_date]:
                    link = _get_meeting_link(m)
                    if m.get("policymaker") == policymaker_code and link:
                        meeting_url = link
                        meeting_date_found = meeting_date
                        break
            # If exact date not found, find the closest meeting for this policymaker
            if not meeting_url:
                for date_str in sorted(data.keys(), key=lambda d: abs((datetime.strptime(d, "%Y-%m-%d") - datetime.strptime(meeting_date, "%Y-%m-%d")).days)):
                    for m in data.get(date_str, []):
                        link = _get_meeting_link(m)
                        if m.get("policymaker") == policymaker_code and link:
                            meeting_url = link
                            meeting_date_found = date_str
                            break
                    if meeting_url:
                        break
        else:
            # Find the most recent meeting with a page link for this policymaker
            for date_str, day_meetings in sorted(data.items(), reverse=True) if isinstance(data, dict) else []:
                for m in day_meetings:
                    link = _get_meeting_link(m)
                    if m.get("policymaker") == policymaker_code and link:
                        meeting_url = link
                        meeting_date_found = date_str
                        break
                if meeting_url:
                    break

        if not meeting_url:
            # Try constructing the policymaker's document listing page
            # Pattern: /fi/paattajat/{slug}/asiakirjat - but we don't have the slug
            return (
                f"No published meeting page found for policymaker {policymaker_code}. "
                f"The agenda may not have been published yet. "
                f"This is common for upcoming meetings - agendas are typically published a few days before the meeting."
            )

        # Remove any fragment
        meeting_url = meeting_url.split("#")[0]

        # Fetch the meeting page
        full_url = meeting_url if meeting_url.startswith("http") else BASE_URL + meeting_url
        page_resp = client.get(full_url)
        page_resp.raise_for_status()

    soup = BeautifulSoup(page_resp.text, "html.parser")

    # Parse agenda items - try h3 headings first (some meeting pages use this format)
    items = []
    for h3 in soup.find_all("h3"):
        text = h3.get_text(strip=True)
        if text and re.match(r"^\d+", text):
            # Find the case URL from the sibling div's "Lue esitys" link
            case_url = ""
            div = h3.find_next_sibling("div")
            if div:
                link = div.find("a", href=True)
                if link:
                    case_url = link.get("href", "")
            line = f"- {text}"
            if case_url:
                line += f" | Case: {case_url}"
            items.append(line)

    # Fallback: some pages (KYL, KH, valtuusto) use direct links to /fi/asia/
    if not items:
        seen_urls = set()
        for link in soup.find_all("a", href=re.compile(r"/fi/asia/")):
            case_url = link.get("href", "")
            if case_url in seen_urls:
                continue
            seen_urls.add(case_url)
            text = link.get_text(strip=True)
            if text:
                items.append(f"- {text} | Case: {case_url}")

    result = f"Meeting: {meeting_date_found} | {policymaker_code}\nPage: {full_url}\n"
    if items:
        result += f"\nAgenda ({len(items)} items):\n" + "\n".join(items[:30])
        result += "\n\nNote: To see attachments for an item, use get_item_attachments with the case URL path."
    if not items:
        result += "\nNo agenda items found on this page."

    return result


@tool(approval_mode="never_require")
def get_item_attachments(
    case_url: Annotated[str, Field(description="The case URL path from get_meeting_agenda (e.g. '/fi/asia/hel-2025-018125?paatos=...')")],
) -> str:
    """Get the list of attachments for a specific agenda item/decision. Takes the case URL from get_meeting_agenda output. Returns attachment names and availability status (some are not published online)."""

    if not case_url.startswith("http"):
        case_url = BASE_URL + case_url

    with httpx.Client(timeout=30, follow_redirects=True) as client:
        resp = client.get(case_url)
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")

    # Find the Liitteet (Attachments) section
    attachments = []
    for h3 in soup.find_all("h3"):
        if "Liitteet" in h3.get_text():
            container = h3.find_next_sibling("div")
            if container:
                for att_div in container.find_all("div", class_="issue__attachment"):
                    # Check for downloadable PDF link
                    link = att_div.find("a", href=True)
                    if link:
                        pdf_url = link.get("href", "")
                        name_text = link.get_text(strip=True)
                        num_match = re.match(r"^(\d+)\.\s*(.+)", name_text)
                        num = int(num_match.group(1)) if num_match else len(attachments) + 1
                        name = num_match.group(2) if num_match else name_text
                        attachments.append({"num": num, "name": name, "pdf_url": pdf_url, "published": True})
                    else:
                        # Not published - extract name from text content
                        full_text = att_div.get_text(strip=True)
                        # Remove the "Liitettä ei julkaista internetissä." suffix
                        full_text = full_text.replace("Liitettä ei julkaista internetissä.", "").strip()
                        num_match = re.match(r"^(\d+)\.\s*(.+)", full_text)
                        num = int(num_match.group(1)) if num_match else len(attachments) + 1
                        name = num_match.group(2) if num_match else full_text
                        attachments.append({"num": num, "name": name, "pdf_url": "", "published": False})
            break

    # Also check for Oheismateriaali (supplementary materials)
    supplementary = []
    for h3 in soup.find_all("h3"):
        if "heismateriaali" in h3.get_text():
            div = h3.find_next_sibling("div")
            if div:
                text = div.get_text(separator="\n", strip=True)
                if text:
                    supplementary.append(text[:500])
            break

    if not attachments and not supplementary:
        return f"No attachments found for: {case_url}"

    result = f"Attachments for case: {case_url}\n\n"
    if attachments:
        result += f"Liitteet ({len(attachments)}):\n"
        for att in attachments:
            status = "available online" if att["published"] and att["pdf_url"] else "not published online" if not att["published"] else "listed (no direct link)"
            line = f"  {att['num']}. {att['name']} [{status}]"
            if att["pdf_url"]:
                line += f"\n     PDF: {att['pdf_url']}"
            result += line + "\n"
    if supplementary:
        result += f"\nOheismateriaali (supplementary):\n{supplementary[0]}"

    return result


@tool(approval_mode="never_require")
def check_attachment_cache(
    case_url: Annotated[str, Field(description="The case URL path from get_meeting_agenda (e.g. '/fi/asia/hel-2025-018125?paatos=...')")],
) -> str:
    """Check which attachments for a case are already cached (will be instant to read) vs need processing.
    Use this before read_attachment to estimate wait time and inform the user.
    For cached attachments, returns metadata: page count, token count, and whether content is full text or summarized.
    Uncached attachments need ~10-30s each for short docs, 1-3 minutes for large docs (50+ pages)."""

    func_url = os.environ.get("PDF_PROCESSOR_URL", "")
    func_key = os.environ.get("PDF_PROCESSOR_KEY", "")

    if not func_url:
        return "[Configuration error: PDF_PROCESSOR_URL not set.]"

    url = f"{func_url}/api/cache-check"
    if func_key:
        url += f"?code={func_key}"

    try:
        with httpx.Client(timeout=15) as client:
            resp = client.post(url, json={"case_url": case_url})
            resp.raise_for_status()
            data = resp.json()

        total = data.get("total", 0)
        published = data.get("published", 0)
        cached = data.get("cached", 0)
        uncached = data.get("uncached", 0)
        attachments = data.get("attachments", [])

        lines = [f"Attachments: {total} total, {published} published online, {cached} already cached"]
        if uncached > 0:
            lines.append(f"Estimated time to process all uncached: ~{uncached * 15}-{uncached * 30}s")
        else:
            lines.append("All published attachments are cached - reading will be instant.")
        lines.append("")

        for att in attachments:
            status = ""
            if not att.get("published"):
                status = "[not published]"
            elif att.get("cached"):
                status = f"[cached, {att.get('pages', '?')} pages, ~{att.get('tokens', '?')} tokens]"
            else:
                status = "[not cached - will need processing]"
            lines.append(f"  {att.get('num', '?')}. {att.get('name', '?')} {status}")

        return "\n".join(lines)
    except Exception as e:
        return f"[Error checking cache: {e}]"


@tool(approval_mode="never_require")
def read_attachment(
    case_url: Annotated[str, Field(description="The case URL path from get_meeting_agenda (e.g. '/fi/asia/hel-2025-018125?paatos=...')")],
    attachment_numbers: Annotated[str, Field(description="Comma-separated attachment numbers to read (e.g. '1,3,5'), or 'all' for all published attachments.")] = "all",
) -> str:
    """Start processing PDF attachments. Sends the work to a dedicated processing service with more resources.
    Returns a job_id. Use get_attachment_results with that job_id to check progress and retrieve results.
    If all requested PDFs are already cached, results may be returned immediately."""

    import uuid
    import time as _time

    job_id = uuid.uuid4().hex[:12]
    func_url = os.environ.get("PDF_PROCESSOR_URL", "")
    func_key = os.environ.get("PDF_PROCESSOR_KEY", "")

    if not func_url:
        return "[Configuration error: PDF_PROCESSOR_URL not set. Cannot process attachments.]"

    payload = {
        "case_url": case_url,
        "attachment_numbers": attachment_numbers,
        "job_id": job_id,
    }

    url = f"{func_url}/api/process"
    if func_key:
        url += f"?code={func_key}"

    _t0 = _time.time()
    try:
        # Give the function 60 seconds - summarization of large docs takes time
        with httpx.Client(timeout=60) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
            result = resp.json()
            _elapsed = _time.time() - _t0
            print(f"[PDF-PROC] Job {job_id} completed synchronously in {_elapsed:.1f}s", flush=True)

            # If it completed, fetch the full results via the status endpoint
            if result.get("status") == "completed":
                status_url = f"{func_url}/api/status/{job_id}"
                if func_key:
                    status_url += f"?code={func_key}"
                status_resp = client.get(status_url, timeout=30)
                if status_resp.status_code == 200:
                    data = status_resp.json()
                    results = data.get("results", [])
                    return _format_attachment_results(case_url, results, data.get("total", 0))
                return f"Processing completed ({result.get('total', 0)} attachments, {result.get('cached', 0)} from cache) but status retrieval failed (HTTP {status_resp.status_code}). Try get_attachment_results with job_id: {job_id}"
            return f"Job started: {job_id}. Use get_attachment_results to check progress."

    except httpx.TimeoutException:
        _elapsed = _time.time() - _t0
        print(f"[PDF-PROC] Job {job_id} still processing after {_elapsed:.1f}s (function running in background)", flush=True)
        return (
            f"Processing started (job_id: {job_id}). The attachments are being processed by a dedicated service.\n"
            f"Use get_attachment_results with job_id '{job_id}' to check progress and retrieve results when ready.\n"
            f"Large documents (50+ pages) may take 1-3 minutes to summarize."
        )
    except Exception as e:
        print(f"[PDF-PROC] Job {job_id} failed to start: {e}", flush=True)
        return f"[Error: PDF processing service returned an error: {e}. The service may be overloaded or the attachment could not be downloaded. Please retry in a moment.]"


@tool(approval_mode="never_require")
def get_attachment_results(
    job_id: Annotated[str, Field(description="The job_id returned by read_attachment")],
) -> str:
    """Check the status of a PDF processing job and retrieve results if complete.
    Returns progress percentage if still processing, or full results if done."""

    func_url = os.environ.get("PDF_PROCESSOR_URL", "")
    func_key = os.environ.get("PDF_PROCESSOR_KEY", "")

    if not func_url:
        return "[Configuration error: PDF_PROCESSOR_URL not set.]"

    status_url = f"{func_url}/api/status/{job_id}"
    if func_key:
        status_url += f"?code={func_key}"

    try:
        with httpx.Client(timeout=30) as client:
            resp = client.get(status_url)
            if resp.status_code == 404:
                return f"Job '{job_id}' not found. It may not have started yet or the ID is incorrect."
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        return f"[Error checking job status: {e}]"

    status = data.get("status", "unknown")
    progress = data.get("progress", 0)
    total = data.get("total", 0)

    if status == "processing":
        pct = int((progress / total) * 100) if total > 0 else 0
        return f"Job '{job_id}' is still processing: {progress}/{total} attachments done ({pct}%). Check again shortly."

    if status == "failed":
        return f"Job '{job_id}' failed. Check logs for details."

    if status == "completed":
        results = data.get("results", [])
        return _format_attachment_results("", results, total)

    return f"Job '{job_id}' has unexpected status: {status}"


def _format_attachment_results(case_url: str, results: list, total: int) -> str:
    """Format processed attachment results into a readable string."""
    if not results:
        return f"No readable attachments found."

    cached_count = sum(1 for d in results if d.get("cached"))
    unpublished = [d for d in results if d.get("unpublished")]
    processed = [d for d in results if not d.get("unpublished")]
    skipped = [d for d in processed if d.get("category") in ("skipped", "error")]

    result_parts = []
    if case_url:
        result_parts.append(f"Attachment content for: {case_url}")
    result_parts.append(f"Total: {total} processed, {cached_count} from cache, {len(unpublished)} unpublished\n")

    if skipped:
        result_parts.append(f"NOTE: {len(skipped)} attachment(s) could not be read:")
        for doc in skipped:
            result_parts.append(f"  - #{doc['num']} {doc['name']}: {doc.get('text', 'unknown reason')}")
        result_parts.append("These may be image-only documents (maps, photos) or very large files. The user should be informed.\n")

    for doc in processed:
        if doc.get("tokens", 0) == 0:
            result_parts.append(f"=== Attachment {doc['num']}: {doc['name']} ===")
        elif doc.get("cached"):
            result_parts.append(f"=== Attachment {doc['num']}: {doc['name']} ({doc.get('pages', '?')} pages) [CACHED] ===")
        elif doc.get("category") == "summarized":
            result_parts.append(f"=== Attachment {doc['num']}: {doc['name']} ({doc.get('pages', '?')} pages, ~{doc['tokens']} tokens) [SUMMARIZED] ===")
        else:
            result_parts.append(f"=== Attachment {doc['num']}: {doc['name']} ({doc.get('pages', '?')} pages, ~{doc['tokens']} tokens) ===")
        result_parts.append(doc.get("text", "[No text]"))
        result_parts.append("")

    if unpublished:
        result_parts.append("=== Unpublished attachments (not accessible) ===")
        for att in unpublished:
            result_parts.append(f"  {att['num']}. {att['name']} - not published online")

    return "\n".join(result_parts)


@tool(approval_mode="never_require")
def search_strategy(
    query: Annotated[str, Field(description="The search query describing what strategic content to find")],
) -> str:
    """Search Helsinki City Strategy 2025-2029 document for relevant content. Use when the user asks about city strategy, goals, priorities, or when you want to relate a meeting topic to strategic objectives."""
    credential = _get_credential()

    # Try vector embedding (best quality), fall back to text-only
    query_vector = None
    try:
        token_provider = get_bearer_token_provider(credential, "https://cognitiveservices.azure.com/.default")
        token = token_provider()
        embed_resp = httpx.post(
            f"{AI_ENDPOINT}/models/embeddings?api-version=2024-05-01-preview",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"input": [query], "input_type": "query", "model": EMBEDDING_DEPLOYMENT},
            timeout=10,
        )
        if embed_resp.status_code == 200:
            query_vector = embed_resp.json()["data"][0]["embedding"]
    except Exception:
        pass

    # Search
    search_token_provider = get_bearer_token_provider(credential, "https://search.azure.com/.default")
    search_token = search_token_provider()

    search_body = {
        "search": query,
        "queryType": "semantic",
        "semanticConfiguration": "default-semantic",
        "top": 5,
        "select": "content,page,source",
    }
    if query_vector:
        search_body["vectorQueries"] = [
            {"kind": "vector", "vector": query_vector, "fields": "content_vector", "k": 5}
        ]

    search_resp = httpx.post(
        f"{SEARCH_ENDPOINT}/indexes/strategy-2025-2029/docs/search?api-version=2024-07-01",
        headers={"Authorization": f"Bearer {search_token}", "Content-Type": "application/json"},
        json=search_body,
        timeout=15,
    )
    search_resp.raise_for_status()
    results = search_resp.json().get("value", [])

    if not results:
        return f"No results found in the city strategy for: {query}"

    passages = []
    for r in results:
        passages.append(f"[Page {r.get('page', '?')}] {r.get('content', '')}")

    return (
        f"Helsinki City Strategy 2025-2029 - search results for '{query}' "
        f"(vector={'yes' if query_vector else 'no'}):\n\n"
        + "\n\n---\n\n".join(passages)
    )


INSTRUCTIONS = """You are a Helsinki municipal meeting briefing assistant. You help users understand 
meetings in the Ahjo decision-making system and relate them to Helsinki's City Strategy 2025-2029.

Your capabilities:
- List meetings from the Ahjo calendar API (both past and upcoming)
- Get meeting agenda items (decisions) for a specific meeting
- List attachments for a specific agenda item/decision
- Read and extract content from published PDF attachments (async processing)
- Check progress and retrieve results of attachment processing
- Search Helsinki City Strategy 2025-2029 for strategic context on any topic

Date interpretation:
- When the user mentions a month without a year (e.g. "May", "toukokuu"), assume the current year.
- For past meetings, use days_back parameter in get_meetings to search backwards.
- Calculate the correct days_back/days_ahead values based on today's date and the target period.
- Example: if today is July 3 and user says "May meetings", use days_back=64 (to cover May 1) with days_ahead=0.

Finding specific topics across meetings:
- When the user asks about a topic (e.g. "Hylkysaari"), first use get_meetings with appropriate date range.
- Then check relevant committees' agendas for items matching the topic.
- Kaupunkiymparistolautakunta (U540) handles land use, zoning, construction, and urban environment.
- Kaupunginhallitus (00400) handles city-wide decisions and proposals to council.
- Kaupunginvaltuusto (02900) handles final approvals for major decisions.

Workflow for summarizing agenda items with attachments:
1. Use get_meeting_agenda to get the agenda with case URLs
2. Use get_item_attachments to see what attachments exist and their availability
3. Use read_attachment with the case URL to START processing. This sends the work to a dedicated processing service.
4. If read_attachment returns results immediately (cached/fast), proceed to step 6.
5. If read_attachment returns a job_id, call get_attachment_results with that job_id to check progress. Repeat until complete.
6. Synthesize findings: summarize in complete sentences, note contradictions, cite by attachment number
7. Optionally use search_strategy if the user wants connections to city strategy

CRITICAL: When the user provides a URL and says to read attachments, ONLY call read_attachment. Do NOT call get_item_attachments or any other tool first.

Attachment processing pattern:
- read_attachment sends heavy PDF processing to a separate service with more resources.
- If results come back immediately (all cached or fast processing), use them directly.
- If you get a job_id, tell the user processing has started and call get_attachment_results to check.
- get_attachment_results returns either progress (X/Y done, Z%) or final results.
- Keep checking get_attachment_results until status is "completed".

When a user asks about meetings (past or upcoming), use get_meetings with appropriate days_back/days_ahead.
When they want details about a specific meeting, use get_meeting_agenda with the policymaker code and meeting_date.
When they ask about attachments for an agenda item, use get_item_attachments with the case URL from the agenda output.
When they want a summary that considers attachments, use read_attachment to start processing.
When the user provides a case URL directly and asks to read attachments, call read_attachment IMMEDIATELY without other tools.
When relating topics to city strategy or goals, use search_strategy to find relevant passages.

Important rules:
- Only use the item's own materials in summaries. If you add context from elsewhere, state it clearly.
- Note contradictions between attachments or between attachments and the main proposal.
- Strategy grounding is optional - only connect to strategy when the user requests it.
- Unpublished attachments cannot be read. Acknowledge they exist and note the count.
- Write executive summaries in complete sentences, not bullet lists, unless the user requests otherwise.
- Always respond in the same language the user uses. If they write in Finnish, respond in Finnish.
- When processing multiple attachments, inform the user of progress.
- Large attachments are automatically summarized by an internal LLM to preserve all factual content.
- Previously processed PDFs are cached and load instantly on subsequent requests.
- Some attachments cannot be processed (too large, image-only maps/photos, or scanned documents). When this happens, ALWAYS explicitly tell the user which attachments were skipped and why. Do not silently omit them."""


def main():
    client = FoundryChatClient(
        project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
        model=os.environ["AZURE_AI_MODEL_DEPLOYMENT_NAME"],
        credential=DefaultAzureCredential(),
    )

    agent = Agent(
        client=client,
        instructions=INSTRUCTIONS,
        tools=[get_meetings, get_meeting_agenda, get_item_attachments, check_attachment_cache, read_attachment, get_attachment_results, search_strategy],
        default_options={"store": False, "max_tokens": 65_536},
    )

    server = ResponsesHostServer(agent)
    server.run()


if __name__ == "__main__":
    main()
