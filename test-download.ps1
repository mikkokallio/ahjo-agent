# Test: Download a complete meeting package (JSON + all PDFs)
# Uses the same meeting we already verified: Kaupunginhallitus 00400202624

$meetingId = "00400202624"
$policymaker = "kaupunginhallitus"
$baseUrl = "https://paatokset.hel.fi"
$outDir = "output\test-$meetingId"

if (Test-Path $outDir) { Remove-Item $outDir -Recurse -Force }
New-Item -ItemType Directory -Path $outDir | Out-Null

Write-Host "=== Downloading meeting package: $meetingId ===" -ForegroundColor Cyan

# Step 1: Fetch meeting page and extract agenda items
$meetingUrl = "$baseUrl/fi/paattajat/$policymaker/asiakirjat/$meetingId"
Write-Host "`n[1/4] Fetching meeting page: $meetingUrl"
$response = Invoke-WebRequest -Uri $meetingUrl -UseBasicParsing
Write-Host "  Status: $($response.StatusCode)"

# Extract agenda item links
$pattern = '/fi/asia/([\w-]+)(\?paatos=([\w-]+))?'
$matches = [regex]::Matches($response.Content, $pattern)
$agendaItems = @()
foreach ($m in $matches) {
    $caseId = $m.Groups[1].Value
    $decisionId = if ($m.Groups[3].Success) { $m.Groups[3].Value } else { "" }
    if ($agendaItems.CaseId -notcontains $caseId) {
        $agendaItems += @{ CaseId = $caseId; DecisionId = $decisionId }
    }
}
Write-Host "  Agenda items found: $($agendaItems.Count)"

# Step 2: Fetch decisions via AJAX API
Write-Host "`n[2/4] Fetching decisions..."
$decisions = @()
foreach ($item in $agendaItems) {
    if ($item.DecisionId) {
        try {
            $url = "$baseUrl/ahjo_api/case/$($item.CaseId)/$($item.DecisionId)"
            $r = Invoke-WebRequest -Uri $url -UseBasicParsing
            $json = $r.Content | ConvertFrom-Json
            $title = if ($json.Title) { $json.Title } elseif ($json.title) { $json.title } else { "Unknown" }
            $decisions += @{
                CaseId = $item.CaseId
                DecisionId = $item.DecisionId
                Title = $title
                ContentLength = $r.Content.Length
            }
            Write-Host "  OK: $($item.CaseId) - $($title.Substring(0, [Math]::Min(60, $title.Length)))..."
        } catch {
            Write-Host "  FAIL: $($item.CaseId) - $($_.Exception.Message)" -ForegroundColor Yellow
        }
    }
}
Write-Host "  Decisions retrieved: $($decisions.Count)"

# Step 3: Discover attachments from case pages
Write-Host "`n[3/4] Discovering attachments..."
$attachments = @()
$cdnPattern = 'https://ahjojulkaisu\.hel\.fi/([A-F0-9-]+\.\w+)'
$seen = @{}
foreach ($item in $agendaItems) {
    try {
        $casePage = Invoke-WebRequest -Uri "$baseUrl/fi/asia/$($item.CaseId)" -UseBasicParsing
        $cdnMatches = [regex]::Matches($casePage.Content, $cdnPattern)
        foreach ($cm in $cdnMatches) {
            $filename = $cm.Groups[1].Value
            if (-not $seen.ContainsKey($filename)) {
                $seen[$filename] = $true
                $attachments += @{
                    CaseId = $item.CaseId
                    Filename = $filename
                    Url = $cm.Value
                }
            }
        }
    } catch {
        # skip
    }
}
Write-Host "  Unique attachments found: $($attachments.Count)"

# Step 4: DOWNLOAD all attachments
Write-Host "`n[4/4] Downloading attachments..."
$downloaded = @()
$totalBytes = 0
foreach ($att in $attachments) {
    $outPath = Join-Path $outDir $att.Filename
    try {
        Invoke-WebRequest -Uri $att.Url -OutFile $outPath -UseBasicParsing
        $fileInfo = Get-Item $outPath
        $totalBytes += $fileInfo.Length
        $downloaded += @{
            CaseId = $att.CaseId
            Filename = $att.Filename
            Url = $att.Url
            Size = $fileInfo.Length
        }
        Write-Host "  Downloaded: $($att.Filename) ($([math]::Round($fileInfo.Length/1024, 1)) KB)"
    } catch {
        Write-Host "  FAILED: $($att.Filename) - $($_.Exception.Message)" -ForegroundColor Red
    }
}

# Save meeting JSON
$meetingData = @{
    metadata = @{
        meetingId = $meetingId
        policymaker = $policymaker
        url = $meetingUrl
        retrievedAt = (Get-Date -Format "yyyy-MM-ddTHH.mm.ssZ")
    }
    agendaItems = $agendaItems
    decisions = $decisions
    attachments = $downloaded
    summary = @{
        totalAgendaItems = $agendaItems.Count
        decisionsRetrieved = $decisions.Count
        attachmentsDownloaded = $downloaded.Count
        totalBytes = $totalBytes
    }
}
$jsonPath = Join-Path $outDir "meeting.json"
$meetingData | ConvertTo-Json -Depth 5 | Set-Content -Path $jsonPath -Encoding UTF8

Write-Host "`n=== RESULTS ===" -ForegroundColor Green
Write-Host "  Output folder: $outDir"
Write-Host "  Agenda items: $($agendaItems.Count)"
Write-Host "  Decisions: $($decisions.Count)"
Write-Host "  Attachments downloaded: $($downloaded.Count)"
Write-Host "  Total size: $([math]::Round($totalBytes/1024/1024, 2)) MB"
Write-Host "`nFiles on disk:"
Get-ChildItem $outDir | Format-Table Name, @{L='Size (KB)';E={[math]::Round($_.Length/1024,1)}} -AutoSize
