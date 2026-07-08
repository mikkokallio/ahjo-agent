"""Download decision HTML for meeting 00400202624 and save to output folder."""
import json
import os
import urllib.request

base_url = "https://paatokset.hel.fi"
out_dir = r"output\test-00400202624\decisions"
os.makedirs(out_dir, exist_ok=True)

# Load the meeting JSON to get case/decision IDs
with open(r"output\test-00400202624\meeting.json", "r", encoding="utf-8") as f:
    meeting = json.load(f)

print(f"Downloading decision HTML for {len(meeting['decisions'])} decisions...\n")

for d in meeting["decisions"]:
    case_id = d["CaseId"]
    decision_id = d["DecisionId"]
    url = f"{base_url}/ahjo_api/case/{case_id}/{decision_id}"
    
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        
        # Save raw JSON response
        out_path = os.path.join(out_dir, f"{case_id}.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        # Extract and save just the HTML content
        content = data.get("content", data.get("Content", ""))
        html_path = os.path.join(out_dir, f"{case_id}.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(content)
        
        print(f"  {case_id}: {len(content):,} chars HTML")
    except Exception as e:
        print(f"  {case_id}: ERROR - {e}")

print(f"\nDone. Files saved to {out_dir}")
print(f"Total files: {len(os.listdir(out_dir))}")
