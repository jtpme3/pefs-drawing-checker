<#
.SYNOPSIS
    Bulk-export DXF and/or PDF from a folder of AutoCAD DWGs, headlessly.

.DESCRIPTION
    Drives accoreconsole.exe (the AutoCAD Core Console) once per drawing. No
    AutoCAD GUI is opened and no licence seat is held for long, so this runs
    while you keep working. Each drawing gets:
      * one PDF per paper-space layout, plotted through DWG To PDF.pc3 using
        the layout's own page setup (paper size, scale, area, orientation)
      * one DXF (ASCII, 16 decimal places, current DWG version)

.PARAMETER DwgFolder
    Folder containing the .dwg files.

.PARAMETER DxfOut
    Output folder for DXFs. Omit to skip DXF export.

.PARAMETER PdfOut
    Output folder for PDFs. Omit to skip PDF export.

.PARAMETER Ctb
    Optional plot style table to force on every layout, e.g. "monochrome.ctb".
    Omit to use whatever each layout's page setup already specifies.

.PARAMETER Recurse
    Include .dwg files in subfolders.

.PARAMETER TimeoutSec
    Per-drawing timeout. A drawing that stalls is killed and reported, and the
    batch carries on.

.EXAMPLE
    .\Export-AcadBatch.ps1 -DwgFolder "...\IF439\dwgs" -DxfOut "...\IF439\dxfs" -PdfOut "...\IF439\pdfs"

.EXAMPLE
    # PDFs only, forced black and white
    .\Export-AcadBatch.ps1 -DwgFolder "...\dwgs" -PdfOut "...\pdfs" -Ctb monochrome.ctb
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$DwgFolder,
    [string]$DxfOut,
    [string]$PdfOut,
    [string]$Ctb,
    [switch]$Recurse,
    [int]$TimeoutSec = 300,
    [string]$AcCoreConsole
)

$ErrorActionPreference = 'Stop'

# ---- locate accoreconsole.exe (newest AutoCAD release wins) ----
if (-not $AcCoreConsole) {
    $AcCoreConsole = Get-ChildItem 'C:\Program Files\Autodesk' -Filter accoreconsole.exe -Recurse -ErrorAction SilentlyContinue |
                     Sort-Object FullName -Descending | Select-Object -First 1 -ExpandProperty FullName
}
if (-not $AcCoreConsole -or -not (Test-Path $AcCoreConsole)) {
    throw "accoreconsole.exe not found. Pass -AcCoreConsole with its full path."
}

if (-not $DxfOut -and -not $PdfOut) { throw "Nothing to do: specify -DxfOut and/or -PdfOut." }

$lsp = Join-Path $PSScriptRoot 'acad_export.lsp'
if (-not (Test-Path $lsp)) { throw "acad_export.lsp not found beside this script." }

# Script files (.scr) treat a space as Enter, so keep every line a single token.
# SECURELOAD 0 is required or AutoCAD refuses to load LISP from an untrusted path.
$work = Join-Path ([IO.Path]::GetTempPath()) ("acadbatch_" + [Guid]::NewGuid().ToString('N').Substring(0,8))
New-Item -ItemType Directory -Force $work | Out-Null
$scr = Join-Path $work 'run.scr'
@"
SECURELOAD
0
FILEDIA
0
(load "$($lsp -replace '\\','/')")
"@ | Set-Content $scr -Encoding ascii

foreach ($d in @($DxfOut, $PdfOut) | Where-Object { $_ }) {
    New-Item -ItemType Directory -Force $d | Out-Null
}

$env:ACX_DXF = $DxfOut
$env:ACX_PDF = $PdfOut
$env:ACX_CTB = $Ctb

$dwgs = Get-ChildItem $DwgFolder -Filter *.dwg -File -Recurse:$Recurse | Sort-Object Name
if (-not $dwgs) { throw "No .dwg files found in $DwgFolder" }

Write-Host "accoreconsole : $AcCoreConsole"
Write-Host "drawings      : $($dwgs.Count)"
if ($DxfOut) { Write-Host "DXF -> $DxfOut" }
if ($PdfOut) { Write-Host "PDF -> $PdfOut" }
if ($Ctb)    { Write-Host "plot style    : $Ctb (forced)" }
Write-Host ""

$results = @()
$i = 0
foreach ($dwg in $dwgs) {
    $i++
    $log = Join-Path $work ($dwg.BaseName + '.log')
    $sw  = [Diagnostics.Stopwatch]::StartNew()

    $p = Start-Process -FilePath $AcCoreConsole `
                       -ArgumentList '/i', "`"$($dwg.FullName)`"", '/s', "`"$scr`"", '/l', 'en-US' `
                       -RedirectStandardOutput $log -PassThru -NoNewWindow
    $ok = $p.WaitForExit($TimeoutSec * 1000)
    if (-not $ok) { $p.Kill(); Start-Sleep -Milliseconds 500 }
    $sw.Stop()

    $text  = try { [IO.File]::ReadAllText($log, [Text.Encoding]::Unicode) } catch { '' }
    $pdfOk = ([regex]::Matches($text, 'ACX>PLOT-OK')).Count
    $dxfOk = $text -match 'ACX>DXF-OK'
    $status = if (-not $ok) { 'TIMEOUT' }
              elseif (($PdfOut -and $pdfOk -eq 0) -or ($DxfOut -and -not $dxfOk)) { 'PARTIAL' }
              else { 'OK' }

    # surface anything AutoCAD complained about
    $warn = ($text -split "`r?`n" |
             Select-String -Pattern 'cannot be found|Incompatible or missing plot style|; error' |
             ForEach-Object { $_.Line.Trim() } | Select-Object -Unique) -join '; '

    $results += [pscustomobject]@{
        Drawing = $dwg.Name
        Status  = $status
        PDFs    = $pdfOk
        DXF     = [bool]$dxfOk
        Sec     = [math]::Round($sw.Elapsed.TotalSeconds, 1)
        Notes   = $warn
        Log     = $log
    }

    $colour = switch ($status) { 'OK' { 'Green' } 'PARTIAL' { 'Yellow' } default { 'Red' } }
    Write-Host ("[{0,2}/{1}] {2,-32} {3,-8} {4,4}s" -f $i, $dwgs.Count, $dwg.Name, $status, $sw.Elapsed.TotalSeconds) -ForegroundColor $colour
}

Write-Host ""
$results | Select-Object Drawing, Status, PDFs, DXF, Sec | Format-Table -AutoSize
$bad = $results | Where-Object Status -ne 'OK'
if ($bad) {
    Write-Host "Problem drawings (logs kept in $work):" -ForegroundColor Yellow
    $bad | Select-Object Drawing, Status, Notes, Log | Format-List
} else {
    Write-Host "All $($dwgs.Count) drawings exported." -ForegroundColor Green
    Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
}

$notes = $results | Where-Object { $_.Notes } | Select-Object -ExpandProperty Notes -Unique
if ($notes) {
    Write-Host "`nWarnings seen during export:" -ForegroundColor Yellow
    $notes | ForEach-Object { Write-Host "  $_" }
}
