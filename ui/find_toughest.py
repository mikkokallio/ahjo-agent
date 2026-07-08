"""Find the meeting with the most/biggest text-heavy attachments."""
import asyncio
import sys
import httpx

sys.stdout.reconfigure(line_buffering=True)

base = "http://127.0.0.1:8080"


async def fetch_agenda(client, meeting_link):
    try:
        resp = await client.get(f"{base}/api/agenda", params={"meeting_link": meeting_link})
        return resp.json().get("items", [])
    except Exception:
        return []


async def fetch_attachments(client, case_url):
    try:
        resp = await client.get(f"{base}/api/attachments", params={"case_url": case_url})
        return resp.json().get("attachments", [])
    except Exception:
        return []


async def analyze_meeting(client, m, idx, total):
    link = m.get("meeting_link", "")
    if not link:
        return None

    items = await fetch_agenda(client, link)
    if not items:
        print(f"  [{idx}/{total}] {m['date']} {m.get('policymaker_name', '')[:35]}: no agenda")
        return None

    # Fetch all attachments in parallel
    case_urls = [it.get("case_url", "") for it in items if it.get("case_url")]
    tasks = [fetch_attachments(client, url) for url in case_urls]
    att_results = await asyncio.gather(*tasks)

    total_tokens = 0
    total_pages = 0
    total_attachments = 0
    cached_count = 0

    for atts in att_results:
        for att in atts:
            if not att.get("published"):
                continue
            total_attachments += 1
            if att.get("cached"):
                cached_count += 1
                total_tokens += att.get("tokens") or 0
                total_pages += att.get("pages") or 0

    name = m.get("policymaker_name", "")[:35]
    print(f"  [{idx}/{total}] {m['date']} {name}: {len(items)} items, {total_attachments} atts, {cached_count} cached, {total_tokens} tok")

    return {
        "date": m["date"],
        "policymaker_name": m.get("policymaker_name", ""),
        "meeting_link": link,
        "agenda_items": len(items),
        "attachments": total_attachments,
        "cached_attachments": cached_count,
        "total_tokens": total_tokens,
        "total_pages": total_pages,
    }


async def main():
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(f"{base}/api/meetings?days_back=30&days_ahead=14")
        meetings_by_date = resp.json()

    all_meetings = []
    for date, mlist in meetings_by_date.items():
        for m in mlist:
            m["date"] = date
            all_meetings.append(m)

    print(f"Total meetings: {len(all_meetings)}")

    # Process meetings sequentially (each one parallelizes its attachments internally)
    results = []
    async with httpx.AsyncClient(timeout=60) as client:
        for i, m in enumerate(all_meetings):
            r = await analyze_meeting(client, m, i + 1, len(all_meetings))
            if r:
                results.append(r)

    # Sort by total attachments (primary) and tokens (secondary)
    results.sort(key=lambda x: (x["attachments"], x["total_tokens"]), reverse=True)

    print("\n=== TOP 5 MEETINGS BY ATTACHMENTS + TEXT ===")
    for r in results[:5]:
        print(f"  {r['date']} | {r['policymaker_name'][:40]}")
        print(f"    {r['agenda_items']} items | {r['attachments']} atts | {r['cached_attachments']} cached | {r['total_tokens']} tokens | {r['total_pages']} pages")
        print(f"    {r['meeting_link']}")
        print()


asyncio.run(main())
