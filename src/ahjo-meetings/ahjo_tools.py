"""Ahjo API tools for the meeting briefing agent.

These are deterministic Python functions that call Helsinki's public Ahjo APIs.
PDF extraction is done with pdfplumber - no LLM needed for structured data retrieval.
"""

import os
import re
import httpx
import pdfplumber
import io
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from azure.identity import DefaultAzureCredential, get_bearer_token_provider

BASE_URL = "https://paatokset.hel.fi"
SEARCH_ENDPOINT = "https://search-ahjo-agent.search.windows.net"
AI_ENDPOINT = "https://ai-account-mapx4sxw6thvg.services.ai.azure.com"
EMBEDDING_DEPLOYMENT = "embed-v-4-0"

# Tool definitions for the Foundry agent
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_meetings",
            "description": "Get upcoming meetings from Helsinki Ahjo system. Returns meeting list with dates, organizations, and IDs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "days_ahead": {
                        "type": "integer",
                        "description": "Number of days ahead to look for meetings. Default 30.",
                        "default": 30,
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_meeting_agenda",
            "description": "Get the agenda (list of decisions/items) for a specific meeting. Requires the meeting page URL from get_meetings.",
            "parameters": {
                "type": "object",
                "properties": {
                    "meeting_url": {
                        "type": "string",
                        "description": "The meeting page URL (minutes_link from get_meetings result)",
                    }
                },
                "required": ["meeting_url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_decision_content",
            "description": "Get the full content of a specific decision, including its text and list of attachments.",
            "parameters": {
                "type": "object",
                "properties": {
                    "case_id": {
                        "type": "string",
                        "description": "The case ID (e.g. 'HEL 2024-012345')",
                    },
                    "decision_id": {
                        "type": "string",
                        "description": "The decision ID from the agenda",
                    },
                },
                "required": ["case_id", "decision_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "extract_pdf_text",
            "description": "Download and extract text from a PDF attachment. Returns the full text content. Use for summarization.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pdf_url": {
                        "type": "string",
                        "description": "The URL of the PDF to extract text from",
                    },
                    "max_pages": {
                        "type": "integer",
                        "description": "Maximum pages to extract. Default 20. Use lower for quick summaries.",
                        "default": 20,
                    },
                },
                "required": ["pdf_url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_strategy",
            "description": "Search Helsinki City Strategy 2025-2029 document for relevant content. Use when the user asks about city strategy, goals, priorities, or when you want to relate a meeting topic to strategic objectives.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query describing what strategic content to find",
                    }
                },
                "required": ["query"],
            },
        },
    },
]


