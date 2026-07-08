import sqlite3, json

conn = sqlite3.connect("cache.db")

# Check kaupunginhallitus - the text-heavy one
agenda_row = conn.execute(
    "SELECT items_json FROM agenda_cache WHERE meeting_link LIKE '%00400202622%'"
).fetchone()

if agenda_row:
    items = json.loads(agenda_row[0])
    case_urls = [it.get("case_url", "") for it in items if it.get("case_url")]
    print(f"Kaupunginhallitus: {len(items)} agenda items, {len(case_urls)} with case_url")
    print()

    total_tok = 0
    total_pages = 0
    for url in case_urls:
        row = conn.execute(
            "SELECT attachments_json FROM attachments_cache WHERE case_url = ?", (url,)
        ).fetchone()
        if row:
            atts = json.loads(row[0])
            published = [a for a in atts if a.get("published")]
            cached = [a for a in published if a.get("cached")]
            if cached:
                tok = sum(a.get("tokens", 0) or 0 for a in cached)
                pgs = sum(a.get("pages", 0) or 0 for a in cached)
                total_tok += tok
                total_pages += pgs
                print(f"{url}")
                for a in cached:
                    name = a["name"][:50]
                    cat = a.get("category", "?")
                    t = a.get("tokens", 0)
                    p = a.get("pages", 0)
                    print(f"  {a['num']}. {name} | {cat} | {t} tok, {p} p")
                print()

    print(f"TOTAL: {total_tok} tokens, {total_pages} pages")

