# Ahjo API Discovery - Phase 0 Findings

**Date:** 2026-07-02  
**Status:** Discovery complete - evidence-based assessment

---

## Executive Summary

The Helsinki Ahjo decision-making system data **is accessible** for AI-assisted meeting preparation. Three access tiers were identified:

1. **Public (no auth)** - Sufficient for building a meeting briefing pipeline
2. **Drupal-authenticated** - Full REST proxy to Ahjo (requires credentials)
3. **Internal API key** - Direct upstream Ahjo API (requires API key)

**Critical finding:** Attachments (PDFs) are fully public and downloadable. The AJAX JSON endpoint for decision content is also public.

---

## Verified Data Access Paths

### Tier 1: Public Access (No Authentication)

| Endpoint | Method | Returns | Purpose |
|----------|--------|---------|---------|
| `/fi/paattajat/{org}/asiakirjat` | GET (HTML) | Meeting listings | Discover meetings by policymaker |
| `/fi/paattajat/{org}/asiakirjat?page={n}` | GET (HTML) | Paginated listings | Browse historical meetings |
| `/fi/asia/{caseId}?paatos={decisionId}` | GET (HTML) | Decision page | Full case/decision with attachments |
| `/ahjo_api/case/{caseId}/{decisionId}` | GET | `application/json` | **Decision content as JSON** |
| `https://ahjojulkaisu.hel.fi/{GUID}.pdf` | GET | `application/pdf` | **Direct PDF download** |

### Tier 2: Drupal Proxy (Requires `access ahjo proxy` permission)

| Endpoint | Parameters | Purpose |
|----------|-----------|---------|
| `/ahjo-proxy/meetings` | `start`, `end`, `decisionmaker_id`, `size` | List meetings |
| `/ahjo-proxy/meetings/single/{id}` | - | Meeting detail with agenda |
| `/ahjo-proxy/decisions` | `meeting_id` | Decisions for a meeting |
| `/ahjo-proxy/decisions/single/{id}` | - | Full decision with attachments |
| `/ahjo-proxy/agenda-item/{meetingId}/{id}` | - | Single agenda item |
| `/ahjo-proxy/cases` | - | List cases |
| `/ahjo-proxy/cases/single/{id}` | - | Case detail |
| `/ahjo-proxy/records/{nativeId}` | - | Record by native ID |
| `/ahjo-proxy/document/{nativeId}` | - | File download |
| `/ahjo-proxy/trustees/single/{id}` | - | Trustee info |
| `/ahjo-proxy/organization/single/{id}` | - | Organization detail |
| `/ahjo-proxy/decisionmaker/single/{id}` | - | Decisionmaker detail |
| `/ahjo-proxy/aggregated/{dataset}` | - | Aggregated reference data |

Authentication: Drupal cookie or `key_auth` header.

### Tier 3: Upstream Ahjo API (Internal, API Key)

Same endpoint patterns as Tier 2 proxy. Authenticated via `api-key` HTTP header.  
Base URL configured via `AHJO_API_URL` environment variable.

---

## Answers to Primary Questions

### Meeting Discovery

| Question | Answer | Evidence |
|----------|--------|----------|
| Can upcoming meetings be listed? | YES | `/fi/paattajat/{org}/asiakirjat` lists meetings by year |
| Can meetings be filtered by committee? | YES | Each policymaker has own listing page |
| Can meetings be filtered by date range? | PARTIAL | Year filter on listing; full filter via proxy API |
| Is there a stable meeting identifier? | YES | Format: `{decisionmaker_id}{year}{sequence}` e.g. `00400202624` |
| Meeting dates and times available? | YES | Shown on listing pages |

### Agenda Items

| Question | Answer | Evidence |
|----------|--------|----------|
| Can a meeting be expanded into agenda items? | YES | Meeting page lists all cases with decision IDs |
| Can agenda items be queried independently? | YES | `/ahjo_api/case/{caseId}/{decisionId}` returns JSON |
| Can agenda items be linked across meetings? | YES | Case IDs (e.g. `HEL-2026-008871`) are stable across meetings |
| Item titles available? | YES | Extracted from JSON content `<h2>` element |
| Content/descriptions available? | YES | Full HTML content in JSON response |

### Decisions

| Question | Answer | Evidence |
|----------|--------|----------|
| Decision content available? | YES | Full HTML with sections (Paatos, Esittelijaperustelu, etc.) |
| Decision proposals (Motion)? | YES (via proxy) | `Motion` field in `AhjoFullDecisionDTO` |
| Decision history? | YES | `DecisionHistoryHTML` / `DecisionHistoryPdf` fields |
| Vote information? | YES (via proxy) | `VotingResults[]` field in full decision DTO |
| Previous decisions linked? | YES (via proxy) | `PreviousDecisions[]` field |

### Attachments (Critical Area)

| Question | Answer | Evidence |
|----------|--------|----------|
| Are attachments exposed? | **YES** | Links found in HTML and available via API |
| Are attachment URLs available? | **YES** | `https://ahjojulkaisu.hel.fi/{GUID}.pdf` |
| Are files downloadable? | **YES** | All tested files returned HTTP 200 |
| File formats? | **PDF** confirmed | All observed attachments are PDF |
| Authentication required? | **NO** | Fully public CDN access |
| Metadata available? | YES (via proxy) | Title, NativeId, AttachmentNumber, PublicityClass, Language, Type |
| MIME types? | YES | `application/pdf` confirmed via HEAD |
| Sizes available? | YES | Content-Length header (range: 80KB - 4.5MB observed) |

