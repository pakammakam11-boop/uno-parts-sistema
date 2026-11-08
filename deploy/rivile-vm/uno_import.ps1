# UNO Parts -> Rivile GAMA: naujos saskaitos is UNO sistemos.
#   C:\UNOPARTS_IMPORT\IMPORTUI\uno_<data>_<siunta>.eip     importo failai Rivilei (nauji)
#   C:\UNOPARTS_IMPORT\PDF\<metai-men>\<data>_<numeris>.pdf  saskaitu originalai
#   C:\UNOPARTS_IMPORT\ARCHYVAS\                            jau suimportuoti EIP (perkelia buhaltere)
#   C:\UNOPARTS_IMPORT\LOGAI\log.txt                        kiekvieno paleidimo rezultatas
#   C:\UNOPARTS_IMPORT\SISTEMA\                             sie skriptai ir raktas (config)
# Atsisiunciamos tik dar neperduotos saskaitos; issaugojus failus siunta patvirtinama (ack),
# todel ta pati saskaita antra karta neatsiunciama.
$ErrorActionPreference = "Stop"
$Root      = "C:\UNOPARTS_IMPORT"
$ImportDir = Join-Path $Root "IMPORTUI"
$PdfRoot   = Join-Path $Root "PDF"
$LogDir    = Join-Path $Root "LOGAI"
$LogFile   = Join-Path $LogDir "log.txt"
$Api       = "https://uno-parts-demo.vercel.app/api/export/gama-eip"
$Token     = (Get-Content (Join-Path $PSScriptRoot "config\token.txt") -Raw).Trim()
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
Add-Type -AssemblyName System.IO.Compression, System.IO.Compression.FileSystem
foreach ($d in $ImportDir, $PdfRoot, $LogDir, (Join-Path $Root "ARCHYVAS")) { New-Item -ItemType Directory -Force $d | Out-Null }

function Log([string]$text) {
    $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $text
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
    $all = Get-Content $LogFile
    if ($all.Count -gt 2000) { $all | Select-Object -Last 1000 | Set-Content $LogFile -Encoding UTF8 }
}

$zipPath = $null
try {
    $headers = @{ "X-UNO-Token" = $Token }
    $resp = Invoke-WebRequest -Uri ($Api + "?format=zip") -Headers $headers -UseBasicParsing -TimeoutSec 280
    if ($resp.StatusCode -eq 204) { Log "OK: nauju saskaitu nera"; exit 0 }
    $batch = [string]$resp.Headers["X-UNO-Batch"]
    $count = [string]$resp.Headers["X-UNO-Count"]
    if ($batch -notmatch '^[A-Za-z0-9]{1,32}$') { throw "netinkamas siuntos ID" }
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $pdfDir = Join-Path $PdfRoot (Get-Date -Format "yyyy-MM")
    New-Item -ItemType Directory -Force $pdfDir | Out-Null
    $zipPath = Join-Path $env:TEMP ("uno_{0}_{1}.zip" -f $stamp, $batch)
    [IO.File]::WriteAllBytes($zipPath, $resp.Content)
    if ((Get-Item $zipPath).Length -ne $resp.RawContentLength) { throw "failas issaugotas nepilnai" }

    $zip = [IO.Compression.ZipFile]::OpenRead($zipPath)
    $pdfs = 0
    try {
        foreach ($entry in $zip.Entries) {
            $leaf = [IO.Path]::GetFileName($entry.FullName)          # be katalogu: jokio ..\
            if ($leaf -eq "uno.eip") {
                $target = Join-Path $ImportDir ("uno_{0}_{1}.eip" -f $stamp, $batch)
            } elseif ($leaf -match '^[\w.\-]+\.pdf$') {
                $target = Join-Path $pdfDir ("{0}_{1}" -f $stamp, $leaf); $pdfs++
            } else { continue }
            [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $target, $true)
        }
    } finally { $zip.Dispose() }

    Invoke-WebRequest -Uri ($Api + "/ack?batch=" + $batch) -Method Post -Headers $headers -UseBasicParsing -TimeoutSec 60 | Out-Null
    Log ("OK: issaugota {0} saskaitu (EIP + {1} PDF), siunta {2}" -f $count, $pdfs, $batch)
    exit 0
} catch {
    Log ("KLAIDA: " + $_.Exception.Message)
    exit 1
} finally {
    if ($zipPath -and (Test-Path $zipPath)) { Remove-Item $zipPath -Force }
}
