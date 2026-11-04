[CmdletBinding()]
param([switch]$Uninstall)

$ErrorActionPreference = 'Stop'
$taskName = 'Video Downloader Ultra'
$runtime = Join-Path $PSScriptRoot '.runtime'
$launcher = Join-Path $runtime 'svd.exe'
$appPaths = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\App Paths'
$aliases = @('svd.exe', 'start video downloader.exe')
$user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$existing = Get-ScheduledTask -TaskName $taskName -TaskPath '\' -ErrorAction SilentlyContinue

if ($existing -and $existing.Actions.Execute -ne $launcher) {
    throw "An unrelated task named '$taskName' already exists."
}
foreach ($name in $aliases) {
    $key = Join-Path $appPaths $name
    if ((Test-Path -LiteralPath $key) -and (Get-Item -LiteralPath $key).GetValue('') -ne $launcher) {
        throw "The application alias '$name' is already in use."
    }
}

if ($Uninstall) {
    if ($existing) {
        Stop-ScheduledTask -InputObject $existing -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -InputObject $existing -Confirm:$false
    }
    foreach ($name in $aliases) {
        $key = Join-Path $appPaths $name
        if (Test-Path -LiteralPath $key) { Remove-Item -LiteralPath $key }
    }
    Write-Output 'Removed the scheduled task and both aliases.'
    return
}

if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'))) {
    throw 'Create the project Python environment first. See README.md.'
}
if (-not (Test-Path -LiteralPath $runtime)) { New-Item -ItemType Directory -Path $runtime | Out-Null }
if (-not (Test-Path -LiteralPath $launcher) -or (Get-Item $launcher).LastWriteTime -lt (Get-Item (Join-Path $PSScriptRoot 'launcher.cs')).LastWriteTime) {
    if ($existing -and $existing.State -eq 'Running') { Stop-ScheduledTask -InputObject $existing }
    if (Test-Path -LiteralPath $launcher) { Remove-Item -LiteralPath $launcher }
    Add-Type -Path (Join-Path $PSScriptRoot 'launcher.cs') -ReferencedAssemblies 'System.Windows.Forms','System.Core' -OutputAssembly $launcher -OutputType WindowsApplication
}
$action = New-ScheduledTaskAction -Execute $launcher -Argument '--watch' -WorkingDirectory $PSScriptRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
if (-not (Test-Path -LiteralPath $appPaths)) { New-Item -Path $appPaths -Force | Out-Null }
foreach ($name in $aliases) {
    $key = Join-Path $appPaths $name
    if (-not (Test-Path -LiteralPath $key)) { New-Item -Path $key | Out-Null }
    Set-Item -LiteralPath $key -Value $launcher
}
Start-ScheduledTask -TaskName $taskName
Write-Output 'Installed the logon task and both application aliases.'