def get_meetings(days_ahead: int = 30) -> dict:
    """Fetch upcoming meetings from the Ahjo calendar API."""
    from_date = datetime.now().strftime("%Y-%m-%d")

    url = f"{BASE_URL}/fi/ahjo_api/meetings"
    params = {"from": from_date}

    with httpx.Client(timeout=30) as client:
        resp = client.get(url, params=params)
        resp.raise_for_status()
        raw = resp.json()

    # API returns {"data": {"2026-07-03": [meeting_obj, ...], ...}}
    data = raw.get("data", raw) if isinstance(raw, dict) else {}

    meetings = []
    target_date = (datetime.now() + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    for date_str, day_meetings in (data.items() if isinstance(data, dict) else []):
        if date_str > target_date:
            continue
        for m in day_meetings:
            if m.get("meeting_cancelled"):
                continue
            meetings.append({
                "date": date_str,
                "time": m.get("start_time", ""),
                "title": m.get("title", ""),
                "organization": m.get("policymaker_name", "Unknown"),
                "policymaker_id": m.get("policymaker", ""),
                "status": m.get("status", ""),
                "agenda_link": m.get("motions_list_link", ""),
            })

    meetings.sort(key=lambda x: x["date"])
    return {"count": len(meetings), "meetings": meetings[:50]}


def get_meeting_agenda(meeting_url: str) -> dict:
    """Scrape the meeting page to get agenda items (decisions).
    
    The Ahjo system has NO JSON API for agenda items per meeting.
    We must scrape the HTML meeting page - it's well-structured.
    """
    if not meeting_url.startswith("http"):
        meeting_url = BASE_URL + meeting_url

    with httpx.Client(timeout=30, follow_redirects=True) as client:
        resp = client.get(meeting_url)
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")

    # Find agenda items - they're in links with case IDs
    items = []
    # Pattern: links containing case references like /fi/paattajat/.../asiakirjat/...
    for link in soup.find_all("a", href=re.compile(r"/asiakirjat/")):
        href = link.get("href", "")
        text = link.get_text(strip=True)
        if text and href:
            items.append({"title": text, "url": href})

    # Also try to find case IDs in the page
    case_pattern = re.compile(r"HEL \d{4}-\d+")
    case_ids = case_pattern.findall(resp.text)

    return {
        "meeting_url": meeting_url,
        "agenda_items": items[:30],
        "case_ids_found": list(set(case_ids))[:30],
    }


def get_decision_content(case_id: str, decision_id: str) -> dict:
    """Fetch decision content from the Ahjo JSON API."""
    # Normalize case_id format for URL
    case_id_url = case_id.replace(" ", "%20")

    url = f"{BASE_URL}/ahjo_api/case/{case_id_url}/{decision_id}"

    with httpx.Client(timeout=30, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
        data = resp.json()

    # Extract key content
    content_html = data.get("content", "")
    attachments_html = data.get("attachments", "")

    # Parse content to plain text
    content_text = BeautifulSoup(content_html, "html.parser").get_text(separator="\n", strip=True)

    # Extract title from content (in <h2>)
    title_match = re.search(r"<h2>(.*?)</h2>", content_html)
    title = title_match.group(1) if title_match else "Unknown"

    # Parse attachments list
    attachments = []
    if attachments_html:
        att_soup = BeautifulSoup(attachments_html, "html.parser")
        for link in att_soup.find_all("a", href=True):
            attachments.append({
                "name": link.get_text(strip=True),
                "url": link["href"],
            })

    return {
        "title": title,
        "decision_pdf": data.get("decision_pdf", ""),
        "content_text": content_text[:3000],  # Cap for token management
        "attachments": attachments,
        "all_decisions_link": data.get("all_decisions_link", ""),
    }


def extract_pdf_text(pdf_url: str, max_pages: int = 20) -> dict:
    """Download a PDF and extract its text content using pdfplumber."""
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        resp = client.get(pdf_url)
        resp.raise_for_status()
        pdf_bytes = resp.content

    text_pages = []
    total_chars = 0

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        num_pages = len(pdf.pages)
        pages_to_read = min(num_pages, max_pages)

        for i in range(pages_to_read):
            page_text = pdf.pages[i].extract_text() or ""
            text_pages.append(page_text)
            total_chars += len(page_text)

    full_text = "\n\n".join(text_pages)

    # If text is very long, truncate intelligently
    if len(full_text) > 15000:
        full_text = full_text[:15000] + "\n\n[... truncated, document continues ...]"

    return {
        "total_pages": num_pages,
        "pages_extracted": pages_to_read,
        "char_count": total_chars,
        "text": full_text,
    }


# Lazy credential for search operations
_credential = None


def _get_credential():
    global _credential
    if _credential is None:
        _credential = DefaultAzureCredential()
    return _credential


def search_strategy(query: str) -> dict:
    """Search Helsinki City Strategy 2025-2029 using hybrid vector + semantic search."""
    credential = _get_credential()

    # Try to get query embedding (best results), fall back to text-only if slow
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
        pass  # Fall back to text-only search

    # Search with text + semantic reranking (+ vector if available)
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
            {
                "kind": "vector",
                "vector": query_vector,
                "fields": "content_vector",
                "k": 5,
            }
        ]

    search_resp = httpx.post(
        f"{SEARCH_ENDPOINT}/indexes/strategy-2025-2029/docs/search?api-version=2024-07-01",
        headers={"Authorization": f"Bearer {search_token}", "Content-Type": "application/json"},
        json=search_body,
        timeout=15,
    )
    search_resp.raise_for_status()
    results = search_resp.json().get("value", [])

    passages = []
    for r in results:
        passages.append({
            "content": r.get("content", ""),
            "page": r.get("page"),
            "score": r.get("@search.score"),
        })

    return {
        "source": "Helsinki City Strategy 2025-2029 (Kaupunkistrategia)",
        "query": query,
        "results_count": len(passages),
        "vector_search_used": query_vector is not None,
        "passages": passages,
    }


def handle_tool_call(function_name: str, arguments: dict) -> dict:
    """Route a tool call to the appropriate function."""
    handlers = {
        "get_meetings": get_meetings,
        "get_meeting_agenda": get_meeting_agenda,
        "get_decision_content": get_decision_content,
        "extract_pdf_text": extract_pdf_text,
        "search_strategy": search_strategy,
    }

    handler = handlers.get(function_name)
    if not handler:
        return {"error": f"Unknown tool: {function_name}"}

    try:
        return handler(**arguments)
    except httpx.HTTPStatusError as e:
        return {"error": f"HTTP {e.response.status_code}: {str(e)}"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {str(e)}"}
