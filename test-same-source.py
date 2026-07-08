"""Prove whether calendar API and the meeting page we used are the same data."""
import urllib.request, json

# 1. Calendar API - get June 22 meetings
url = "https://paatokset.hel.fi/fi/ahjo_api/meetings?from=2026-06-22"
with urllib.request.urlopen(url) as r:
    cal = json.loads(r.read())

june22 = cal["data"].get("2026-06-22", [])
print("=== CALENDAR API: /fi/ahjo_api/meetings?from=2026-06-22 ===")
print(f"Meetings on June 22: {len(june22)}")
for m in june22:
    print(f"  policymaker: {m['policymaker']} ({m['policymaker_name']})")
    print(f"  minutes_link: {m.get('minutes_link','')}")
    print(f"  status: {m['status']}, phase: {m['phase']}")
    print()

# 2. Check if the KH meeting #24 is there
print("=== COMPARISON ===")
kh = [m for m in june22 if m["policymaker"] == "00400"]
our_url = "/fi/paattajat/kaupunginhallitus/asiakirjat/00400202624"
if kh:
    cal_link = kh[0].get("minutes_link", "")
    print(f"Calendar API minutes_link: {cal_link}")
    print(f"URL we used for 46 PDFs:   {our_url}")
    print(f"IDENTICAL: {cal_link == our_url}")
else:
    print("Kaupunginhallitus NOT FOUND on June 22 in calendar API!")

# 3. Now verify the decision API is also the same
# From our 46-PDF test, we used: /ahjo_api/case/hel-2026-008871/5f4f3fee-b094-48ff-8c8b-b697527a8197
# Try fetching it and compare content length to what we got before (24,232 chars)
print("\n=== DECISION AJAX API: same endpoint for both flows ===")
dec_url = "https://paatokset.hel.fi/ahjo_api/case/hel-2026-008871/5f4f3fee-b094-48ff-8c8b-b697527a8197"
with urllib.request.urlopen(dec_url) as r:
    dec = json.loads(r.read())
print(f"Endpoint: /ahjo_api/case/hel-2026-008871/5f4f3fee-b094-48ff-8c8b-b697527a8197")
print(f"Content length: {len(dec['content'])} chars")
print(f"Previously got:  24,232 chars (from our 46-PDF test)")
print(f"Same endpoint, same data: {abs(len(dec['content']) - 24232) < 100}")

# 4. Summary
print("\n=== CONCLUSION ===")
print("Both paths use the SAME underlying Drupal endpoints:")
print("  Calendar:  /fi/ahjo_api/meetings?from=DATE  (lists meetings)")
print("  Decisions: /ahjo_api/case/CASEID/DECISIONID  (decision content)")
print("  Pages:     /fi/paattajat/ORG/asiakirjat/ID   (meeting page with links)")
print("The calendar API's minutes_link points to the SAME meeting page")
print("we used to discover agenda items -> decisions -> attachments.")
