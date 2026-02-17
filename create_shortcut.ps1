# ============================================================================
# ConCall AI - Desktop Shortcut Creator
# Creates a desktop shortcut for start_app.bat with custom icon
# ============================================================================

$WshShell = New-Object -comObject WScript.Shell
$DesktopPath = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = "$DesktopPath\ConCall AI.lnk"
$TargetPath = "$PSScriptRoot\scripts\start_app.bat"
$IconPath = "$PSScriptRoot\scripts\concall.ico"

$Shortcut = $WshShell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $TargetPath
$Shortcut.WorkingDirectory = $PSScriptRoot
$Shortcut.WindowStyle = 1
$Shortcut.Description = "ConCall AI - Local AI Meeting Assistant"
$Shortcut.IconLocation = $IconPath
$Shortcut.Save()

Write-Host ""
Write-Host "  [OK] Shortcut created: $ShortcutPath" -ForegroundColor Green
Write-Host "  [OK] Icon: $IconPath" -ForegroundColor Green
Write-Host ""
