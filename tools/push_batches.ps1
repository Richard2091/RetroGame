$ErrorActionPreference = 'Continue'
$start = 100
$total = 1252
$batchSize = 100
$batchNum = 2
$lastBatch = 13
while ($start -lt $total) {
    py "tools\git_stage.py" rom-batch $start $batchSize
    $cached = git diff --cached --name-only
    $romCount = ($cached | Select-String 'roms/').Count
    if ($romCount -eq 0) {
        Write-Output "NO ROMS STAGED at start=$start, abort"
        break
    }
    $end = [Math]::Min($start + $batchSize, $total)
    $msg = "data: FC ROM 批次 $batchNum/$lastBatch（$start-$end）"
    git commit -m $msg --quiet
    if ($LASTEXITCODE -ne 0) {
        Write-Output "COMMIT FAILED at start=$start"
        break
    }
    $ok = $false
    for ($i = 1; $i -le 4 -and -not $ok; $i++) {
        git push 2>$null
        if ($LASTEXITCODE -eq 0) { $ok = $true } else { Start-Sleep -Seconds 8 }
    }
    if (-not $ok) {
        Write-Output "PUSH FAILED at start=$start ($romCount roms)"
        break
    }
    Write-Output "batch $batchNum ok: games $start-$end, roms $romCount"
    $start += $batchSize
    $batchNum++
}
Write-Output "done at start=$start"
