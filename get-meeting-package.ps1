# Ahjo Meeting Package Retrieval - Proof of Concept
# Demonstrates retrieval of a complete meeting package including attachments
# Uses only publicly accessible endpoints (no authentication required)

param(
    [string]$MeetingId = "00400202624",  # Default: Kaupunginhallitus 25/2026
    [string]$Policymaker = "kaupunginhallitus",
    [string]$OutputDir = ".\output"
)

$ErrorActionPreference = "Stop"
$baseUrl = "https://paatokset.hel.fi"

# Create output directory
if (-not (Test-Path $OutputDir)) { New-Item -ItemType Directory -Path $OutputDir | Out-Null }

Write-Host "=" * 60
Write-Host "AHJO MEETING PACKAGE RETRIEVAL"
Write-Host "Meeting: $MeetingId ($Policymaker)"
Write-Host "Output: $OutputDir"
Write-Host "=" * 60

# =============================================================================
# Step 1: Fetch meeting page and extract agenda items
# =============================================================================
Write-Host "`n[1/4] Fetching meeting page..."
$meetingUrl = "$baseUrl/fi/paattajat/$Policymaker/asiakirjat/$MeetingId"
$r = Invoke-WebRequest -Uri $meetingUrl -TimeoutSec 30
Write-Host "  Page loaded: $($r.Content.Length) bytes"

# Extract title
$titleMatch = [regex]::Match($r.Content, '<h1[^>]*>(.*?)</h1>')
$meetingTitle = if ($titleMatch.Success) { $titleMatch.Groups[1].Value -replace '<[^>]+>', '' } else { "Meeting $MeetingId" }
Write-Host "  Title: $meetingTitle"

# Extract agenda items
$caseMatches = [regex]::Matches($r.Content, 'href="(/fi/asia/([^"?]+)(?:\?paatos=([^"]+))?)"')
$agendaItems = @()
foreach ($match in $caseMatches) {
    $agendaItems += [PSCustomObject]@{
        Url = $match.Groups[1].Value
        CaseId = $match.Groups[2].Value
        DecisionId = $match.Groups[3].Value
    }
}
Write-Host "  Agenda items found: $($agendaItems.Count)"

# Extract meeting-level PDFs
$meetingPdfs = [regex]::Matches($r.Content, 'href="(https://ahjojulkaisu\.hel\.fi/([^"]+))"')
Write-Host "  Meeting-level PDFs: $($meetingPdfs.Count)"

# =============================================================================
# Step 2: Fetch decision content for each agenda item via AJAX API
# =============================================================================
Write-Host "`n[2/4] Fetching decision content via AJAX API..."
$decisions = @()
$fetchCount = 0

foreach ($item in $agendaItems) {
    if (-not $item.DecisionId) { continue }
    
    $ajaxUrl = "$baseUrl/ahjo_api/case/$($item.CaseId)/$($item.DecisionId)"
    try {
        $dr = Invoke-WebRequest -Uri $ajaxUrl -TimeoutSec 15
        $json = $dr.Content | ConvertFrom-Json
        
        # Extract title from HTML content
        $t = [regex]::Match($json.content, '<h2>(.*?)</h2>')
        $title = if ($t.Success) { [System.Web.HttpUtility]::HtmlDecode($t.Groups[1].Value) } else { $item.CaseId }
        
        $decisions += [PSCustomObject]@{
            CaseId = $item.CaseId
            DecisionId = $item.DecisionId
            Title = $title
            ContentLength = $json.content.Length
            RawJson = $json
        }
        
        $fetchCount++
        Write-Host "  [$fetchCount] $($item.CaseId): $title"
    } catch {
        Write-Host "  [!] Failed: $($item.CaseId) - $($_.Exception.Message)"
    }
    
    # Be polite - small delay between requests
    Start-Sleep -Milliseconds 500
}

# =============================================================================
# Step 3: Discover all attachments
# =============================================================================
Write-Host "`n[3/4] Discovering attachments..."
$allAttachments = @()

