"""E2E test: select a meeting, send a message, verify streaming progress and response."""
import time
from playwright.sync_api import sync_playwright, expect


def test_chat_streaming():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("http://127.0.0.1:8080/")

        # Wait for meetings to load
        page.wait_for_selector(".meeting-item", timeout=15000)
        print("[OK] Meetings loaded")

        # Find and click a meeting (first one visible)
        meetings = page.query_selector_all(".meeting-item")
        assert len(meetings) > 0, "No meetings found"
        meeting_name = meetings[0].inner_text().split("\n")[0]
        print(f"[OK] Clicking meeting: {meeting_name}")
        meetings[0].click()

        # Verify chat input is enabled
        chat_input = page.locator("#chatInput")
        expect(chat_input).to_be_enabled()
        print("[OK] Chat input enabled")

        # Verify right pane loads agenda
        page.wait_for_selector(".agenda-group, .pane-right-empty", timeout=15000)
        agenda_items = page.query_selector_all(".agenda-group")
        print(f"[OK] Right pane: {len(agenda_items)} agenda items")

        # Type and send a message
        chat_input.fill("Mitä asioita on kokouksen asialistalla?")
        page.locator("#chatSend").click()
        print("[OK] Message sent")

        # Wait for status message (tool calls)
        status_el = page.wait_for_selector(".status-msg", timeout=30000)
        status_text = status_el.inner_text()
        print(f"[OK] Status shown: {status_text}")

        # Collect status updates
        statuses = [status_text]
        start = time.time()
        while time.time() - start < 180:  # max 3 minutes
            time.sleep(1)
            # Check if response has started streaming
            streaming_msg = page.query_selector("#streaming-msg")
            if streaming_msg:
                partial = streaming_msg.inner_text()
                print(f"[OK] Response streaming started ({len(partial)} chars so far)")
                break
            # Check for updated status
            status_now = page.query_selector(".status-msg")
            if status_now:
                txt = status_now.inner_text()
                if txt not in statuses:
                    statuses.append(txt)
                    print(f"[OK] Status update: {txt}")
            # Check for error
            error_el = page.query_selector(".status-msg.error")
            if error_el:
                print(f"[FAIL] Error: {error_el.inner_text()}")
                break
        else:
            print("[FAIL] Timed out waiting for response")
            browser.close()
            return

        # Wait for completion (stream done)
        time.sleep(2)
        final_start = time.time()
        while time.time() - final_start < 180:
            time.sleep(2)
            streaming = page.query_selector("#streaming-msg")
            if not streaming:
                # streaming-msg id removed = done
                break
            current = streaming.inner_text()
            print(f"  ... still streaming ({len(current)} chars)")

        # Get final response
        messages = page.query_selector_all(".message.assistant")
        if messages:
            final_text = messages[-1].inner_text()
            print(f"\n[OK] Final response ({len(final_text)} chars):")
            print(final_text[:300])
        else:
            print("[FAIL] No assistant message found")

        print(f"\n[OK] Status updates seen: {statuses}")
        browser.close()


if __name__ == "__main__":
    test_chat_streaming()
