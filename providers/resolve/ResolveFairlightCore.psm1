# Trusted helper source, loaded in the fixed entrypoint's script scope.
# Loading this file defines functions only: no discovery, capture, input, or UI.
function Fail-Fairlight([string]$Code) { throw ('FLUI_' + $Code) }

function Test-FairlightName($Value, [int]$Maximum = 128) {
    return $Value -is [string] -and $Value.Length -gt 0 -and $Value.Length -le $Maximum -and
        $Value -ceq $Value.Trim() -and $Value -notmatch '[\x00-\x1f\x7f]'
}

function Test-FairlightInteger($Value, [int]$Minimum, [int]$Maximum) {
    return ($Value -is [int] -or $Value -is [long]) -and $Value -ge $Minimum -and $Value -le $Maximum
}

function Assert-FairlightKeys($Object, [string[]]$Keys) {
    if ($null -eq $Object -or $Object -isnot [pscustomobject] -or
        ((@($Object.PSObject.Properties.Name | Sort-Object) -join ',') -cne
        (@($Keys | Sort-Object) -join ','))) { Fail-Fairlight 'INVALID_REQUEST' }
}

function Assert-FairlightJsonKeys([string]$Text) {
    # ConvertFrom-Json overwrites duplicate keys. Reject those before decoding,
    # including escaped/case-variant spellings, while respecting string escapes.
    $objects = New-Object 'System.Collections.Generic.Stack[hashtable]'
    for ($i=0; $i -lt $Text.Length; $i++) {
        if ($Text[$i] -ceq '{') { $objects.Push(@{}); continue }
        if ($Text[$i] -ceq '}') { if ($objects.Count -gt 0) { $null=$objects.Pop() }; continue }
        if ($Text[$i] -cne '"') { continue }
        $start=$i; $i++
        while ($i -lt $Text.Length -and $Text[$i] -cne '"') {
            if ($Text[$i] -ceq '\') { $i++ }
            $i++
        }
        if ($i -ge $Text.Length) { Fail-Fairlight 'INVALID_REQUEST' }
        $next=$i+1
        while ($next -lt $Text.Length -and [char]::IsWhiteSpace($Text[$next])) { $next++ }
        if ($next -lt $Text.Length -and $Text[$next] -ceq ':' -and $objects.Count -gt 0) {
            try { $key=ConvertFrom-Json -InputObject $Text.Substring($start,$i-$start+1) -ErrorAction Stop }
            catch { Fail-Fairlight 'INVALID_REQUEST' }
            $current=$objects.Peek()
            if ($current.ContainsKey($key)) { Fail-Fairlight 'INVALID_REQUEST' }
            $current[$key]=$true
        }
    }
}

function ConvertTo-FairlightRequest([string]$Text) {
    if (-not $Text -or $Text.Length -gt 8192) { Fail-Fairlight 'INVALID_REQUEST' }
    Assert-FairlightJsonKeys $Text
    try { $request = ConvertFrom-Json -InputObject $Text -ErrorAction Stop } catch { Fail-Fairlight 'INVALID_REQUEST' }
    $base = @('operation','expected_project_name','target')
    if ($request -isnot [pscustomobject] -or $null -eq $request.PSObject.Properties['operation']) {
        Fail-Fairlight 'INVALID_REQUEST'
    }
    switch -CaseSensitive ($request.operation) {
        'inspect_processing' { Assert-FairlightKeys $request ($base + @('module')) }
        'set_processing' { Assert-FairlightKeys $request ($base + @('module','enabled')) }
        'inspect_arm' { Assert-FairlightKeys $request $base }
        'set_arm' { Assert-FairlightKeys $request ($base + @('armed')) }
        'inspect_patch' { Assert-FairlightKeys $request $base }
        'set_patch' { Assert-FairlightKeys $request ($base + @('source_name','source_channel')) }
        default { Fail-Fairlight 'INVALID_OPERATION' }
    }
    if (-not (Test-FairlightName $request.expected_project_name)) { Fail-Fairlight 'INVALID_REQUEST' }
    Assert-FairlightKeys $request.target @('timeline_name','scope','name','index','audio_track_count')
    $target = $request.target
    if (-not (Test-FairlightName $target.timeline_name) -or -not (Test-FairlightName $target.name) -or
        $target.scope -cnotin @('track','bus')) { Fail-Fairlight 'INVALID_REQUEST' }
    if ($target.scope -ceq 'track') {
        if (-not (Test-FairlightInteger $target.index 1 64) -or
            -not (Test-FairlightInteger $target.audio_track_count 1 64) -or
            $target.audio_track_count -lt $target.index) { Fail-Fairlight 'INVALID_REQUEST' }
    } elseif ($null -ne $target.index -or $null -ne $target.audio_track_count) { Fail-Fairlight 'INVALID_REQUEST' }
    if ($request.operation -in @('inspect_arm','set_arm','inspect_patch','set_patch') -and
        $target.scope -cne 'track') { Fail-Fairlight 'TRACK_REQUIRED' }
    if ($request.operation -in @('inspect_processing','set_processing') -and
        $request.module -cnotin @('eq','dynamics')) { Fail-Fairlight 'INVALID_REQUEST' }
    if ($request.operation -ceq 'set_processing' -and $request.enabled -isnot [bool]) { Fail-Fairlight 'INVALID_REQUEST' }
    if ($request.operation -ceq 'set_arm' -and $request.armed -isnot [bool]) { Fail-Fairlight 'INVALID_REQUEST' }
    if ($request.operation -ceq 'set_patch' -and
        (-not (Test-FairlightName $request.source_name) -or
        -not (Test-FairlightInteger $request.source_channel 1 64))) { Fail-Fairlight 'INVALID_REQUEST' }
    return $request
}

function Initialize-FairlightRuntime {
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName WindowsBase
    Add-Type -AssemblyName System.Drawing
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    if (-not ('FairlightNative' -as [type])) {
        Add-Type -ReferencedAssemblies System.Drawing @'
using System;
using System.Drawing;
using System.Runtime.InteropServices;
[StructLayout(LayoutKind.Sequential)] public struct FairlightPoint { public int X,Y; }
public static class FairlightNative {
    [DllImport("user32.dll")] public static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);
    [DllImport("user32.dll")] public static extern IntPtr GetThreadDpiAwarenessContext();
    [DllImport("user32.dll")] public static extern bool AreDpiAwarenessContextsEqual(IntPtr first,IntPtr second);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr handle, out uint id);
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x,int y);
    [DllImport("user32.dll")] private static extern bool GetPhysicalCursorPos(out FairlightPoint point);
    [DllImport("user32.dll")] private static extern void mouse_event(uint flags,uint x,uint y,uint data,UIntPtr extra);
    public static FairlightPoint Position() {
        FairlightPoint value;
        if (!GetPhysicalCursorPos(out value)) throw new InvalidOperationException("FLUI_CURSOR_UNAVAILABLE");
        return value;
    }
    public static void Click(bool twice) {
        for (int i=0;i<(twice?2:1);i++) {
            if (i>0) System.Threading.Thread.Sleep(90);
            mouse_event(2,0,0,0,UIntPtr.Zero);
            try { System.Threading.Thread.Sleep(80); }
            finally { mouse_event(4,0,0,0,UIntPtr.Zero); }
        }
    }
}
public static class FairlightVision {
    private static bool Dark(Color c) { return c.R<35 && c.G<35 && c.B<35; }
    private static bool Red(Color c) { return c.R>130 && c.R>c.G*1.65 && c.R>c.B*1.65; }
    private static bool Grey(Color c) { return c.R>=80 && c.R<=205 && Math.Abs(c.R-c.G)<18 && Math.Abs(c.G-c.B)<18; }
    // Positive patterns for BOTH positions; absence of red is never an off proof.
    public static int ToggleState(Bitmap image, Rectangle box) {
        if (!new Rectangle(0,0,image.Width,image.Height).Contains(box) || box.Width<12 || box.Height<7) return -1;
        int[] red={0,0}, grey={0,0}, dark={0,0}, count={0,0};
        for (int y=box.Top+box.Height/5;y<box.Bottom-box.Height/5;y++)
        for (int x=box.Left+box.Width/8;x<box.Right-box.Width/8;x++) {
            int side=x<box.Left+box.Width/2?0:1; Color c=image.GetPixel(x,y); count[side]++;
            if (Red(c)) red[side]++; if (Grey(c)) grey[side]++; if (Dark(c)) dark[side]++;
        }
        if (count[0]==0 || count[1]==0) return -1;
        if (red[1]>count[1]*.12 && red[0]<count[0]*.04 && dark[0]>count[0]*.35) return 1;
        if (grey[0]>count[0]*.12 && grey[1]<count[1]*.06 && red[0]+red[1]==0 && dark[1]>count[1]*.45) return 0;
        return -1;
    }
    public static int ArmState(Bitmap image, Rectangle box) {
        if (!new Rectangle(0,0,image.Width,image.Height).Contains(box) || box.Width<10 || box.Height<10) return -1;
        int red=0, body=0, glyph=0, border=0, count=0;
        for (int y=box.Top;y<box.Bottom;y++) for (int x=box.Left;x<box.Right;x++) {
            Color c=image.GetPixel(x,y); count++;
            if (Red(c)) red++;
            if (c.R>=50 && c.R<=90 && Math.Abs(c.R-c.G)<12 && Math.Abs(c.G-c.B)<12) body++;
            if (c.R>=115 && Math.Abs(c.R-c.G)<20 && Math.Abs(c.G-c.B)<20) glyph++;
            if (Dark(c)) border++;
        }
        if (red>count*.20 && glyph>count*.015) return 1;
        if (red==0 && body>count*.30 && glyph>count*.015 && border>count*.025) return 0;
        return -1;
    }
    public static bool PageUnderline(Bitmap image, Rectangle text) {
        int left=Math.Max(0,text.Left),right=Math.Min(image.Width,text.Right);
        if (right-left<12) return false;
        for (int y=text.Bottom+1;y<Math.Min(image.Height,text.Bottom+18);y++) {
            int n=0;
            for (int x=left;x<right;x++) {
                Color c=image.GetPixel(x,y);
                if (c.R>150 && c.G>35 && c.G<145 && c.B<80) n++;
            }
            if (n>(right-left)*.75) return true;
        }
        return false;
    }
    // Locate a complete dark frame with a painted EQ line or dynamics diagonal.
    // Caller supplies a column and row derived from OCR, never desktop constants.
    public static Rectangle Graph(Bitmap image, Rectangle search, int columnWidth, bool equalizer) {
        Rectangle clip=Rectangle.Intersect(search,new Rectangle(0,0,image.Width,image.Height));
        Rectangle best=Rectangle.Empty; int bestScore=-1; bool ambiguous=false;
        for (int y=clip.Top;y<clip.Bottom-8;y++) for (int x=clip.Left;x<clip.Right-12;x++) {
            if (!Dark(image.GetPixel(x,y))) continue;
            int end=x;
            while (end<clip.Right && Dark(image.GetPixel(end,y))) end++;
            int w=end-x;
            if (w<columnWidth*.65 || w>columnWidth*.97) { x=Math.Max(x,end-1); continue; }
            int minH=(int)(columnWidth*(equalizer?.22:.65));
            int maxH=(int)(columnWidth*(equalizer?.55:1.05));
            for (int h=Math.Max(10,minH);h<=maxH && y+h<clip.Bottom;h++) {
                int edges=0,total=0,paint=0,inside=0;
                for (int dx=0;dx<w;dx++) { total++; if(Dark(image.GetPixel(x+dx,y+h)))edges++; }
                for (int dy=0;dy<=h;dy++) {
                    total+=2;
                    if(Dark(image.GetPixel(x,y+dy)))edges++;
                    if(Dark(image.GetPixel(end-1,y+dy)))edges++;
                }
                if (edges<total*.80) continue;
                for(int dy=2;dy<h-1;dy++) for(int dx=2;dx<w-2;dx++) {
                    Color c=image.GetPixel(x+dx,y+dy);
                    if (c.R>=35 && c.R<100 && c.G>=35 && c.G<100 && c.B>=35 && c.B<100) inside++;
                    if(equalizer ? (c.G>110 && c.B>110 && c.R<c.G*.65) :
                        ((c.R>95 && Math.Abs(c.R-c.G)<20 && Math.Abs(c.G-c.B)<20) || (c.G>100 && c.R>60 && c.B<c.G*.65))) paint++;
                }
                if (inside<w*h*.35 || paint<Math.Min(w,h)*.40) continue;
                int score=edges+paint;
                Rectangle candidate=new Rectangle(x,y,w,h+1);
                if (best.IsEmpty || score>bestScore) {
                    if(!best.IsEmpty && !best.IntersectsWith(candidate)) ambiguous=true;
                    best=candidate; bestScore=score;
                } else if(!best.IntersectsWith(candidate)) ambiguous=true;
            }
            x=Math.Max(x,end-1);
        }
        return ambiguous?Rectangle.Empty:best;
    }
}
'@
    }
}

