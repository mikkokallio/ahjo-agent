"""Deterministic Playwright coverage for rendering untrusted Ahjo fields."""

import json

from playwright.sync_api import expect, sync_playwright


HOSTILE = '<img src=x onerror="window.__xssExecuted=true">'
CASE_URL = "https://paatokset.hel.fi/fi/asia/test"


def json_route(payload):
    def handler(route):
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

    return handler


def test_upstream_content_is_text_and_external_pdf_is_blocked():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.add_init_script("window.__xssExecuted = false")
        page.route(
            "**/api/meetings?*",
            json_route({"2026-04-01": [{
                "date": "2026-04-01",
                "policymaker": "test' onclick='window.__xssExecuted=true",
                "policymaker_name": HOSTILE,
                "start_time": HOSTILE,
                "meeting_link": CASE_URL,
            }]}),
        )
        page.route(
            "**/api/agenda?*",
            json_route({"items": [{"title": f"1 {HOSTILE}", "case_url": CASE_URL}]}),
        )
        page.route(
            "**/api/attachments-bulk",
            json_route({"results": {CASE_URL: [{
                "num": 1,
                "name": HOSTILE,
                "published": True,
                "cached": False,
                "pdf_url": "https://example.com/hostile.pdf",
            }]}}),
        )

        page.goto("http://127.0.0.1:8080/")
        meeting = page.locator(".meeting-item")
        expect(meeting).to_contain_text(HOSTILE)
        meeting.click()
        agenda = page.locator(".agenda-header")
        expect(agenda).to_contain_text(HOSTILE)
        agenda.click()
        attachment = page.locator(".attachment-item")
        expect(attachment).to_contain_text(HOSTILE)
        attachment.click()

        expect(page.locator("#attachmentModal")).to_have_class("modal-overlay open")
        expect(page.locator("#modal-pdf-link")).to_have_count(0)
        expect(page.locator("img")).to_have_count(0)
        assert page.evaluate("window.__xssExecuted") is False
        browser.close()


if __name__ == "__main__":
    test_upstream_content_is_text_and_external_pdf_is_blocked()