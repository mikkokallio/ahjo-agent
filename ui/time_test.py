import time
import httpx

link = "/fi/paattajat/kaupunginhallitus/asiakirjat/00400202622"

# Use a persistent client to eliminate connection overhead
with httpx.Client(base_url="http://127.0.0.1:8080") as client:
    # Warm up connection
    client.get("/")

    # Test agenda
    t0 = time.time()
    resp = client.get("/api/agenda", params={"meeting_link": link})
    t1 = time.time()
    data = resp.json()
    print(f"Agenda (persistent client): {len(data.get('items',[]))} items, cached={data.get('cached')}, took {(t1-t0)*1000:.0f}ms")

    # Test again immediately
    t0 = time.time()
    resp = client.get("/api/agenda", params={"meeting_link": link})
    t1 = time.time()
    data = resp.json()
    print(f"Agenda (2nd call): {len(data.get('items',[]))} items, cached={data.get('cached')}, took {(t1-t0)*1000:.0f}ms")

    # Test attachments
    items_with_url = [it for it in data["items"] if it.get("case_url")]
    if items_with_url:
        case_url = items_with_url[0]["case_url"]
        t0 = time.time()
        resp2 = client.get("/api/attachments", params={"case_url": case_url})
        t1 = time.time()
        data2 = resp2.json()
        print(f"Attachments[0]: {len(data2.get('attachments',[]))} atts, cached={data2.get('cached')}, took {(t1-t0)*1000:.0f}ms")

        if len(items_with_url) > 1:
            case_url2 = items_with_url[1]["case_url"]
            t0 = time.time()
            resp3 = client.get("/api/attachments", params={"case_url": case_url2})
            t1 = time.time()
            data3 = resp3.json()
            print(f"Attachments[1]: {len(data3.get('attachments',[]))} atts, cached={data3.get('cached')}, took {(t1-t0)*1000:.0f}ms")