function Initialize-FairlightDpi {
    try {
        $previous = [FairlightNative]::SetThreadDpiAwarenessContext([IntPtr]::new(-4))
        if ($previous -eq [IntPtr]::Zero) { Fail-Fairlight 'DPI_CONTEXT_UNAVAILABLE' }
        $script:FairlightPreviousDpiContext = $previous
        Assert-FairlightDpi
    } catch { Fail-Fairlight 'DPI_CONTEXT_UNAVAILABLE' }
}

function Assert-FairlightDpi {
    try {
        if (-not [FairlightNative]::AreDpiAwarenessContextsEqual(
            [FairlightNative]::GetThreadDpiAwarenessContext(),[IntPtr]::new(-4))) {
            Fail-Fairlight 'DPI_CONTEXT_UNVERIFIED'
        }
    } catch { Fail-Fairlight 'DPI_CONTEXT_UNVERIFIED' }
}

function Restore-FairlightDpi {
    if ($script:FairlightPreviousDpiContext -ne [IntPtr]::Zero) {
        try { $null = [FairlightNative]::SetThreadDpiAwarenessContext($script:FairlightPreviousDpiContext) }
        catch { <# The dedicated driver process is exiting; no further input. #> }
        $script:FairlightPreviousDpiContext = [IntPtr]::Zero
    }
}

function Get-FairlightDescendants($Scope) {
    Assert-FairlightDpi
    return $Scope.FindAll([System.Windows.Automation.TreeScope]::Descendants,
        [System.Windows.Automation.Condition]::TrueCondition)
}

function Get-FairlightMain {
    Assert-FairlightDpi
    $title = 'DaVinci Resolve - ' + $script:FairlightContext.expected_project_name
    $found = @([System.Windows.Automation.AutomationElement]::RootElement.FindAll(
        [System.Windows.Automation.TreeScope]::Children,
        [System.Windows.Automation.PropertyCondition]::new(
            [System.Windows.Automation.AutomationElement]::NameProperty, $title)) |
        Where-Object { $_.Current.ClassName -ceq 'UiMainWindowImp' -and -not $_.Current.IsOffscreen })
    if ($found.Count -ne 1) { Fail-Fairlight 'PROJECT_WINDOW_UNAVAILABLE' }
    $main = $found[0]
    $process = Get-Process -Id $main.Current.ProcessId -ErrorAction Stop
    if ($process.ProcessName -cne 'Resolve' -or
        [IO.Path]::GetFileName($process.MainModule.FileName) -ine 'Resolve.exe') { Fail-Fairlight 'WRONG_PROCESS' }
    if ($script:BoundProcessId -ne 0 -and ($main.Current.ProcessId -ne $script:BoundProcessId -or
        $main.Current.NativeWindowHandle -ne $script:BoundWindowHandle)) { Fail-Fairlight 'PROJECT_WINDOW_CHANGED' }
    return $main
}

function Find-FairlightControl($Scope, [string[]]$Names, [string]$Type, [string]$Class = '') {
    $found = @(Get-FairlightDescendants $Scope | Where-Object {
        $_.Current.ProcessId -eq $script:BoundProcessId -and -not $_.Current.IsOffscreen -and
        $_.Current.Name -cin $Names -and
        $_.Current.ControlType.ProgrammaticName -ceq ('ControlType.' + $Type) -and
        (-not $Class -or $_.Current.ClassName -ceq $Class)
    })
    if ($found.Count -ne 1) { Fail-Fairlight 'CONTROL_UNAVAILABLE_OR_AMBIGUOUS' }
    return $found[0]
}

function Get-FairlightOwnedWindows {
    $main = Get-FairlightMain
    $all = @(Get-FairlightDescendants $main) + @(
        [System.Windows.Automation.AutomationElement]::RootElement.FindAll(
            [System.Windows.Automation.TreeScope]::Children,
            [System.Windows.Automation.PropertyCondition]::new(
                [System.Windows.Automation.AutomationElement]::ProcessIdProperty, $script:BoundProcessId)))
    $seen = @{}
    foreach ($item in $all) {
        if ($item.Current.IsOffscreen -or $item.Current.ProcessId -ne $script:BoundProcessId -or
            $item.Current.ControlType -ne [System.Windows.Automation.ControlType]::Window -or
            [System.Windows.Automation.Automation]::Compare($item,$main)) { continue }
        $id = $item.GetRuntimeId() -join ':'
        if (-not $seen.ContainsKey($id)) { $seen[$id] = $true; $item }
    }
}

function Get-FairlightTransport {
    $all = Get-FairlightDescendants (Get-FairlightMain)
    $result = @{}
    foreach ($pair in @(@('recording','Record'),@('playing','Play'),@('stopped','Stop'))) {
        $suffix = '.playbackControlsBar.UiTransportControlsWidget.btnTransport' + $pair[1]
        $found = @($all | Where-Object { $_.Current.AutomationId.EndsWith($suffix) -and
            $_.Current.ProcessId -eq $script:BoundProcessId -and -not $_.Current.IsOffscreen })
        if ($found.Count -ne 1) { Fail-Fairlight 'TRANSPORT_UNAVAILABLE' }
        $toggle = $null
        if (-not $found[0].TryGetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern,[ref]$toggle)) {
            Fail-Fairlight 'TRANSPORT_STATE_UNAVAILABLE'
        }
        $state = $toggle.Current.ToggleState
        if ($state -eq [System.Windows.Automation.ToggleState]::Indeterminate) { Fail-Fairlight 'TRANSPORT_STATE_UNAVAILABLE' }
        $result[$pair[0]] = $state -eq [System.Windows.Automation.ToggleState]::On
    }
    $result.verification = 'windows_uia_toggle_state'
    return $result
}

