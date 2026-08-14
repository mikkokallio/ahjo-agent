[CmdletBinding()]
param(
    [string]$Destination = (Join-Path $PSScriptRoot "..\grounding\Kaupunkistrategia-2025-2029.pdf"),
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$sourceUri = [Uri]"https://www.hel.fi/static/helsinki/kaupunkistrategia/Kaupunkistrategia-2025-2029.pdf"
$expectedSha256 = "05A0E6AD13174397C497B2EF039D7A32484628B86E022CD6A0EE7FDC666E9FD9"
$destinationPath = [IO.Path]::GetFullPath($Destination)
$destinationDirectory = Split-Path -Parent $destinationPath
$temporaryPath = "$destinationPath.download-$PID"

New-Item -ItemType Directory -Path $destinationDirectory -Force | Out-Null

if (Test-Path -LiteralPath $destinationPath) {
    $existingHash = (Get-FileHash -LiteralPath $destinationPath -Algorithm SHA256).Hash
    if ($existingHash -eq $expectedSha256) {
        Write-Output "Strategy PDF is already present and verified: $destinationPath"
        exit 0
    }
    if (-not $Force) {
        throw "Destination exists with an unexpected checksum. Re-run with -Force to replace it: $destinationPath"
    }
}

try {
    $response = Invoke-WebRequest -Uri $sourceUri -OutFile $temporaryPath -PassThru -MaximumRedirection 3
    $finalUri = $response.BaseResponse.RequestMessage.RequestUri
    if ($finalUri.Scheme -ne "https" -or $finalUri.Host -ne "www.hel.fi") {
        throw "Download redirected to an untrusted location: $finalUri"
    }

    $stream = [IO.File]::OpenRead($temporaryPath)
    try {
        $header = [byte[]]::new(5)
        if ($stream.Read($header, 0, $header.Length) -ne $header.Length -or
            [Text.Encoding]::ASCII.GetString($header) -ne "%PDF-") {
            throw "Downloaded file is not a PDF."
        }
    }
    finally {
        $stream.Dispose()
    }

    $downloadedHash = (Get-FileHash -LiteralPath $temporaryPath -Algorithm SHA256).Hash
    if ($downloadedHash -ne $expectedSha256) {
        throw "Downloaded PDF checksum did not match the reviewed Helsinki publication."
    }

    Move-Item -LiteralPath $temporaryPath -Destination $destinationPath -Force
    Write-Output "Downloaded and verified Helsinki City Strategy: $destinationPath"
}
finally {
    if (Test-Path -LiteralPath $temporaryPath) {
        Remove-Item -LiteralPath $temporaryPath -Force
    }
}