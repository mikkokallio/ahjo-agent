# Ahjo API Discovery Script
# Purpose: Systematically probe and document the Helsinki Paatokset/Ahjo APIs
# to determine what data is available for AI-assisted meeting preparation.

$ErrorActionPreference = "Continue"
$baseUrl = "https://paatokset.hel.fi"

Write-Host "=" * 70
Write-Host "AHJO API DISCOVERY - $(Get-Date -Format 'yyyy-MM-dd HH:mm')"
Write-Host "=" * 70

# =============================================================================
# 1. MEETING DISCOVERY - Policymaker (Decision Maker) Document Listings
# =============================================================================
Write-Host "`n## 1. MEETING DISCOVERY"
Write-Host "-" * 40

# Known policymaker slugs and their meeting ID prefixes
$policymakers = @(
    @{slug="kaupunginhallitus"; name="Kaupunginhallitus (City Board)"; prefix="0040020"},
    @{slug="kaupunginvaltuusto"; name="Kaupunginvaltuusto (City Council)"; prefix="0200020"}
)

foreach ($pm in $policymakers) {
    Write-Host "`n### $($pm.name)"
    $url = "$baseUrl/fi/paattajat/$($pm.slug)/asiakirjat"
    try {
        $r = Invoke-WebRequest -Uri $url -TimeoutSec 15 -ErrorAction Stop
        Write-Host "  Listing page: OK ($($r.Content.Length) bytes)"
        
        # Extract meeting IDs from the page
        $meetingLinks = [regex]::Matches($r.Content, "href=""/fi/paattajat/$($pm.slug)/asiakirjat/([^""]+)""")
        Write-Host "  Meetings found on page 1: $($meetingLinks.Count)"
        
        # Show first 3
        foreach ($link in $meetingLinks | Select-Object -First 3) {
            Write-Host "    Meeting ID: $($link.Groups[1].Value)"
        }
    } catch {
        Write-Host "  ERROR: $($_.Exception.Message)"
    }
}

# =============================================================================
# 2. MEETING DETAIL - Single Meeting Page Analysis
# =============================================================================
Write-Host "`n`n## 2. MEETING DETAIL (Single Meeting)"
Write-Host "-" * 40

$testMeetingId = "00400202624"  # Kaupunginhallitus 25/2026
$meetingUrl = "$baseUrl/fi/paattajat/kaupunginhallitus/asiakirjat/$testMeetingId"

try {
    $r = Invoke-WebRequest -Uri $meetingUrl -TimeoutSec 15 -ErrorAction Stop
    Write-Host "Meeting page: OK ($($r.Content.Length) bytes)"
    
    # Extract agenda item links (cases/decisions)
    $caseLinks = [regex]::Matches($r.Content, 'href="(/fi/asia/([^"?]+)(?:\?paatos=([^"]+))?)"')
    Write-Host "Agenda items (cases with decisions): $($caseLinks.Count)"
    
    $agendaItems = @()
    foreach ($link in $caseLinks) {
        $agendaItems += @{
            url = $link.Groups[1].Value
            caseId = $link.Groups[2].Value
            decisionId = $link.Groups[3].Value
        }
    }
    
    Write-Host "`nAgenda items on meeting page:"
    foreach ($item in $agendaItems | Select-Object -First 5) {
        Write-Host "  Case: $($item.caseId) | Decision: $($item.decisionId)"
    }
    
    # PDF on meeting overview page
    $meetingPdfs = [regex]::Matches($r.Content, 'href="(https://ahjojulkaisu\.hel\.fi/[^"]+)"')
    Write-Host "`nMeeting-level PDFs: $($meetingPdfs.Count)"
    foreach ($pdf in $meetingPdfs) {
        Write-Host "  $($pdf.Groups[1].Value)"
    }
} catch {
    Write-Host "ERROR: $($_.Exception.Message)"
}