function Assert-FairlightStopped {
    $state = Get-FairlightTransport
    if ($state.recording -or $state.playing -or -not $state.stopped) { Fail-Fairlight 'TRANSPORT_NOT_STOPPED' }
}

function Get-FairlightCapture($Scope, [int]$Scale = 1) {
    Assert-FairlightDpi
    if ($Scope.Current.ProcessId -ne $script:BoundProcessId -or $Scope.Current.IsOffscreen) {
        Fail-Fairlight 'CAPTURE_SCOPE_UNAVAILABLE'
    }
    $rectangle = $Scope.Current.BoundingRectangle
    if ($rectangle.IsEmpty -or $rectangle.Width -lt 2 -or $rectangle.Height -lt 2 -or
        $rectangle.Width -gt 8192 -or $rectangle.Height -gt 8192) { Fail-Fairlight 'TARGET_GEOMETRY_UNAVAILABLE' }
    # Capture only the verified process window/container, in memory. No image is saved.
    Assert-FairlightForeground
    $bitmap = New-Object System.Drawing.Bitmap ([int]$rectangle.Width),([int]$rectangle.Height)
    $graphics = [Drawing.Graphics]::FromImage($bitmap)
    try { $graphics.CopyFromScreen([int]$rectangle.X,[int]$rectangle.Y,0,0,$bitmap.Size) }
    finally { $graphics.Dispose() }
    if ($Scale -ne 1) {
        $large = New-Object System.Drawing.Bitmap ($bitmap.Width*$Scale),($bitmap.Height*$Scale)
        $graphics = [Drawing.Graphics]::FromImage($large)
        try {
            $graphics.InterpolationMode = [Drawing.Drawing2D.InterpolationMode]::NearestNeighbor
            $graphics.DrawImage($bitmap,0,0,$large.Width,$large.Height)
        } finally { $graphics.Dispose(); $bitmap.Dispose() }
        $bitmap = $large
    }
    return @{ Bitmap = $bitmap; X = [int]$rectangle.X; Y = [int]$rectangle.Y; Scale = $Scale }
}

