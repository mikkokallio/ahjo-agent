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


def test_chat_stream_error_remains_visible():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.route(
            "**/api/meetings?*",
            json_route({"2026-04-01": [{
                "date": "2026-04-01",
                "policymaker": "00400",
                "policymaker_name": "Helsingin kaupunginhallitus",
                "meeting_link": CASE_URL,
            }]}),
        )
        page.route("**/api/agenda?*", json_route({"items": []}))
        page.route("**/api/attachments-bulk", json_route({"results": {}}))
        page.route(
            "**/api/chat",
            lambda route: route.fulfill(
                status=200,
                content_type="text/event-stream",
                body=(
                    'data: {"type":"status","text":"Käsitellään..."}\n\n'
                    'data: {"type":"error","text":"Agent returned 500"}\n\n'
                ),
            ),
        )

        page.goto("http://127.0.0.1:8080/")
        page.locator(".meeting-item").click()
        page.locator("#chatInput").fill("Mitä kokouksessa käsitellään?")
        page.locator("#chatSend").click()

        expect(page.locator("#chatMessages")).to_contain_text("Virhe: Agent returned 500")
        expect(page.locator("#chatMessages")).not_to_contain_text("Ei vastausta")
        browser.close()


def test_compact_pane_controls_and_light_theme():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1050, "height": 800})
        page.route(
            "**/api/meetings?*",
            json_route({"2026-04-01": [{
                "date": "2026-04-01",
                "policymaker": "00400",
                "policymaker_name": "Helsingin kaupunginhallitus",
                "meeting_link": CASE_URL,
            }]}),
        )
        page.route("**/api/agenda?*", json_route({"items": []}))
        page.route("**/api/attachments-bulk", json_route({"results": {}}))

        page.goto("http://127.0.0.1:8080/")
        expect(page.locator("#toggleLeft")).to_be_visible()
        expect(page.locator("#toggleRight")).to_be_visible()

        page.locator("#toggleLeft").click()
        expect(page.locator("#app")).to_have_class("left-open")
        expect(page.locator(".pane-left .meeting-item")).to_contain_text("Helsingin kaupunginhallitus")
        page.locator(".pane-left .pane-toggle").click()
        expect(page.locator("#app")).not_to_have_class("left-open")

        page.locator("#toggleRight").click()
        expect(page.locator("#app")).to_have_class("right-open")
        expect(page.locator(".pane-right")).to_contain_text("Valitse kokous")
        page.locator(".pane-right .pane-toggle").click()
        expect(page.locator("#app")).not_to_have_class("right-open")

        expect(page.locator("html")).to_have_attribute("data-theme", "light")
        expect(page.locator("#themeToggle")).to_have_text("◐")
        page.locator("#themeToggle").click()
        expect(page.locator("html")).to_have_attribute("data-theme", "dark")
        expect(page.locator("#themeToggle")).to_have_text("☼")
        page.reload()
        expect(page.locator("html")).to_have_attribute("data-theme", "dark")
        browser.close()


def test_chat_http_error_detail_remains_visible():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.route(
            "**/api/meetings?*",
            json_route({"2026-04-01": [{
                "date": "2026-04-01",
                "policymaker": "00400",
                "policymaker_name": "Helsingin kaupunginhallitus",
                "meeting_link": CASE_URL,
            }]}),
        )
        page.route("**/api/agenda?*", json_route({"items": []}))
        page.route("**/api/attachments-bulk", json_route({"results": {}}))
        page.route(
            "**/api/chat",
            lambda route: route.fulfill(
                status=503,
                content_type="application/json",
                body=json.dumps({"error": "AGENT_ENDPOINT is not configured"}),
            ),
        )

        page.goto("http://127.0.0.1:8080/")
        page.locator(".meeting-item").click()
        page.locator("#chatInput").fill("Onko tässä kokouksessa liitteitä?")
        page.locator("#chatSend").click()

        expect(page.locator("#chatMessages")).to_contain_text(
            "Yhteysvirhe: AGENT_ENDPOINT is not configured"
        )
        browser.close()


def test_trusted_attachment_pdf_link_is_available():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.route(
            "**/api/meetings?*",
            json_route({"2026-04-01": [{
                "date": "2026-04-01",
                "policymaker": "00400",
                "policymaker_name": "Helsingin kaupunginhallitus",
                "meeting_link": CASE_URL,
            }]}),
        )
        page.route(
            "**/api/agenda?*",
            json_route({"items": [{"num": 1, "title": "1. / 525 § Testi", "case_url": CASE_URL}]}),
        )
        page.route("**/api/attachments-bulk", json_route({"results": {}}))
        page.route(
            "**/api/attachments?*",
            json_route({"attachments": [{
                "num": 1,
                "name": "Julkaistu liite",
                "published": True,
                "pdf_url": "https://ahjojulkaisu.hel.fi/document.pdf",
            }]}),
        )

        page.goto("http://127.0.0.1:8080/")
        page.locator(".meeting-item").click()
        page.locator(".agenda-header").click()
        page.locator(".attachment-item").click()

        pdf_link = page.locator("#modal-pdf-link")
        expect(pdf_link).to_be_visible()
        expect(pdf_link).to_have_attribute(
            "href", "https://ahjojulkaisu.hel.fi/document.pdf"
        )
        browser.close()


if __name__ == "__main__":
    test_upstream_content_is_text_and_external_pdf_is_blocked()
    test_chat_stream_error_remains_visible()
    test_compact_pane_controls_and_light_theme()
    test_chat_http_error_detail_remains_visible()
    test_trusted_attachment_pdf_link_is_available()