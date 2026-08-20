# Generate a placeholder LearnGraph app icon (32x32 ICO) for tauri-build.
# Phase 1 placeholder only — real brand icon comes later.
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing

$size = 32
$bmp = New-Object System.Drawing.Bitmap($size, $size)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
$bg = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(255, 79, 70, 229))   # indigo
$fg = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(255, 255, 255, 255))
$g.FillRectangle($bg, 0, 0, $size, $size)
# simple "L" glyph
$font = New-Object System.Drawing.Font("Segoe UI", 18, [System.Drawing.FontStyle]::Bold, [System.Drawing.GraphicsUnit]::Pixel)
$g.DrawString("L", $font, $fg, 3, 0)
$font.Dispose(); $fg.Dispose(); $bg.Dispose(); $g.Dispose()

$dir = Join-Path $PSScriptRoot "..\src-tauri\icons"
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$icon = [System.Drawing.Icon]::FromHandle($bmp.GetHicon())
$out = Join-Path $dir "icon.ico"
$fs = [System.IO.File]::Create($out)
$icon.Save($fs)
$fs.Close(); $icon.Dispose(); $bmp.Dispose()
Write-Host "written: $out"