function Wait-FairlightWinRT($Operation, [type]$ResultType) {
    $method = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.IsGenericMethodDefinition -and
        $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    } | Select-Object -First 1
    if ($null -eq $method) { Fail-Fairlight 'OCR_UNAVAILABLE' }
    $task = $method.MakeGenericMethod($ResultType).Invoke($null,@($Operation))
    if (-not $task.Wait(10000)) { Fail-Fairlight 'OCR_TIMEOUT' }
    return $task.Result
}

function Get-FairlightOcr($Scope) {
    $capture = $null
    $memory = $null
    $random = $null
    $software = $null
    try {
        $null = [Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]
        $null = [Windows.Globalization.Language,Windows.Foundation,ContentType=WindowsRuntime]
        $null = [Windows.Graphics.Imaging.BitmapDecoder,Windows.Foundation,ContentType=WindowsRuntime]
        $null = [Windows.Graphics.Imaging.SoftwareBitmap,Windows.Foundation,ContentType=WindowsRuntime]
        $null = [Windows.Storage.Streams.IRandomAccessStream,Windows.Foundation,ContentType=WindowsRuntime]
        $language = [Windows.Globalization.Language]::new('en-US')
        $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($language)
        if ($null -eq $engine) { Fail-Fairlight 'OCR_UNAVAILABLE' }
        $capture = Get-FairlightCapture $Scope 2
        if ($capture.Bitmap.Width -gt [Windows.Media.Ocr.OcrEngine]::MaxImageDimension -or
            $capture.Bitmap.Height -gt [Windows.Media.Ocr.OcrEngine]::MaxImageDimension) { Fail-Fairlight 'OCR_SCOPE_TOO_LARGE' }
        $memory = New-Object IO.MemoryStream
        $capture.Bitmap.Save($memory,[Drawing.Imaging.ImageFormat]::Png)
        $memory.Position = 0
        $random = [System.IO.WindowsRuntimeStreamExtensions]::AsRandomAccessStream($memory)
        $decoder = Wait-FairlightWinRT ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($random)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $software = Wait-FairlightWinRT ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
        $ocr = Wait-FairlightWinRT ($engine.RecognizeAsync($software)) ([Windows.Media.Ocr.OcrResult])
        $lines = @()
        $words = @()
        foreach ($line in $ocr.Lines) {
            $lineWords = @()
            foreach ($word in $line.Words) {
                $box = $word.BoundingRect
                $value = @{ Text = [string]$word.Text; X = $capture.X+$box.X/2; Y = $capture.Y+$box.Y/2;
                    Width = $box.Width/2; Height = $box.Height/2 }
                $lineWords += $value; $words += $value
            }
            if ($lineWords.Count -gt 0) {
                $left = ($lineWords | ForEach-Object { $_.X } | Measure-Object -Minimum).Minimum
                $top = ($lineWords | ForEach-Object { $_.Y } | Measure-Object -Minimum).Minimum
                $right = ($lineWords | ForEach-Object { $_.X+$_.Width } | Measure-Object -Maximum).Maximum
                $bottom = ($lineWords | ForEach-Object { $_.Y+$_.Height } | Measure-Object -Maximum).Maximum
                $lines += @{ Text = [string]$line.Text; X=$left; Y=$top; Width=$right-$left; Height=$bottom-$top; Words=$lineWords }
            }
        }
        return @{ Lines = $lines; Words = $words }
    } catch {
        if ($_.Exception.Message -match 'FLUI_') { throw }
        Fail-Fairlight 'OCR_UNAVAILABLE'
    } finally {
        if ($null -ne $software) { $software.Dispose() }
        if ($null -ne $random) { $random.Dispose() }
        if ($null -ne $memory) { $memory.Dispose() }
        if ($null -ne $capture) { $capture.Bitmap.Dispose() }
    }
}

function Find-FairlightText($Ocr, [string]$Text) {
    # Match complete consecutive OCR words, never a substring of another name.
    foreach ($line in $Ocr.Lines) {
        $words = @($line.Words)
        for ($first=0; $first -lt $words.Count; $first++) {
            $joined = ''
            for ($last=$first; $last -lt $words.Count; $last++) {
                if ($last -gt $first) { $joined += ' ' }
                $joined += $words[$last].Text
                if ($joined -ceq $Text) {
                    $top = ($words[$first..$last] | ForEach-Object { $_.Y } | Measure-Object -Minimum).Minimum
                    $bottom = ($words[$first..$last] | ForEach-Object { $_.Y+$_.Height } | Measure-Object -Maximum).Maximum
                    @{ Text=$Text; X=$words[$first].X; Y=$top;
                        Width=$words[$last].X+$words[$last].Width-$words[$first].X; Height=$bottom-$top }
                }
                if ($joined.Length -ge $Text.Length) { break }
            }
        }
    }
}

function Find-FairlightExactLine($Ocr,[string]$Text) {
    foreach ($line in $Ocr.Lines) {
        if ((@($line.Words | ForEach-Object { $_.Text }) -join ' ') -ceq $Text) {
            Find-FairlightText @{ Lines=@($line) } $Text
        }
    }
}

function Test-FairlightLabelInColumn($Ocr,$Label,[double]$Center,[double]$Width,[string]$Text) {
    $words = @($Ocr.Words | Where-Object {
        [Math]::Abs(($_.Y+$_.Height/2)-($Label.Y+$Label.Height/2)) -lt [Math]::Max(3,$Label.Height*.65) -and
        $_.X+$_.Width/2 -gt $Center-$Width/2 -and $_.X+$_.Width/2 -lt $Center+$Width/2
    } | Sort-Object { $_.X })
    # Extra words and ellipses are material: a prefix never proves a target name.
    return (@($words | ForEach-Object { $_.Text }) -join ' ') -ceq $Text
}

function Assert-FairlightForeground {
    [uint32]$foregroundId = 0
    [void][FairlightNative]::GetWindowThreadProcessId([FairlightNative]::GetForegroundWindow(),[ref]$foregroundId)
    if ($foregroundId -ne $script:BoundProcessId) { Fail-Fairlight 'FOCUS_LOST' }
}