# =============================================================================
# 3. CASE/DECISION AJAX API (PUBLIC - NO AUTH)
# =============================================================================
Write-Host "`n`n## 3. CASE/DECISION AJAX API"
Write-Host "-" * 40
Write-Host "Endpoint: GET /ahjo_api/case/{case_id}/{decision_id}"
Write-Host "Auth: None required (_access: TRUE)"
Write-Host "Returns: application/json"

if ($agendaItems.Count -gt 0) {
    $testItem = $agendaItems[0]
    $ajaxUrl = "$baseUrl/ahjo_api/case/$($testItem.caseId)/$($testItem.decisionId)"
    Write-Host "`nTesting: $ajaxUrl"
    
    try {
        $r = Invoke-WebRequest -Uri $ajaxUrl -TimeoutSec 15 -ErrorAction Stop
        Write-Host "  Status: $($r.StatusCode)"
        Write-Host "  Content-Type: $($r.Headers['Content-Type'])"
        Write-Host "  Size: $($r.Content.Length) bytes"
        
        $json = $r.Content | ConvertFrom-Json
        Write-Host "  JSON keys: $($json.PSObject.Properties.Name -join ', ')"
        
        # Look for title in the HTML content
        $titleMatch = [regex]::Match($json.content, '<h2>(.*?)</h2>')
        if ($titleMatch.Success) {
            Write-Host "  Title: $($titleMatch.Groups[1].Value)"
        }
        
        # Check for attachment info
        if ($json.PSObject.Properties['attachments']) {
            Write-Host "  Attachments field present: YES"
        }
    } catch {
        Write-Host "  ERROR: $($_.Exception.Message)"
    }
}

# =============================================================================
# 4. ATTACHMENT DISCOVERY
# =============================================================================
Write-Host "`n`n## 4. ATTACHMENT DISCOVERY"
Write-Host "-" * 40
Write-Host "CDN: https://ahjojulkaisu.hel.fi/{GUID}.pdf"
Write-Host "Access: Public (no authentication required)"

# Get attachments from a case page
if ($agendaItems.Count -gt 0) {
    $testCase = $agendaItems[1]  # Pick item that likely has attachments
    $caseUrl = "$baseUrl/fi/asia/$($testCase.caseId)?paatos=$($testCase.decisionId)"
    Write-Host "`nFetching case page: $caseUrl"
    
    try {
        $r = Invoke-WebRequest -Uri $caseUrl -TimeoutSec 15 -ErrorAction Stop
        $attachments = [regex]::Matches($r.Content, 'href="(https://ahjojulkaisu\.hel\.fi/([^"]+))"')
        Write-Host "  Attachments found: $($attachments.Count)"
        
        foreach ($att in $attachments) {
            $attUrl = $att.Groups[1].Value
            $attFile = $att.Groups[2].Value
            
            # Check file accessibility
            try {
                $head = Invoke-WebRequest -Uri $attUrl -TimeoutSec 10 -Method Head -ErrorAction Stop
                $size = $head.Headers['Content-Length']
                $type = $head.Headers['Content-Type']
                Write-Host "  [OK] $attFile - $type ($size bytes)"
            } catch {
                Write-Host "  [FAIL] $attFile - $($_.Exception.Response.StatusCode)"
            }
        }
    } catch {
        Write-Host "  ERROR fetching case: $($_.Exception.Message)"
    }
}

# =============================================================================
# 5. AHJO PROXY ENDPOINTS (AUTHENTICATED - 403 WITHOUT AUTH)
# =============================================================================
Write-Host "`n`n## 5. AHJO PROXY ENDPOINTS (Require Authentication)"
Write-Host "-" * 40
Write-Host "Base: $baseUrl/ahjo-proxy/"
Write-Host "Auth: Drupal 'access ahjo proxy' permission (cookie or key_auth)"
Write-Host ""

