"""Test which endpoints cover which steps."""
import urllib.request, json, re

print("=== STEP 1: List meetings ===")
print("  /fi/ahjo_api/meetings?from=DATE -> returns JSON with meetings list")
print("  Each entry has: minutes_link (for held) or motions_list_link (for upcoming)")
print("  COVERED BY: ahjo_api")
print()

print("=== STEP 2: Get agenda items for a specific meeting ===")
print("Testing if there's a JSON API for listing agenda items...")
print()

# Try various ahjo_api paths
tests = [
    ("https://paatokset.hel.fi/fi/ahjo_api/meeting/00400202624", "ahjo_api/meeting/ID"),
    ("https://paatokset.hel.fi/ahjo_api/meeting/00400202624", "ahjo_api/meeting/ID (no lang)"),
    ("https://paatokset.hel.fi/fi/ahjo_api/agenda/00400202624", "ahjo_api/agenda/ID"),
    ("https://paatokset.hel.fi/fi/ahjo_api/cases/00400202624", "ahjo_api/cases/ID"),
]
for url, label in tests:
    try:
        req = urllib.request.Request(url, method="GET")
        req.add_header("Accept", "application/json")
        with urllib.request.urlopen(req, timeout=5) as r:
            ct = r.headers.get("content-type", "")
            body = r.read(500).decode("utf-8", errors="replace")
            print(f"  {r.status} {label} -> {ct} | {body[:100]}")
    except urllib.error.HTTPError as e:
        print(f"  {e.code} {label}")
    except Exception as e:
        print(f"  TIMEOUT/ERR {label} -> {type(e).__name__}")

print()
print("=== STEP 3: Get decision content ===")
print("  /ahjo_api/case/CASEID/DECISIONID -> returns JSON with content, attachments, decision_pdf")
print("  COVERED BY: ahjo_api")
print()

# Check what the decision JSON's all_decisions_link gives us
print("=== Can we get agenda items from within a decision JSON? ===")
url = "https://paatokset.hel.fi/ahjo_api/case/hel-2026-008871/5f4f3fee-b094-48ff-8c8b-b697527a8197"
with urllib.request.urlopen(url) as r:
    dec = json.loads(r.read())
print(f"  all_decisions_link: {dec.get('all_decisions_link','')}")
print(f"  other_decisions_link: {dec.get('other_decisions_link','')}")
nav = dec.get("decision_navigation", "")
nav_links = re.findall(r'href="([^"]+)"', nav)
print(f"  decision_navigation contains {len(nav_links)} links")
for l in nav_links[:3]:
    print(f"    {l}")