function Assert-FairlightPage($Main) {
    $buttons = @(Get-FairlightDescendants $Main | Where-Object {
        $_.Current.Name -ceq 'Fairlight' -and -not $_.Current.IsOffscreen -and
        $_.Current.ProcessId -eq $script:BoundProcessId -and
        $_.Current.ControlType -ne [System.Windows.Automation.ControlType]::MenuItem
    })
    foreach ($button in $buttons) {
        $selection = $null; $toggle = $null
        if ($button.TryGetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern,[ref]$selection) -and
            $selection.Current.IsSelected) { return }
        if ($button.TryGetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern,[ref]$toggle) -and
            $toggle.Current.ToggleState -eq [System.Windows.Automation.ToggleState]::On) { return }
    }
    $ocr = Get-FairlightOcr $Main
    $mainBox = $Main.Current.BoundingRectangle
    $labels = @(Find-FairlightText $ocr 'Fairlight' | Where-Object { $_.Y -gt $mainBox.Y+$mainBox.Height*.90 })
    if ($labels.Count -ne 1) { Fail-Fairlight 'FAIRLIGHT_PAGE_UNVERIFIED' }
    $capture = Get-FairlightCapture $Main
    try {
        $label = $labels[0]
        $box = [Drawing.Rectangle]::new([int]($label.X-$capture.X),[int]($label.Y-$capture.Y),[int]$label.Width,[int]$label.Height)
        if (-not [FairlightVision]::PageUnderline($capture.Bitmap,$box)) { Fail-Fairlight 'FAIRLIGHT_PAGE_UNVERIFIED' }
    } finally { $capture.Bitmap.Dispose() }
}

function Assert-FairlightTimeline($Main,[string]$ExpectedName) {
    $all = @(Get-FairlightDescendants $Main | Where-Object {
        $_.Current.ProcessId -eq $script:BoundProcessId -and -not $_.Current.IsOffscreen
    })
    # This fixed Resolve selector displays the CURRENT timeline. An arbitrary
    # button with the same name may merely be an inactive open timeline tab.
    $selectors = @($all | Where-Object {
        $_.Current.AutomationId.EndsWith('.m_CbTimelineList') -and
        $_.Current.ControlType.ProgrammaticName -cin @('ControlType.Button','ControlType.ComboBox')
    })
    if ($selectors.Count -gt 1) { Fail-Fairlight 'CURRENT_TIMELINE_AMBIGUOUS' }
    if ($selectors.Count -eq 1) {
        $selector=$selectors[0]
        $selection=$null; $value=$null
        if ($selector.TryGetCurrentPattern([System.Windows.Automation.SelectionPattern]::Pattern,[ref]$selection)) {
            $items=@($selection.Current.GetSelection())
            if ($items.Count -ne 1 -or $items[0].Current.ProcessId -ne $script:BoundProcessId -or
                $items[0].Current.Name -cne $ExpectedName) { Fail-Fairlight 'CURRENT_TIMELINE_MISMATCH' }
            return
        }
        if ($selector.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern,[ref]$value)) {
            if ($value.Current.Value -cne $ExpectedName) { Fail-Fairlight 'CURRENT_TIMELINE_MISMATCH' }
            return
        }
        # Qt can expose the fixed current selector as a Button with its displayed
        # value in Name, without either pattern. The fixed ID+role is essential.
        if ($selector.Current.Name -cne $ExpectedName) { Fail-Fairlight 'CURRENT_TIMELINE_MISMATCH' }
        return
    }
    # A selected TabItem can belong to any pane. Until a timeline tab-bar anchor
    # has its own verified profile, only the fixed current selector proves this.
    Fail-Fairlight 'CURRENT_TIMELINE_UNVERIFIED'
}

function Assert-FairlightContext {
    $main = Get-FairlightMain
    Assert-FairlightForeground
    Assert-FairlightStopped
    Assert-FairlightPage $main
    Assert-FairlightTimeline $main $script:FairlightContext.target.timeline_name
}

function Initialize-FairlightContext($Request) {
    $script:FairlightContext = $Request
    $script:FairlightOwnedDialogs = @{}
    Initialize-FairlightRuntime
    Initialize-FairlightDpi
    $main = Get-FairlightMain
    $script:BoundProcessId = $main.Current.ProcessId
    $script:BoundWindowHandle = $main.Current.NativeWindowHandle
    $version = (Get-Process -Id $script:BoundProcessId).MainModule.FileVersionInfo.ProductVersion
    if ($version -notmatch '^21\.1(?:\.|\s|$)') { Fail-Fairlight 'UNSUPPORTED_RESOLVE_PROFILE' }
    # Existing dialogs can carry uncommitted user state. Do not adopt them.
    if (@(Get-FairlightOwnedWindows).Count -ne 0) { Fail-Fairlight 'EXISTING_DIALOG' }
    Assert-FairlightContext
}

function Mark-FairlightMutation { $script:FairlightMutationTouched = $true }

function Test-FairlightSameElement($Left,$Right) {
    return [System.Windows.Automation.Automation]::Compare($Left,$Right)
}

function Test-FairlightDescendant($Item,$Scope) {
    $ancestor = $Item
    for ($depth=0; $depth -lt 24 -and $null -ne $ancestor; $depth++) {
        if (Test-FairlightSameElement $ancestor $Scope) { return $true }
        $ancestor = [System.Windows.Automation.TreeWalker]::RawViewWalker.GetParent($ancestor)
    }
    return $false
}

function Test-FairlightTransportElement($Item) {
    $ancestor = $Item
    for ($depth=0; $depth -lt 24 -and $null -ne $ancestor; $depth++) {
        if ($ancestor.Current.AutomationId -match 'btnTransport' -or $ancestor.Current.Name -ceq 'Record') { return $true }
        $ancestor = [System.Windows.Automation.TreeWalker]::RawViewWalker.GetParent($ancestor)
    }
    return $false
}

function Invoke-FairlightHit($Scope,[int]$X,[int]$Y,[switch]$Mutation,[switch]$DoubleClick) {
    Assert-FairlightDpi
    Assert-FairlightContext
    if ($Scope.Current.ProcessId -ne $script:BoundProcessId -or $Scope.Current.IsOffscreen -or
        -not $Scope.Current.IsEnabled -or $Scope.Current.Name -ceq 'Record' -or
        $Scope.Current.AutomationId -match 'btnTransport') { Fail-Fairlight 'FORBIDDEN_OR_DISABLED_TARGET' }
    if (Test-FairlightTransportElement $Scope) { Fail-Fairlight 'FORBIDDEN_TRANSPORT_TARGET' }
    $rectangle = $Scope.Current.BoundingRectangle
    if ($rectangle.IsEmpty -or $rectangle.Width -lt 2 -or $rectangle.Height -lt 2 -or
        $X -le $rectangle.Left -or $X -ge $rectangle.Right -or
        $Y -le $rectangle.Top -or $Y -ge $rectangle.Bottom) { Fail-Fairlight 'TARGET_GEOMETRY_UNAVAILABLE' }
    $before = [FairlightNative]::Position()
    Start-Sleep -Milliseconds 120
    $stable = [FairlightNative]::Position()
    if ([Math]::Abs($before.X-$stable.X) -gt 3 -or [Math]::Abs($before.Y-$stable.Y) -gt 3) {
        Fail-Fairlight 'CURSOR_INTERFERENCE'
    }
    if (-not [FairlightNative]::SetCursorPos($X,$Y)) { Fail-Fairlight 'CURSOR_MOVE_FAILED' }
    Start-Sleep -Milliseconds 120
    $position = [FairlightNative]::Position()
    if ([Math]::Abs($position.X-$X) -gt 3 -or [Math]::Abs($position.Y-$Y) -gt 3) { Fail-Fairlight 'CURSOR_CLIPPED_OR_MOVED' }
    $hit = [System.Windows.Automation.AutomationElement]::FromPoint([System.Windows.Point]::new($position.X,$position.Y))
    if ($hit.Current.ProcessId -ne $script:BoundProcessId -or (Test-FairlightTransportElement $hit) -or
        -not (Test-FairlightDescendant $hit $Scope)) { Fail-Fairlight 'WRONG_TARGET' }
    Assert-FairlightForeground
    Assert-FairlightStopped
    $last = [FairlightNative]::Position()
    if ([Math]::Abs($last.X-$position.X) -gt 3 -or [Math]::Abs($last.Y-$position.Y) -gt 3) { Fail-Fairlight 'CURSOR_INTERFERENCE' }
    if ($Mutation) { Mark-FairlightMutation }
    [FairlightNative]::Click([bool]$DoubleClick)
    Start-Sleep -Milliseconds 180
}