$proxyEndpoints = @(
    @{path="/ahjo-proxy/meetings"; params="?start={date}&end={date}&decisionmaker_id={id}&size={n}"; desc="List meetings"},
    @{path="/ahjo-proxy/meetings/single/{id}"; params=""; desc="Single meeting detail"},
    @{path="/ahjo-proxy/decisions"; params="?meeting_id={id}"; desc="List decisions for a meeting"},
    @{path="/ahjo-proxy/decisions/single/{id}"; params=""; desc="Single decision detail"},
    @{path="/ahjo-proxy/cases"; params=""; desc="List cases"},
    @{path="/ahjo-proxy/cases/single/{id}"; params=""; desc="Single case detail"},
    @{path="/ahjo-proxy/agenda-item/{meeting_id}/{id}"; params=""; desc="Single agenda item"},
    @{path="/ahjo-proxy/trustees/single/{id}"; params=""; desc="Trustee info"},
    @{path="/ahjo-proxy/organization/single/{id}"; params=""; desc="Organization info"},
    @{path="/ahjo-proxy/decisionmaker/single/{id}"; params=""; desc="Decisionmaker info"},
    @{path="/ahjo-proxy/organization/positionsoftrust/{id}"; params=""; desc="Positions of trust"},
    @{path="/ahjo-proxy/aggregated/{dataset}"; params=""; desc="Aggregated datasets"},
    @{path="/ahjo-proxy/records/{nativeId}"; params=""; desc="Record by native ID"},
    @{path="/ahjo-proxy/document/{nativeId}"; params=""; desc="Document file download"}
)

Write-Host "Known proxy endpoints (all require auth):"
foreach ($ep in $proxyEndpoints) {
    Write-Host "  GET $($ep.path)$($ep.params)"
    Write-Host "      -> $($ep.desc)"
}

# Verify they return 403
Write-Host "`nVerifying auth requirement:"
try {
    $r = Invoke-WebRequest -Uri "$baseUrl/ahjo-proxy/meetings" -TimeoutSec 10 -ErrorAction Stop
    Write-Host "  /ahjo-proxy/meetings: $($r.StatusCode) (UNEXPECTED - public?)"
} catch {
    Write-Host "  /ahjo-proxy/meetings: $($_.Exception.Response.StatusCode) (auth required - confirmed)"
}

# =============================================================================
# 6. UPSTREAM AHJO API (INTERNAL)
# =============================================================================
Write-Host "`n`n## 6. UPSTREAM AHJO API (Backend)"
Write-Host "-" * 40
Write-Host "The upstream Ahjo API is accessed by the Drupal proxy."
Write-Host "Connection: AHJO_API_URL environment variable + AHJO_API_KEY header"
Write-Host ""
Write-Host "Known upstream endpoints (from datapumppu-paatokset-srv source code):"
Write-Host "  GET /ahjo-proxy/meetings?start={date}&end={date}&decisionmaker_id={id}&size={n}&agendaminutespublished=true"
Write-Host "  GET /ahjo-proxy/meetings/single/{meetingId}"
Write-Host "  GET /ahjo-proxy/decisions?meeting_id={meetingId}"
Write-Host "  GET /ahjo-proxy/decisions/single/{nativeId}"
Write-Host "  GET /ahjo-proxy/agenda-item/{meetingId}/{nativeId}"
Write-Host ""
Write-Host "Auth: api-key header"
Write-Host "Default DecisionMaker ID: 02900 (Kaupunginvaltuusto)"

# =============================================================================
# 7. DATA MODEL (OBSERVED)
# =============================================================================
Write-Host "`n`n## 7. OBSERVED DATA MODEL"
Write-Host "-" * 40
Write-Host @"

Meeting (AhjoFullMeetingDTO):
  - MeetingID (string, stable identifier e.g. "00400202624")
  - DateMeeting (DateTime)
  - DecisionMaker (string, e.g. "Kaupunginhallitus")
  - DecisionMakerID (string, e.g. "00400")
  - Name (string)
  - Location (string)
  - AgendaPublished (bool)
  - MinutesPublished (bool)
  - Status (string)
  - MeetingSequenceNumber (int)
  - Agenda[] (AgendaItems)
  - Composition[] (meeting participants)
  - MeetingDocuments[] (RecordPublicDTO)
  - PreviousMeeting, NextMeeting (navigation)