**Success criterion MET:** Complete meeting package retrieved including 12 attachments (7.11 MB total) from meeting 00400202624 (Kaupunginhallitus 25/2026).

### Related Material

| Question | Answer |
|----------|--------|
| Decision history PDFs | YES - `DecisionHistoryPdf` field |
| Previous decisions | YES - linked via `PreviousDecisions[]` |
| Classification metadata | YES - `ClassificationCode`, `ClassificationTitle` |

### Metadata

| Field | Available |
|-------|-----------|
| Policymaker/DecisionMaker | YES |
| DecisionMakerID | YES (e.g. "00400" = Kaupunginhallitus) |
| CaseID / CaseIDLabel | YES (e.g. "HEL-2026-008871") |
| Section number | YES |
| Organization | YES (via proxy) |
| Status | YES |

---

## Observed Entity Model

```
Meeting (ID: "00400202624")
 +-- DateMeeting
 +-- DecisionMaker / DecisionMakerID
 +-- AgendaPublished / MinutesPublished
 +-- Status
 +-- MeetingDocuments[] (full meeting PDF)
 +-- Agenda[]
      +-- AgendaItem
           +-- AgendaPoint (int)
           +-- CaseIDLabel (e.g. "HEL-2026-008871")
           +-- Title
           +-- Html (content)
           +-- Pdf (main item PDF)
           +-- DecisionHistoryPdf
           +-- Attachments[] --> ahjojulkaisu.hel.fi/{GUID}.pdf
 +-- Decisions[] (after minutes published)
      +-- Decision
           +-- NativeId (GUID)
           +-- Title
           +-- CaseID / CaseIDLabel
           +-- Content (HTML)
           +-- Motion (proposal)
           +-- VotingResults[]
           +-- Attachments[]
           +-- PreviousDecisions[]
           +-- DateDecision
           +-- Classification

Committee / Policymaker
 +-- Slug (e.g. "kaupunginhallitus")
 +-- ID (e.g. "00400")
 +-- Meetings (listed by year)
```

---

## API Inventory

| Endpoint | Purpose | Auth | Filtering | Historical | Attachments |
|----------|---------|------|-----------|-----------|-------------|
| `/fi/paattajat/{org}/asiakirjat` | Meeting listing | None | Year, paging | YES | No |
| `/ahjo_api/case/{id}/{did}` | Decision JSON | None | By case+decision | YES | Via HTML links |
| `ahjojulkaisu.hel.fi/{GUID}.pdf` | PDF download | None | N/A | YES | IS attachment |
| `/ahjo-proxy/meetings` | Meeting list | Drupal | Date, org, size | YES | Via detail |
| `/ahjo-proxy/decisions` | Decision list | Drupal | meeting_id | YES | YES |
| `/ahjo-proxy/agenda-item/{m}/{id}` | Agenda item | Drupal | By IDs | YES | YES |

---

## Non-Functional Findings

| Aspect | Observation |
|--------|-------------|
| Rate limiting | No explicit rate limit headers observed |
| Pagination | Meeting listings: `?page={n}` (25 per page) |
| Response times | HTML pages: 1-3s; AJAX JSON: 1-2s; PDF HEAD: <1s |
| Caching | Drupal page cache active (`s-maxage=1800`) |
| Legacy APIs | `api.hel.fi` - timeout/unreachable; `dev.hel.fi/paatokset` - timeout |
| Old Open Ahjo | `github.com/City-of-Helsinki/paatos` - archived Oct 2025 |

---

## Identified Risks and Gaps

1. **No public meeting listing API** - Meeting discovery requires HTML scraping of listing pages (not ideal but functional)
2. **Proxy auth barrier** - Full structured data (proper JSON DTOs with metadata) requires Drupal authentication
3. **AJAX endpoint returns HTML-in-JSON** - Decision content is rendered HTML, not structured data (title, sections must be parsed from HTML)
4. **No documented public API** - The old Open Ahjo API and api.hel.fi appear defunct
5. **Calendar is JS-rendered** - The kokouskalenteri page doesn't expose meeting links in static HTML

---

## Recommended Approach for AI Agent (Phase 1 prep)

### Without authentication (immediate):
1. Scrape `/fi/paattajat/{org}/asiakirjat` for meeting IDs
2. Scrape meeting pages for agenda item links (case + decision IDs)
3. Call `/ahjo_api/case/{caseId}/{decisionId}` for structured-ish JSON
4. Parse decision HTML content for titles, sections, attachment links
5. Download PDFs from `ahjojulkaisu.hel.fi`

### With Drupal authentication (recommended):
1. Use `/ahjo-proxy/meetings` with date range filter
2. Use `/ahjo-proxy/meetings/single/{id}` for full meeting with agenda
3. Use `/ahjo-proxy/decisions?meeting_id={id}` for all decisions
4. Access proper structured DTOs with metadata, voting, history

---

## Scripts Produced

| File | Purpose |
|------|---------|
| `discover-ahjo-api.ps1` | Systematic API endpoint discovery and documentation |
| `get-meeting-package.ps1` | End-to-end meeting package retrieval (proof of concept) |
| `output/meeting-00400202624.json` | Retrieved meeting data (Kaupunginhallitus 25/2026) |

---

## Next Steps (Do Not Implement)

- Obtain Drupal auth credentials for Tier 2 proxy access
- Evaluate whether scraping-based approach (Tier 1) is sufficient or Tier 2 is needed
- Test attachment content extraction (PDF -> text) with Azure AI services
- Test with different policymakers (valtuusto, lautakunnat, viranhaltijat)