function Invoke-FairlightControl($Target,[switch]$Mutation) {
    $rectangle = $Target.Current.BoundingRectangle
    Invoke-FairlightHit $Target ([int]($rectangle.X+$rectangle.Width/2)) ([int]($rectangle.Y+$rectangle.Height/2)) -Mutation:$Mutation
}

function Invoke-FairlightPoint($Scope,[int]$X,[int]$Y,[switch]$Mutation,[switch]$DoubleClick) {
    # A point action is restricted to a proven process graph or owned dialog.
    $id = $Scope.GetRuntimeId() -join ':'
    $graph = $Scope.Current.ClassName -match 'Mixer|QGraphicsView|TrackControls' -or
        $Scope.Current.AutomationId.EndsWith('FL TrackControls')
    if (Test-FairlightSameElement $Scope (Get-FairlightMain)) { Fail-Fairlight 'UNSCOPED_POINT_FORBIDDEN' }
    if (-not $graph -and -not $script:FairlightOwnedDialogs.ContainsKey($id)) { Fail-Fairlight 'UNSCOPED_POINT_FORBIDDEN' }
    Invoke-FairlightHit $Scope $X $Y -Mutation:$Mutation -DoubleClick:$DoubleClick
}

function Register-FairlightDialog($Window) {
    if ($Window.Current.ProcessId -ne $script:BoundProcessId -or $Window.Current.ClassName -cnotin
        @('UiFLAudioEqualizerDialogImp','UiDynamicsControlDlgImp','UiFLPatchIODialogImp')) { Fail-Fairlight 'UNEXPECTED_DIALOG' }
    $script:FairlightOwnedDialogs[($Window.GetRuntimeId() -join ':')] = $Window
}

function Invoke-FairlightActionButton($Button,[switch]$Mutation) {
    if ($Button.Current.ClassName -cne 'QPushButton' -or $Button.Current.Name -cnotin @('Patch','Close','Cancel')) {
        Fail-Fairlight 'ACTION_BUTTON_FORBIDDEN'
    }
    $owned = $false
    foreach ($dialog in $script:FairlightOwnedDialogs.Values) {
        if (Test-FairlightDescendant $Button $dialog) { $owned=$true; break }
    }
    if (-not $owned -or ($Button.Current.Name -ceq 'Patch' -and -not $Mutation)) { Fail-Fairlight 'ACTION_BUTTON_FORBIDDEN' }
    Invoke-FairlightControl $Button -Mutation:$Mutation
}

function Close-FairlightDialog($Window) {
    $id = $Window.GetRuntimeId() -join ':'
    if (-not $script:FairlightOwnedDialogs.ContainsKey($id)) { Fail-Fairlight 'UNOWNED_DIALOG' }
    Assert-FairlightContext
    $pattern = $null
    if (-not $Window.TryGetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern,[ref]$pattern)) {
        $close = Find-FairlightControl $Window @('Close') 'Button' 'QPushButton'
        Invoke-FairlightActionButton $close
    } else { $pattern.Close(); Start-Sleep -Milliseconds 180 }
    if (@(Get-FairlightOwnedWindows | Where-Object { ($_.GetRuntimeId() -join ':') -ceq $id }).Count -ne 0) {
        Fail-Fairlight 'DIALOG_CLOSE_UNVERIFIED'
    }
    $script:FairlightOwnedDialogs.Remove($id)
}

