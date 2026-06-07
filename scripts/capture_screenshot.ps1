param(
    [switch]$ConsentConfirmed,
    [ValidateSet('desktop', 'clipboard')]
    [string]$Destination,
    [ValidateSet('fullscreen', 'active', 'window')]
    [string]$Target,
    [string[]]$Query = @(),
    [string]$OutputRoot = (Join-Path ([Environment]::GetFolderPath('Desktop')) 'screenshots'),
    [switch]$AllowMultipleMatches,
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not $ConsentConfirmed) {
    throw 'consent required: ask the user to approve capture and destination first'
}

Add-Type -AssemblyName System.Windows.Forms,System.Drawing

Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;

public static class Win32Capture {
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

    [DllImport("user32.dll")]
    public static extern IntPtr GetForegroundWindow();

    [DllImport("user32.dll")]
    public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);

    [DllImport("user32.dll")]
    public static extern bool IsWindowVisible(IntPtr hWnd);

    [DllImport("user32.dll", SetLastError=true)]
    public static extern int GetWindowText(IntPtr hWnd, StringBuilder lpString, int nMaxCount);

    [DllImport("user32.dll", SetLastError=true)]
    public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);

    [DllImport("user32.dll")]
    public static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);

    [DllImport("user32.dll")]
    public static extern bool PrintWindow(IntPtr hWnd, IntPtr hdcBlt, uint nFlags);

    [DllImport("user32.dll")]
    public static extern bool IsIconic(IntPtr hWnd);

    [StructLayout(LayoutKind.Sequential)]
    public struct RECT {
        public int Left;
        public int Top;
        public int Right;
        public int Bottom;
    }
}
"@

function Sanitize-Label {
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) {
        $Value = 'capture'
    }
    $label = $Value.ToLowerInvariant() -replace 'https?://', ''
    $label = $label -replace '[^a-z0-9]+', '-'
    $label = $label -replace '-+', '-'
    $label = $label.Trim('-')
    if ([string]::IsNullOrWhiteSpace($label)) {
        return 'capture'
    }
    if ($label.Length -gt 80) {
        return $label.Substring(0, 80).Trim('-')
    }
    return $label
}

function Protect-Directory {
    param([string]$Path)
    if (Test-Path -LiteralPath $Path) {
        $item = Get-Item -LiteralPath $Path -Force
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw 'refusing to write to reparse-point screenshots folder'
        }
        if (-not $item.PSIsContainer) {
            throw 'refusing to write screenshots into a non-directory path'
        }
    } else {
        New-Item -ItemType Directory -Path $Path -Force | Out-Null
    }

    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $acl = Get-Acl -LiteralPath $Path
    # Block inheritance; then purge ALL existing explicit ACEs so pre-existing
    # foreign rules (e.g. Everyone:Allow on a shared machine) cannot survive.
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($rule in @($acl.Access)) {
        $acl.RemoveAccessRule($rule) | Out-Null
    }
    $rule = [System.Security.AccessControl.FileSystemAccessRule]::new(
        $identity,
        'FullControl',
        'ContainerInherit,ObjectInherit',
        'None',
        'Allow'
    )
    $acl.AddAccessRule($rule)
    Set-Acl -LiteralPath $Path -AclObject $acl
}

function Protect-File {
    param([string]$Path)
    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $acl = Get-Acl -LiteralPath $Path
    # Block inheritance and purge all existing explicit ACEs before adding the
    # owner-only rule, matching the replace-not-merge semantics of Protect-Directory.
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($rule in @($acl.Access)) {
        $acl.RemoveAccessRule($rule) | Out-Null
    }
    $rule = [System.Security.AccessControl.FileSystemAccessRule]::new($identity, 'FullControl', 'Allow')
    $acl.AddAccessRule($rule)
    Set-Acl -LiteralPath $Path -AclObject $acl
}

function New-RequestFolder {
    Protect-Directory -Path $OutputRoot
    $folderName = Get-Date -Format 'MM_dd_yyyy_HH_mm_ss'
    $folder = Join-Path $OutputRoot $folderName
    Protect-Directory -Path $folder
    return $folder
}

function Get-RequestFolderPath {
    $folderName = Get-Date -Format 'MM_dd_yyyy_HH_mm_ss'
    return (Join-Path $OutputRoot $folderName)
}

function New-CapturePath {
    param([string]$Folder, [string]$Label)
    $safe = Sanitize-Label -Value $Label
    $candidate = Join-Path $Folder "$safe.png"
    if (-not (Test-Path -LiteralPath $candidate)) {
        return $candidate
    }
    for ($i = 1; $i -lt 1000; $i++) {
        $candidate = Join-Path $Folder ('{0}-{1:D3}.png' -f $safe, $i)
        if (-not (Test-Path -LiteralPath $candidate)) {
            return $candidate
        }
    }
    throw 'could not allocate a unique screenshot filename'
}

