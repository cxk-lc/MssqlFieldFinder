# 抓取指定标题窗口的截图（用于验证打包产物的运行效果）
# 用法: powershell -ExecutionPolicy Bypass -File capture_exe.ps1 <输出png> <标题关键字>

param(
    [Parameter(Mandatory=$true)][string]$Out,
    [Parameter(Mandatory=$false)][string]$TitleLike = "库表字段内容检索器"
)

Add-Type -AssemblyName System.Drawing

Add-Type @"
using System;
using System.Runtime.InteropServices;
public class WinApi {
    [StructLayout(LayoutKind.Sequential)]
    public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern int GetWindowTextLength(IntPtr hWnd);
}
"@

# 按「进程名 + 非空标题」定位，避免选到 Tk 的 popdown 浮层
$proc = Get-Process |
    Where-Object { $_.MainWindowHandle -ne 0 -and $_.MainWindowTitle -ne "" -and $_.MainWindowTitle -like "*$TitleLike*" } |
    Select-Object -First 1

if (-not $proc) {
    Write-Output "ERROR: 未找到标题包含「$TitleLike」的窗口"
    exit 1
}

$h = $proc.MainWindowHandle
[WinApi]::ShowWindow($h, 5) | Out-Null
[WinApi]::SetForegroundWindow($h) | Out-Null
Start-Sleep -Milliseconds 900

$rect = New-Object WinApi+RECT
[WinApi]::GetWindowRect($h, [ref]$rect) | Out-Null
$w = $rect.Right - $rect.Left
$hgt = $rect.Bottom - $rect.Top
if ($w -le 0 -or $hgt -le 0) {
    Write-Output "ERROR: 窗口尺寸无效 $w x $hgt"
    exit 1
}

$bmp = New-Object System.Drawing.Bitmap($w, $hgt)
$gfx = [System.Drawing.Graphics]::FromImage($bmp)
$gfx.CopyFromScreen($rect.Left, $rect.Top, 0, 0, $bmp.Size)
$bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
$gfx.Dispose()
$bmp.Dispose()

Write-Output "SAVED $Out ${w}x${hgt} (PID $($proc.Id) : $($proc.MainWindowTitle))"