function Close-FairlightOwnedDialogs {
    foreach ($dialog in @($script:FairlightOwnedDialogs.Values)) {
        try { Close-FairlightDialog $dialog } catch { <# Preserve original failure; no blind cleanup input. #> }
    }
}

function Open-FairlightMenuItem([string]$MenuName,[string]$ItemName) {
    $allowed = ($MenuName -ceq 'Fairlight' -and $ItemName -ceq 'Patch Input/Output...') -or
        ($MenuName -ceq 'File' -and $ItemName -ceq 'Save Project')
    if (-not $allowed) { Fail-Fairlight 'MENU_ACTION_FORBIDDEN' }
    Invoke-FairlightControl (Find-FairlightControl (Get-FairlightMain) @($MenuName) 'MenuItem')
    $names = @($ItemName)
    if ($ItemName -ceq 'Patch Input/Output...') { $names += 'Patch Input/Output'; $names += 'Patch Input/Output'+[char]0x2026 }
    Invoke-FairlightControl (Find-FairlightControl (Get-FairlightMain) $names 'MenuItem')
}

function Save-FairlightProject {
    Assert-FairlightContext
    if (@(Get-FairlightOwnedWindows).Count -ne 0) { Fail-Fairlight 'DIALOG_STILL_OPEN' }
    Open-FairlightMenuItem 'File' 'Save Project'
    Assert-FairlightContext
}

function Test-FairlightMixerHeader($Target,[string]$Text) {
    if ($Target.scope -ceq 'track') { return $Text -ceq ('A'+$Target.index) }
    if ($Target.scope -ceq 'bus') { return $Text -cmatch '^Bus[1-9][0-9]*$' }
    return $false
}

function Get-FairlightMixerBinding($Target,[string]$Module) {
    $main = Get-FairlightMain
    $candidates = @(Get-FairlightDescendants $main | Where-Object {
        -not $_.Current.IsOffscreen -and $_.Current.ProcessId -eq $script:BoundProcessId -and
        ($_.Current.ClassName -match 'Mixer|QGraphicsView' -or $_.Current.AutomationId -match '(Fairlight|FL).*Mixer') -and
        $_.Current.BoundingRectangle.Width -gt 100 -and
        $_.Current.BoundingRectangle.Height -gt 150
    })
    $bindings = @()
    foreach ($scope in $candidates) {
        $ocr = Get-FairlightOcr $scope
        $names = @(Find-FairlightText $ocr $Target.name)
        $rows = @(Find-FairlightText $ocr $(if ($Module -ceq 'eq') { 'EQ' } else { 'Dynamics' }))
        $inputs = @(Find-FairlightText $ocr 'Input')
        $pans = @(Find-FairlightText $ocr 'Pan')
        if ($names.Count -ne 1 -or $rows.Count -ne 1 -or $inputs.Count -ne 1 -or $pans.Count -ne 1 -or
            $names[0].Y -le $pans[0].Y+$pans[0].Height -or $pans[0].Y -le $rows[0].Y) { continue }
        $headers = @($ocr.Words | Where-Object { $_.Text -cmatch '^(A[1-9][0-9]*|Bus[1-9][0-9]*)$' })
        $columns = @($headers | Where-Object {
            (Test-FairlightMixerHeader $Target $_.Text) -and
            [Math]::Abs(($_.X+$_.Width/2)-($names[0].X+$names[0].Width/2)) -le 36 -and
            $_.Y -lt $inputs[0].Y -and $_.Y -lt $names[0].Y
        })
        if ($columns.Count -ne 1) { continue }
        $column = $columns[0]
        $peers = @($headers | Where-Object { [Math]::Abs($_.Y-$column.Y) -lt [Math]::Max(4,$column.Height) } | Sort-Object { $_.X })
        $centers = @($peers | ForEach-Object { $_.X+$_.Width/2 })
        $center = $column.X+$column.Width/2
        $distances = @($centers | ForEach-Object { [Math]::Abs($_-$center) } | Where-Object { $_ -gt 8 })
        if ($distances.Count -eq 0) { continue }
        $width = ($distances | Measure-Object -Minimum).Minimum
        if ($width -lt 40 -or $width -gt 150 -or $names[0].Width -gt $width*.98) { continue }
        if (-not (Test-FairlightLabelInColumn $ocr $names[0] $center $width $Target.name)) { continue }
        if ($rows[0].X+$rows[0].Width -gt $center-$width*.35) { continue }
        $capture = Get-FairlightCapture $scope
        try {
            $search = [Drawing.Rectangle]::new([int]($center-$width/2-$capture.X),
                [int]($rows[0].Y-$rows[0].Height-$capture.Y),[int]$width,[int]($width*1.35))
            $frame = [FairlightVision]::Graph($capture.Bitmap,$search,[int]$width,($Module -ceq 'eq'))
            if ($frame.IsEmpty) { continue }
            $bindings += @{ Scope=$scope; X=$capture.X+$frame.X+$frame.Width/2;
                Y=$capture.Y+$frame.Y+$frame.Height/2; Area=$scope.Current.BoundingRectangle.Width*$scope.Current.BoundingRectangle.Height }
        } finally { $capture.Bitmap.Dispose() }
    }
    if ($bindings.Count -eq 0) { Fail-Fairlight 'MIXER_TARGET_UNVERIFIED' }
    # Nested UIA graph wrappers may expose identical pixels. Require one point
    # cluster and prefer the smallest scoped container in that cluster.
    $binding = $bindings | Sort-Object { $_.Area } | Select-Object -First 1
    if (@($bindings | Where-Object { [Math]::Abs($_.X-$binding.X) -gt 4 -or [Math]::Abs($_.Y-$binding.Y) -gt 4 }).Count -ne 0) {
        Fail-Fairlight 'MIXER_TARGET_AMBIGUOUS'
    }
    return $binding
}

function Open-FairlightProcessing($Target,[string]$Module) {
    if (@(Get-FairlightOwnedWindows).Count -ne 0) { Fail-Fairlight 'EXISTING_DIALOG' }
    $binding = Get-FairlightMixerBinding $Target $Module
    Invoke-FairlightPoint $binding.Scope ([int]$binding.X) ([int]$binding.Y) -DoubleClick
    $class = if ($Module -ceq 'eq') { 'UiFLAudioEqualizerDialogImp' } else { 'UiDynamicsControlDlgImp' }
    $dialogs = @(Get-FairlightOwnedWindows | Where-Object { $_.Current.ClassName -ceq $class })
    if ($dialogs.Count -ne 1) { Fail-Fairlight 'PROCESSING_DIALOG_UNAVAILABLE' }
    $dialog = $dialogs[0]
    Register-FairlightDialog $dialog
    # Dynamics' UIA name can incorrectly say Patch Input/Output. Class and actual
    # painted module/target header are both required, never that misleading name.
    $ocr = Get-FairlightOcr $dialog
    $header = $(if ($Module -ceq 'eq') { 'Equalizer - ' } else { 'Dynamics - ' })+$Target.name
    $headers = @(Find-FairlightExactLine $ocr $header)
    $box = $dialog.Current.BoundingRectangle
    if ($headers.Count -ne 1 -or $headers[0].Y -gt $box.Y+$box.Height*.10) { Fail-Fairlight 'PROCESSING_TARGET_MISMATCH' }
    return $dialog
}

function Read-FairlightProcessingDialog($Dialog,[string]$Module) {
    if ($Module -ceq 'eq') {
        $checkbox = Find-FairlightControl $Dialog @('Equalizer') 'CheckBox' 'UiFLCheckBox'
        $toggle = $null
        if (-not $checkbox.TryGetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern,[ref]$toggle) -or
            $toggle.Current.ToggleState -eq [System.Windows.Automation.ToggleState]::Indeterminate) { Fail-Fairlight 'PROCESSING_STATE_UNAVAILABLE' }
        return @{ Enabled=($toggle.Current.ToggleState -eq [System.Windows.Automation.ToggleState]::On); Control=$checkbox }
    }
    $ocr = Get-FairlightOcr $Dialog
    $labels = @(Find-FairlightText $ocr 'Dynamics' | Where-Object {
        $_.Y -gt $Dialog.Current.BoundingRectangle.Y+$Dialog.Current.BoundingRectangle.Height*.06 -and
        $_.Y -lt $Dialog.Current.BoundingRectangle.Y+$Dialog.Current.BoundingRectangle.Height*.18
    })
    if ($labels.Count -ne 1) { Fail-Fairlight 'DYNAMICS_SWITCH_UNAVAILABLE' }
    $label = $labels[0]
    # Resolve's fixed profile puts the painted master pill immediately left of
    # this label. The scale comes from the OCR font, then BOTH states are proven.
    $height = $label.Height*1.5
    $width = $height*1.8
    $capture = Get-FairlightCapture $Dialog
    try {
        $box = [Drawing.Rectangle]::new([int]($label.X-$width-$label.Height*.8-$capture.X),
            [int]($label.Y-$label.Height*.2-$capture.Y),[int]$width,[int]$height)
        $state = [FairlightVision]::ToggleState($capture.Bitmap,$box)
        if ($state -lt 0) { Fail-Fairlight 'DYNAMICS_STATE_UNVERIFIED' }
        return @{ Enabled=($state -eq 1); X=$capture.X+$box.X+$box.Width/2; Y=$capture.Y+$box.Y+$box.Height/2 }
    } finally { $capture.Bitmap.Dispose() }
}