function New-TemporaryCapturePath {
    param([string]$Folder, [string]$FinalPath)
    $stem = [IO.Path]::GetFileNameWithoutExtension($FinalPath)
    for ($i = 0; $i -lt 1000; $i++) {
        $candidate = Join-Path $Folder ('.{0}.{1}.{2:D3}.tmp.png' -f $stem, $PID, $i)
        try {
            # CreateNew + exclusive share mode: atomic, no TOCTOU race.
            $stream = [System.IO.File]::Open(
                $candidate,
                [System.IO.FileMode]::CreateNew,
                [System.IO.FileAccess]::Write,
                [System.IO.FileShare]::None
            )
            $stream.Close()
            return $candidate
        } catch [System.IO.IOException] {
            continue
        }
    }
    throw 'could not allocate a private temporary screenshot file'
}

function Get-WindowTitle {
    param([IntPtr]$Handle)
    $builder = [Text.StringBuilder]::new(1024)
    [void][Win32Capture]::GetWindowText($Handle, $builder, $builder.Capacity)
    return $builder.ToString()
}

function Get-ProcessNameForWindow {
    param([IntPtr]$Handle)
    [uint32]$pid = 0
    [void][Win32Capture]::GetWindowThreadProcessId($Handle, [ref]$pid)
    if ($pid -eq 0) {
        return ''
    }
    try {
        return (Get-Process -Id $pid -ErrorAction Stop).ProcessName
    } catch {
        return ''
    }
}

