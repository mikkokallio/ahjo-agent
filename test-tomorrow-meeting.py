"""Map tomorrow's meeting from calendar API to decision content."""
import urllib.request, json, re

# The meeting from calendar API
url = "https://paatokset.hel.fi/ahjo_api/case/hel-2026-006394/646b2890-775e-c535-97b6-9f1cb1900000"
with urllib.request.urlopen(url) as r:
    data = json.loads(r.read())

print("=== Decision JSON structure ===")
print(f"Top-level keys: {list(data.keys())}")
print(f"Content: {len(data.get('content',''))} chars of HTML")
print(f"Decision PDF: {data.get('decision_pdf','')}")
print(f"all_decisions_link: {data.get('all_decisions_link','')}")
print(f"show_warning: {data.get('show_warning')}")

# Try to find the title in the HTML
content = data.get("content", "")
h2 = re.search(r'<h2[^>]*>(.*?)</h2>', content)
title_class = re.search(r'issue-content-top__title[^>]*>([^<]+)', content)
motion_tag = re.search(r'<span>(.*?)</span>', content)
print(f"\nh2 match: {h2.group(1) if h2 else 'none'}")
print(f"title_class match: {title_class.group(1).strip() if title_class else 'none'}")
print(f"motion_tag: {motion_tag.group(1) if motion_tag else 'none'}")

# Check first 500 chars of content
print(f"\nContent preview:\n{content[:500]}")

# Attachments
att_html = data.get("attachments", "")
att_urls = re.findall(r'https://ahjojulkaisu\.hel\.fi/[A-F0-9-]+\.\w+', att_html)
print(f"\n=== Attachments: {len(att_urls)} ===")
for a in att_urls:
    print(f"  {a}")

# Now do the same for the other 2 decisions
print("\n=== All 3 decisions from tomorrow's meeting ===")
cases = [
    ("hel-2026-006394", "646b2890-775e-c535-97b6-9f1cb1900000"),
    ("hel-2025-018125", "32944c48-1a60-cf04-a451-9f1cb1a00004"),
    ("hel-2024-017145", "d3afd318-fd91-c0a7-85b9-9f1cb1c00002"),
]
for case_id, dec_id in cases:
    url = f"https://paatokset.hel.fi/ahjo_api/case/{case_id}/{dec_id}"
    with urllib.request.urlopen(url) as r:
        d = json.loads(r.read())
    c = d.get("content", "")
    # Try multiple title patterns
    h2 = re.search(r'<h2[^>]*>(.*?)</h2>', c)
    title = h2.group(1) if h2 else "(no h2)"
    atts = re.findall(r'https://ahjojulkaisu\.hel\.fi/[A-F0-9-]+\.\w+', d.get("attachments",""))
    pdf = d.get("decision_pdf", "")
    print(f"\n  {case_id}:")
    print(f"    Title: {title}")
    print(f"    Content: {len(c)} chars")
    print(f"    Attachments: {len(atts)}")
    print(f"    Decision PDF: {pdf}")