foreach ($item in $agendaItems | Select-Object -First 5) {
    $caseUrl = "$baseUrl/fi/asia/$($item.CaseId)"
    if ($item.DecisionId) { $caseUrl += "?paatos=$($item.DecisionId)" }
    
    try {
        $cr = Invoke-WebRequest -Uri $caseUrl -TimeoutSec 15
        $pdfMatches = [regex]::Matches($cr.Content, 'href="(https://ahjojulkaisu\.hel\.fi/([^"]+))"')
        
        foreach ($pdf in $pdfMatches) {
            $allAttachments += [PSCustomObject]@{
                CaseId = $item.CaseId
                Url = $pdf.Groups[1].Value
                Filename = $pdf.Groups[2].Value
            }
        }
        
        if ($pdfMatches.Count -gt 0) {
            Write-Host "  $($item.CaseId): $($pdfMatches.Count) attachment(s)"
        }
    } catch {
        Write-Host "  [!] $($item.CaseId): $($_.Exception.Message)"
    }
    
    Start-Sleep -Milliseconds 300
}

$uniqueAttachments = $allAttachments | Sort-Object Url -Unique
Write-Host "  Total unique attachments: $($uniqueAttachments.Count)"

# =============================================================================
# Step 4: Verify attachment accessibility (HEAD requests only)
# =============================================================================
Write-Host "`n[4/4] Verifying attachment accessibility..."
$verifiedAttachments = @()

foreach ($att in $uniqueAttachments | Select-Object -First 10) {
    try {
        $hr = Invoke-WebRequest -Uri $att.Url -Method Head -TimeoutSec 10
        $verifiedAttachments += [PSCustomObject]@{
            CaseId = $att.CaseId
            Filename = $att.Filename
            Url = $att.Url
            ContentType = $hr.Headers['Content-Type']
            Size = [int]$hr.Headers['Content-Length']
            Accessible = $true
        }
        Write-Host "  [OK] $($att.Filename) ($($hr.Headers['Content-Length']) bytes)"
    } catch {
        Write-Host "  [FAIL] $($att.Filename)"
        $verifiedAttachments += [PSCustomObject]@{
            CaseId = $att.CaseId
            Filename = $att.Filename
            Url = $att.Url
            ContentType = $null
            Size = 0
            Accessible = $false
        }
    }
}

# =============================================================================
# Output: Save meeting package summary as JSON
# =============================================================================
$meetingPackage = [PSCustomObject]@{
    metadata = [PSCustomObject]@{
        meetingId = $MeetingId
        policymaker = $Policymaker
        title = $meetingTitle
        url = $meetingUrl
        retrievedAt = (Get-Date -Format "yyyy-MM-ddTHH:mm:ssZ")
    }
    agendaItems = $agendaItems
    decisions = $decisions | Select-Object CaseId, DecisionId, Title, ContentLength
    attachments = $verifiedAttachments
    summary = [PSCustomObject]@{
        totalAgendaItems = $agendaItems.Count
        decisionsRetrieved = $decisions.Count
        totalAttachments = $uniqueAttachments.Count
        accessibleAttachments = ($verifiedAttachments | Where-Object Accessible).Count
        totalAttachmentBytes = ($verifiedAttachments | Where-Object Accessible | Measure-Object -Property Size -Sum).Sum
    }
}

$outputFile = Join-Path $OutputDir "meeting-$MeetingId.json"
$meetingPackage | ConvertTo-Json -Depth 5 | Set-Content -Path $outputFile -Encoding UTF8
Write-Host "`n`nMeeting package saved to: $outputFile"

Write-Host "`n--- SUMMARY ---"
Write-Host "Agenda items: $($meetingPackage.summary.totalAgendaItems)"
Write-Host "Decisions retrieved: $($meetingPackage.summary.decisionsRetrieved)"
Write-Host "Attachments found: $($meetingPackage.summary.totalAttachments)"
Write-Host "Attachments accessible: $($meetingPackage.summary.accessibleAttachments)"
Write-Host "Total attachment size: $([Math]::Round($meetingPackage.summary.totalAttachmentBytes / 1MB, 2)) MB"