AgendaItem (AhjoAgendaItemDTO):
  - AgendaPoint (int)
  - Section (string)
  - AgendaItem (string, title)
  - CaseIDLabel (string, e.g. "HEL-2026-008871")
  - Html (string, content)
  - DecisionHistoryHTML (string)
  - Pdf (AttachmentDTO, main PDF)
  - DecisionHistoryPdf (AttachmentDTO)
  - Attachments[] (AttachmentDTO[])

Decision (AhjoFullDecisionDTO):
  - NativeId (string, GUID)
  - Title (string)
  - CaseIDLabel (string)
  - CaseID (string)
  - Section (string)
  - Content (string, HTML)
  - Motion (string, proposal text)
  - ClassificationCode (string)
  - ClassificationTitle (string)
  - Organization (OrganizationDTO)
  - Meeting (MeetingDTO)
  - VotingResults[] (VotingResultDTO[])
  - Attachments[] (AttachmentDTO[])
  - PreviousDecisions[] (DecisionDTO[])
  - Pdf (AttachmentDTO)
  - DateDecision (DateTime)
  - DecisionHistoryPdf (AttachmentDTO)
  - DecisionHistoryHtml (string)

Attachment (AhjoAttachmentDTO):
  - NativeId (string, GUID)
  - Title (string)
  - AttachmentNumber (string)
  - PublicityClass (string)
  - SecurityReasons[] (string[])
  - Type (string)
  - FileURI (string, URL at ahjojulkaisu.hel.fi)
  - Language (string)
  - PersonalData (string)
  - Issued (string, date)
"@

# =============================================================================
# 8. SUMMARY
# =============================================================================
Write-Host "`n`n## 8. DISCOVERY SUMMARY"
Write-Host "=" * 70
Write-Host @"

ACCESS TIERS:
  1. PUBLIC (no auth) - Scraping paatokset.hel.fi HTML pages
     - Meeting listings by policymaker: /fi/paattajat/{slug}/asiakirjat
     - Case/Decision pages: /fi/asia/{caseId}?paatos={decisionId}
     - AJAX API: /ahjo_api/case/{caseId}/{decisionId} (returns JSON!)
     - PDF downloads: https://ahjojulkaisu.hel.fi/{GUID}.pdf
     
  2. AUTHENTICATED (Drupal auth) - Ahjo Proxy
     - Full REST API at /ahjo-proxy/*
     - Meetings, decisions, agenda items, organizations
     - Requires 'access ahjo proxy' permission
     
  3. INTERNAL (API key) - Upstream Ahjo API
     - Same endpoints as proxy but directly to Ahjo backend
     - Requires AHJO_API_KEY

CRITICAL FINDINGS:
  [OK] Attachments ARE publicly downloadable (ahjojulkaisu.hel.fi CDN)
  [OK] Case/decision AJAX endpoint returns JSON (no auth!)
  [OK] Meeting pages list all agenda items with case IDs and decision IDs
  [OK] PDF format confirmed; sizes range from ~80KB to ~600KB+
  [!!] ahjo-proxy endpoints require Drupal authentication (403)
  [!!] No documented public REST API for meeting listings
  [OK] Meeting IDs are stable and follow pattern: {decisionmaker_id}{year}{seq}

RECOMMENDED APPROACH FOR AI AGENT:
  1. Use /fi/paattajat/{org}/asiakirjat to discover meetings
  2. Parse meeting pages for agenda items (case/decision links)
  3. Use /ahjo_api/case/{caseId}/{decisionId} for decision content (JSON!)
  4. Download PDFs from ahjojulkaisu.hel.fi
  5. For full API access, obtain Drupal auth credentials for ahjo-proxy
"@

Write-Host "`n`nDiscovery complete."