function Find-WindowHandles {
    param([string]$Needle)
    $matches = [System.Collections.Generic.List[IntPtr]]::new()
    $callback = [Win32Capture+EnumWindowsProc]{
        param([IntPtr]$Handle, [IntPtr]$LParam)
        if (-not [Win32Capture]::IsWindowVisible($Handle)) {
            return $true
        }
        $title = Get-WindowTitle -Handle $Handle
        $processName = Get-ProcessNameForWindow -Handle $Handle
        if ($title.IndexOf($Needle, [StringComparison]::OrdinalIgnoreCase) -ge 0 -or
            $processName.IndexOf($Needle, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
            $matches.Add($Handle)
        }
        return $true
    }
    [void][Win32Capture]::EnumWindows($callback, [IntPtr]::Zero)
    return $matches
}

function Get-WindowBounds {
    param([IntPtr]$Handle)
    $rect = [Win32Capture+RECT]::new()
    if (-not [Win32Capture]::GetWindowRect($Handle, [ref]$rect)) {
        throw 'could not read window bounds'
    }
    $width = $rect.Right - $rect.Left
    $height = $rect.Bottom - $rect.Top
    if ($width -le 0 -or $height -le 0) {
        throw 'window has no drawable bounds'
    }
    return [Drawing.Rectangle]::new($rect.Left, $rect.Top, $width, $height)
}

function Copy-Rectangle {
    param([Drawing.Rectangle]$Bounds)
    $bitmap = [Drawing.Bitmap]::new($Bounds.Width, $Bounds.Height)
    $graphics = [Drawing.Graphics]::FromImage($bitmap)
    try {
        $graphics.CopyFromScreen($Bounds.Left, $Bounds.Top, 0, 0, $bitmap.Size)
        return $bitmap
    } finally {
        $graphics.Dispose()
    }
}

function Copy-Window {
    param([IntPtr]$Handle, [Drawing.Rectangle]$Bounds)
    # Ask the window to render itself into our DC. Unlike CopyFromScreen (which
    # grabs whatever pixels are on the screen at the rect), PrintWindow captures
    # the window's own content even when it is occluded or in the background,
    # without raising or stealing focus. PW_RENDERFULLCONTENT (0x2) is required
    # for DWM/DirectX-composited windows.
    $bitmap = [Drawing.Bitmap]::new($Bounds.Width, $Bounds.Height)
    $graphics = [Drawing.Graphics]::FromImage($bitmap)
    try {
        $hdc = $graphics.GetHdc()
        try {
            $ok = [Win32Capture]::PrintWindow($Handle, $hdc, 0x2)
        } finally {
            $graphics.ReleaseHdc($hdc)
        }
        if (-not $ok) {
            $bitmap.Dispose()
            throw 'PrintWindow failed to capture the window'
        }
        return $bitmap
    } finally {
        $graphics.Dispose()
    }
}

function Test-BitmapAllBlack {
    # Some GPU/DirectX/Electron (e.g. Chrome) windows render black under
    # PrintWindow. Sample a sparse grid so we can warn the user rather than
    # silently saving a black image.
    param([Drawing.Bitmap]$Bitmap)
    if ($Bitmap.Width -le 0 -or $Bitmap.Height -le 0) {
        return $true
    }
    $stepX = [Math]::Max(1, [int]($Bitmap.Width / 16))
    $stepY = [Math]::Max(1, [int]($Bitmap.Height / 16))
    for ($y = 0; $y -lt $Bitmap.Height; $y += $stepY) {
        for ($x = 0; $x -lt $Bitmap.Width; $x += $stepX) {
            $px = $Bitmap.GetPixel($x, $y)
            if ($px.R -ne 0 -or $px.G -ne 0 -or $px.B -ne 0) {
                return $false
            }
        }
    }
    return $true
}

function Capture-ToDestination {
    param([Drawing.Rectangle]$Bounds, [string]$Label, [IntPtr]$Handle = [IntPtr]::Zero)
    if ($DryRun) {
        if ($Destination -eq 'clipboard') {
            Write-Output 'clipboard'
        } else {
            Write-Output (New-CapturePath -Folder $script:RequestFolder -Label $Label)
        }
        return
    }

    if ($Handle -ne [IntPtr]::Zero) {
        # Per-window capture: use PrintWindow so occluded/background windows
        # capture their own content instead of whatever is drawn on top of them.
        $bitmap = Copy-Window -Handle $Handle -Bounds $Bounds
        if (Test-BitmapAllBlack -Bitmap $bitmap) {
            [Console]::Error.WriteLine("warning: '$Label' rendered black via PrintWindow (GPU/Electron window); content may be unavailable without bringing it forward.")
        }
    } else {
        $bitmap = Copy-Rectangle -Bounds $Bounds
    }
    try {
        if ($Destination -eq 'clipboard') {
            [Windows.Forms.Clipboard]::SetImage($bitmap)
            Write-Output 'clipboard'
        } else {
            $path = New-CapturePath -Folder $script:RequestFolder -Label $Label
            $tempPath = New-TemporaryCapturePath -Folder $script:RequestFolder -FinalPath $path
            try {
                # Secure the empty temp file before writing so the PNG bytes are
                # never readable by other users, even for a brief window.
                Protect-File -Path $tempPath
                $bitmap.Save($tempPath, [Drawing.Imaging.ImageFormat]::Png)
                # Guard against both existing files and dangling reparse points
                # (Test-Path returns $false for dangling junctions/symlinks).
                $destItem = Get-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
                if ($destItem) {
                    if (($destItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                        throw 'refusing to overwrite a reparse-point at the screenshot destination'
                    }
                    throw 'refusing to overwrite an existing screenshot path'
                }
                Move-Item -LiteralPath $tempPath -Destination $path -ErrorAction Stop
                Protect-File -Path $path
            } finally {
                if (Test-Path -LiteralPath $tempPath) {
                    Remove-Item -LiteralPath $tempPath -Force
                }
            }
            Write-Output $path
        }
    } finally {
        $bitmap.Dispose()
    }
}

if ($Destination -eq 'desktop') {
    if ($DryRun) {
        $script:RequestFolder = Get-RequestFolderPath
    } else {
        $script:RequestFolder = New-RequestFolder
    }
} else {
    $script:RequestFolder = $null
}

if ($Target -eq 'fullscreen') {
    $bounds = [Windows.Forms.SystemInformation]::VirtualScreen
    Capture-ToDestination -Bounds $bounds -Label 'screen'
    exit 0
}

if ($Target -eq 'active') {
    $handle = [Win32Capture]::GetForegroundWindow()
    if ($handle -eq [IntPtr]::Zero) {
        throw 'no active window found'
    }
    if ([Win32Capture]::IsIconic($handle)) {
        [Console]::Error.WriteLine("window_not_capturable: the active window is minimized — restore it and retry.")
        exit 75
    }
    Capture-ToDestination -Bounds (Get-WindowBounds -Handle $handle) -Label 'active-window' -Handle $handle
    exit 0
}

if ($Query.Count -eq 0) {
    throw 'window target requires at least one query'
}

foreach ($queryText in $Query) {
    $handles = Find-WindowHandles -Needle $queryText
    if ($handles.Count -eq 0) {
        throw 'no matching on-screen window found'
    }
    # Minimized windows have no bitmap (PrintWindow returns black); drop them so a
    # minimized duplicate cannot block a visible match, mirroring macOS/Linux.
    $capturable = @($handles | Where-Object { -not [Win32Capture]::IsIconic($_) })
    if ($capturable.Count -eq 0) {
        [Console]::Error.WriteLine("window_not_capturable: '$queryText' is minimized — restore it and retry.")
        exit 75
    }
    if ($capturable.Count -gt 1 -and -not $AllowMultipleMatches) {
        throw 'multiple matching windows found; ask for a more specific target'
    }
    foreach ($handle in $capturable) {
        Capture-ToDestination -Bounds (Get-WindowBounds -Handle $handle) -Label $queryText -Handle $handle
    }
}