function Read-FairlightProcessing($Target,[string]$Module) {
    $dialog = $null
    try { $dialog = Open-FairlightProcessing $Target $Module; $null = Read-FairlightProcessingDialog $dialog $Module }
    finally { if ($null -ne $dialog) { Close-FairlightDialog $dialog } }
    $dialog = $null
    try {
        $dialog = Open-FairlightProcessing $Target $Module
        $value = Read-FairlightProcessingDialog $dialog $Module
        return @{ module=$Module; enabled=[bool]$value.Enabled;
            verification=$(if ($Module -ceq 'eq') { 'windows_uia_reopened' } else { 'windows_visual_reopened' }) }
    } finally { if ($null -ne $dialog) { Close-FairlightDialog $dialog } }
}

function Set-FairlightProcessing($Target,[string]$Module,[bool]$Enabled) {
    $before = Read-FairlightProcessing $Target $Module
    if ($before.enabled -eq $Enabled) { return @{ processing=$before; changed=$false } }
    $dialog = $null
    try {
        $dialog = Open-FairlightProcessing $Target $Module
        $value = Read-FairlightProcessingDialog $dialog $Module
        if ($value.Enabled -ne $before.enabled) { Fail-Fairlight 'PROCESSING_STATE_CHANGED' }
        if ($Module -ceq 'eq') { Invoke-FairlightControl $value.Control -Mutation }
        else { Invoke-FairlightPoint $dialog ([int]$value.X) ([int]$value.Y) -Mutation }
    } finally { if ($null -ne $dialog) { Close-FairlightDialog $dialog } }
    $after = Read-FairlightProcessing $Target $Module
    if ($after.enabled -ne $Enabled) { Fail-Fairlight 'PROCESSING_READBACK_MISMATCH' }
    return @{ processing=$after; changed=$true }
}

function Get-FairlightArmBinding($Target) {
    if ($Target.scope -cne 'track') { Fail-Fairlight 'TRACK_REQUIRED' }
    $scopes = @(Get-FairlightDescendants (Get-FairlightMain) | Where-Object {
        $_.Current.AutomationId.EndsWith('FL TrackControls') -and
        $_.Current.ProcessId -eq $script:BoundProcessId -and -not $_.Current.IsOffscreen
    })
    $found = @()
    foreach ($scope in $scopes) {
        $ocr = Get-FairlightOcr $scope
        $indices = @(Find-FairlightText $ocr ('A'+$Target.index))
        $names = @(Find-FairlightText $ocr $Target.name)
        if ($indices.Count -ne 1 -or $names.Count -ne 1) { continue }
        $index=$indices[0]; $name=$names[0]
        if ([Math]::Abs($index.Y-$name.Y) -gt [Math]::Max($index.Height,$name.Height) -or
            $index.X+$index.Width -ge $name.X) { continue }
        $nameWords = @($ocr.Words | Where-Object {
            $_.X -gt $index.X+$index.Width -and
            [Math]::Abs(($_.Y+$_.Height/2)-($name.Y+$name.Height/2)) -lt [Math]::Max(3,$name.Height*.65)
        } | Sort-Object { $_.X })
        if ((@($nameWords | ForEach-Object { $_.Text }) -join ' ') -cne $Target.name) { continue }
        $next = @($ocr.Words | Where-Object { $_.Text -cmatch '^[AB][1-9][0-9]*$' -and $_.Y -gt $index.Y+$index.Height*2 } | Sort-Object { $_.Y } | Select-Object -First 1)
        $endY = if ($next.Count -eq 1) { $next[0].Y } else { $index.Y+$index.Height*6.5 }
        $letters = @($ocr.Words | Where-Object { $_.Text -cin @('R','S','M') -and
            $_.Y -gt $index.Y+$index.Height -and $_.Y+$_.Height -lt $endY -and $_.X -gt $name.X })
        $rs=@($letters | Where-Object { $_.Text -ceq 'R' }); $ss=@($letters | Where-Object { $_.Text -ceq 'S' }); $ms=@($letters | Where-Object { $_.Text -ceq 'M' })
        if ($rs.Count -ne 1 -or $ss.Count -ne 1 -or $ms.Count -ne 1) { continue }
        $r=$rs[0]; $s=$ss[0]; $m=$ms[0]
        $gap=$s.X-$r.X
        if ($gap -lt $r.Height*1.4 -or $gap -gt $r.Height*4 -or
            [Math]::Abs(($m.X-$s.X)-$gap) -gt $gap*.25 -or
            [Math]::Abs($r.Y-$s.Y) -gt 3 -or [Math]::Abs($s.Y-$m.Y) -gt 3) { continue }
        $size=[int][Math]::Round([Math]::Min($gap*.68,$r.Height*2))
        $capture=Get-FairlightCapture $scope
        try {
            $box=[Drawing.Rectangle]::new([int]($r.X+$r.Width/2-$size/2-$capture.X),
                [int]($r.Y+$r.Height/2-$size/2-$capture.Y),$size,$size)
            $state=[FairlightVision]::ArmState($capture.Bitmap,$box)
            if ($state -lt 0) { continue }
            $found += @{ Scope=$scope; X=$capture.X+$box.X+$box.Width/2;
                Y=$capture.Y+$box.Y+$box.Height/2; Armed=($state -eq 1) }
        } finally { $capture.Bitmap.Dispose() }
    }
    if ($found.Count -ne 1) { Fail-Fairlight 'TRACK_ARM_UNVERIFIED_OR_AMBIGUOUS' }
    return $found[0]
}

function Read-FairlightArm($Target) {
    Assert-FairlightContext
    $first=Get-FairlightArmBinding $Target
    $second=Get-FairlightArmBinding $Target
    if ($first.Armed -ne $second.Armed) { Fail-Fairlight 'TRACK_ARM_STATE_CHANGED' }
    return @{ armed=[bool]$second.Armed; verification='windows_visual_reacquired' }
}

function Set-FairlightArm($Target,[bool]$Armed) {
    $before=Read-FairlightArm $Target
    if ($before.armed -eq $Armed) { return @{ record_arm=$before; changed=$false } }
    $binding=Get-FairlightArmBinding $Target
    if ($binding.Armed -ne $before.armed) { Fail-Fairlight 'TRACK_ARM_STATE_CHANGED' }
    Invoke-FairlightPoint $binding.Scope ([int]$binding.X) ([int]$binding.Y) -Mutation
    $after=Read-FairlightArm $Target
    if ($after.armed -ne $Armed) { Fail-Fairlight 'TRACK_ARM_READBACK_MISMATCH' }
    return @{ record_arm=$after; changed=$true }
}
