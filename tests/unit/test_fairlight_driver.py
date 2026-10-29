"""Offline driver checks: trusted source parsing and synthetic pixels/OCR only.

These tests never run the entrypoint with a valid request, discover Resolve,
capture a screen, touch audio, or invoke a native input function.
"""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "providers/resolve/ResolveFairlightCore.psm1"
ENTRY = ROOT / "providers/resolve/ResolveFairlightUI.ps1"
POWERSHELL = shutil.which("powershell.exe")


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _run_offline(script: str, payload: Any = None) -> Any:
    """Load function definitions, then execute only the explicit synthetic test."""
    if POWERSHELL is None:
        pytest.skip("Windows PowerShell is required for offline driver tests")
    bootstrap = (
        "$ErrorActionPreference='Stop'; Set-StrictMode -Version Latest; "
        "[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false); "
        "[Console]::InputEncoding=New-Object Text.UTF8Encoding($false); "
        f". ([scriptblock]::Create([IO.File]::ReadAllText({_ps_quote(str(CORE))}))); "
    )
    encoded = base64.b64encode((bootstrap + script).encode("utf-16-le")).decode()
    completed = subprocess.run(
        [
            POWERSHELL,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-EncodedCommand",
            encoded,
        ],
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        encoding="utf-8",
        check=False,
        timeout=45,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout.lstrip("\ufeff"))


def _request(operation: str = "inspect_arm", **extra: Any) -> dict[str, Any]:
    return {
        "operation": operation,
        "expected_project_name": "Приклад",
        "target": {
            "timeline_name": "Voice",
            "scope": "track",
            "name": "Microphone",
            "index": 2,
            "audio_track_count": 3,
        },
        **extra,
    }


def _native_source() -> str:
    """Compiling P/Invoke declarations does not invoke their native functions."""
    source = CORE.read_text(encoding="utf-8")
    return source.split("Add-Type -ReferencedAssemblies System.Drawing @'\n", 1)[
        1
    ].split("\n'@", 1)[0]


def _vision_bootstrap() -> str:
    return (
        "Add-Type -AssemblyName System.Drawing; "
        "Add-Type -ReferencedAssemblies System.Drawing -TypeDefinition "
        + _ps_quote(_native_source())
        + "; "
    )


def test_all_driver_sources_parse_without_running_them() -> None:
    result = _run_offline(
        "$paths=ConvertFrom-Json ([Console]::In.ReadToEnd()); $out=@(); "
        "foreach($path in $paths) { $tokens=$null; $errors=$null; "
        "[Management.Automation.Language.Parser]::ParseFile("
        "$path,[ref]$tokens,[ref]$errors)|Out-Null; "
        "$out+=@{file=[IO.Path]::GetFileName($path);"
        "errors=@($errors|ForEach-Object {$_.Message})} }; "
        "ConvertTo-Json -InputObject @($out) -Depth 5 -Compress",
        [str(ENTRY), str(CORE), str(CORE.with_name("ResolveFairlightPatch.psm1"))],
    )
    assert all(item["errors"] == [] for item in result)


def test_request_schema_rejects_extra_selectors_wrong_types_and_duplicates() -> None:
    valid = [
        _request(),
        _request("set_arm", armed=True),
        _request("inspect_processing", module="eq"),
        _request("set_processing", module="dynamics", enabled=False),
        _request("inspect_patch"),
        _request("set_patch", source_name="Input $(literal)", source_channel=64),
    ]
    bus = _request("set_processing", module="eq", enabled=False)
    bus["target"].update(scope="bus", name="Bus 1", index=None, audio_track_count=None)
    valid.append(bus)
    invalid = [
        _request("record"),
        _request(x=10),
        _request(selector="Record"),
        _request(script="anything"),
        _request("set_arm", armed=1),
        _request("set_processing", module="eq", enabled="false"),
        _request("inspect_processing", module="EQ"),
        _request("set_patch", source_name="Input", source_channel=True),
        _request("set_patch", source_name="Input", source_channel=65),
        _request("set_patch", source_name="x" * 129, source_channel=1),
        _request(expected_project_name="bad\nname"),
    ]
    for key, value in [
        ("index", True),
        ("index", 0),
        ("index", 65),
        ("index", 2.0),
        ("audio_track_count", 1),
        ("scope", "Track"),
        ("name", " name"),
        ("coordinates", [10, 20]),
    ]:
        request = _request()
        request["target"][key] = value
        invalid.append(request)
    for operation in ["inspect_arm", "set_arm", "inspect_patch", "set_patch"]:
        request = _request(
            operation, **({"armed": True} if operation == "set_arm" else {})
        )
        request["target"].update(scope="bus", index=None, audio_track_count=None)
        invalid.append(request)
    encoded_valid = [json.dumps(item, ensure_ascii=False) for item in valid]
    encoded_invalid = [json.dumps(item, ensure_ascii=False) for item in invalid]
    raw = json.dumps(_request())
    encoded_invalid += [
        raw.replace('"operation":', '"operation":"inspect_arm","operation":', 1),
        raw.replace('"operation":', '"Operation":"inspect_arm","operation":', 1),
        raw.replace('"operation":', '"oper\\u0061tion":"inspect_arm","operation":', 1),
        raw.replace('"index":', '"index":2,"index":', 1),
        "[]",
        "null",
        "{" + "x" * 8192,
    ]
    result = _run_offline(
        "$all=ConvertFrom-Json ([Console]::In.ReadToEnd()); $results=@(); "
        "foreach($json in $all) { try { $null=ConvertTo-FairlightRequest $json; "
        "$results+=$true } catch { if($_.Exception.Message -notmatch "
        "'FLUI_(INVALID_REQUEST|INVALID_OPERATION|TRACK_REQUIRED)') {throw}; "
        "$results+=$false } }; ConvertTo-Json -InputObject @($results) -Compress",
        encoded_valid + encoded_invalid,
    )
    assert result == [True] * len(encoded_valid) + [False] * len(encoded_invalid)


def test_ocr_matching_uses_complete_words_and_preserves_ambiguity() -> None:
    result = _run_offline(
        "$ocr=@{Lines=@(@{Words=@(@{Text='A2';X=10;Y=10;Width=12;Height=8},"
        "@{Text='Raw';X=30;Y=10;Width=15;Height=8},@{Text='Voice';X=49;Y=10;Width=25;Height=8})},"
        "@{Words=@(@{Text='Raw';X=30;Y=60;Width=15;Height=8},"
        "@{Text='Voice';X=49;Y=60;Width=25;Height=8})})}; "
        "$exact=@(Find-FairlightText $ocr 'Raw Voice'); "
        "$partial=@(Find-FairlightText $ocr 'Voice Extra'); "
        "@{exact=$exact;partial=$partial;index=@(Find-FairlightText $ocr 'A2')}|"
        "ConvertTo-Json -Depth 8 -Compress"
    )
    assert len(result["exact"]) == 2
    assert result["exact"][0]["Width"] == 44
    assert result["partial"] == []
    assert len(result["index"]) == 1


def test_bus_name_collision_cannot_bind_an_audio_track_column() -> None:
    result = _run_offline(
        r"""
$bus=@{scope='bus';name='Bus 1';index=$null}
$track=@{scope='track';name='Bus 1';index=1}
$valid=@((Test-FairlightMixerHeader $bus 'Bus1'),
    (Test-FairlightMixerHeader $bus 'A1'),
    (Test-FairlightMixerHeader $track 'A1'),
    (Test-FairlightMixerHeader $track 'Bus1'),
    (Test-FairlightMixerHeader $bus 'Bus0'),
    (Test-FairlightMixerHeader $bus 'Bus1 extra'))
$script:BoundProcessId=123
function Get-FairlightMain { return 'synthetic main' }
function Get-FairlightDescendants {
    return [pscustomobject]@{Current=[pscustomobject]@{
        ProcessId=123;IsOffscreen=$false;ClassName='QGraphicsView';AutomationId='';
        BoundingRectangle=[pscustomobject]@{Width=300;Height=500}}}
}
function Word([string]$text,[int]$x,[int]$y,[int]$width=20) {
    return @{Text=$text;X=$x;Y=$y;Width=$width;Height=10}
}
function Get-FairlightOcr {
    $words=@((Word 'A1' 120 10),(Word 'Input' 10 40),
        (Word 'Dynamics' 10 100 60),(Word 'Pan' 10 200),
        (Word 'Bus' 110 240),(Word '1' 135 240))
    $lines=@($words[0..3]|ForEach-Object{@{Words=@($_)}})
    $lines+=@{Words=@($words[4..5])}
    return @{Words=$words;Lines=$lines}
}
function Get-FairlightCapture { throw 'must not capture a wrong-scope column' }
$code=''
try { $null=Get-FairlightMixerBinding $bus 'dynamics' }
catch { $code=$_.Exception.Message }
@{valid=$valid;code=$code}|ConvertTo-Json -Depth 5 -Compress
"""
    )
    assert result["valid"] == [True, False, True, False, False, False]
    assert result["code"] == "FLUI_MIXER_TARGET_UNVERIFIED"


def test_target_name_prefixes_do_not_prove_footer_or_processing_title() -> None:
    result = _run_offline(
        r"""
function Word([string]$text,[int]$x,[int]$y,[int]$width=20) {
    return @{Text=$text;X=$x;Y=$y;Width=$width;Height=10}
}
$title=@((Word 'Equalizer' 10 10 50),(Word '-' 65 10 5),
    (Word 'Bus' 75 10),(Word '1' 100 10 6),(Word 'Copy' 110 10))
$footer=@((Word 'Bus' 130 200),(Word '1' 155 200 6),
    (Word 'Copy' 165 200),(Word 'Other' 220 200))
$ocr=@{Words=$footer;Lines=@(@{Words=$title},@{Words=$footer})}
$label=@{X=130;Y=200;Width=31;Height=10}
@{prefixTitle=@(Find-FairlightExactLine $ocr 'Equalizer - Bus 1').Count;
    fullTitle=@(Find-FairlightExactLine $ocr 'Equalizer - Bus 1 Copy').Count;
    prefixFooter=(Test-FairlightLabelInColumn $ocr $label 157 70 'Bus 1');
    fullFooter=(Test-FairlightLabelInColumn $ocr $label 157 70 'Bus 1 Copy')
}|ConvertTo-Json -Compress
"""
    )
    assert result == {
        "prefixTitle": 0,
        "fullTitle": 1,
        "prefixFooter": False,
        "fullFooter": True,
    }


def test_current_timeline_requires_the_fixed_current_selector() -> None:
    result = _run_offline(
        r"""
Add-Type -AssemblyName UIAutomationClient
$script:BoundProcessId=123
function Element([string]$name,[string]$id,[string]$kind,
    [string]$pattern='none',$state=$null) {
    $element=[pscustomobject]@{Current=[pscustomobject]@{
        Name=$name;AutomationId=$id;ProcessId=123;IsOffscreen=$false;
        ControlType=[pscustomobject]@{ProgrammaticName=('ControlType.'+$kind)}};
        TestPattern=$pattern;TestState=$state}
    $element|Add-Member ScriptMethod TryGetCurrentPattern {
        param($requested,[ref]$result)
        if($this.TestPattern -eq 'selected' -and $requested.Id -eq
            [System.Windows.Automation.SelectionItemPattern]::Pattern.Id) {
            $result.Value=[pscustomobject]@{Current=[pscustomobject]@{
                IsSelected=[bool]$this.TestState}};return $true
        }
        if($this.TestPattern -eq 'value' -and $requested.Id -eq
            [System.Windows.Automation.ValuePattern]::Pattern.Id) {
            $result.Value=[pscustomobject]@{Current=[pscustomobject]@{
                Value=$this.TestState}};return $true
        }
        if($this.TestPattern -eq 'selection' -and $requested.Id -eq
            [System.Windows.Automation.SelectionPattern]::Pattern.Id) {
            $current=[pscustomobject]@{Items=@($this.TestState)}
            $current|Add-Member ScriptMethod GetSelection { return $this.Items }
            $result.Value=[pscustomobject]@{Current=$current};return $true
        }
        return $false
    }
    return $element
}
function Get-FairlightDescendants { return $script:Elements }
function Check($elements) {
    $script:Elements=@($elements)
    try { Assert-FairlightTimeline 'synthetic main' 'Voice A';return 'accepted' }
    catch { return $_.Exception.Message }
}
$inactive=Element 'Voice A' 'timeline.tabA' 'TabItem' 'selected' $false
$other=Element 'Voice B' 'timeline.tabB' 'TabItem' 'selected' $true
$selector=Element 'Voice A' 'timeline.m_CbTimelineList' 'Button'
$value=Element 'selector' 'timeline.m_CbTimelineList' 'ComboBox' 'value' 'Voice A'
$wrong=Element 'Voice A' 'timeline.m_CbTimelineList' 'ComboBox' 'value' 'Voice B'
$active=Element 'Voice A' 'timeline.tabA' 'TabItem' 'selected' $true
$otherInactive=Element 'Voice B' 'timeline.tabB' 'TabItem' 'selected' $false
$selectedItem=Element 'Voice A' 'timeline.itemA' 'ListItem'
$selectedOther=Element 'Voice B' 'timeline.itemB' 'ListItem'
$selection=Element 'selector' 'timeline.m_CbTimelineList' 'ComboBox' 'selection' @(
    $selectedItem)
$ambiguous=Element 'selector' 'timeline.m_CbTimelineList' 'ComboBox' 'selection' @(
    $selectedItem,$selectedOther)
$plain=Element 'Voice A' 'other.button' 'Button'
@{inactive=(Check @($inactive,$other));selector=(Check @($inactive,$selector));
    value=(Check @($value));wrong=(Check @($inactive,$wrong));
    selected=(Check @($selection));ambiguous=(Check @($ambiguous));
    activeTab=(Check @($active,$otherInactive));plain=(Check @($plain));
    ambiguousTabs=(Check @($active,$other));
    duplicate=(Check @($selector,$value))}|ConvertTo-Json -Compress
"""
    )
    assert result == {
        "inactive": "FLUI_CURRENT_TIMELINE_UNVERIFIED",
        "selector": "accepted",
        "value": "accepted",
        "wrong": "FLUI_CURRENT_TIMELINE_MISMATCH",
        "selected": "accepted",
        "ambiguous": "FLUI_CURRENT_TIMELINE_MISMATCH",
        "activeTab": "FLUI_CURRENT_TIMELINE_UNVERIFIED",
        "plain": "FLUI_CURRENT_TIMELINE_UNVERIFIED",
        "ambiguousTabs": "FLUI_CURRENT_TIMELINE_UNVERIFIED",
        "duplicate": "FLUI_CURRENT_TIMELINE_AMBIGUOUS",
    }


def test_positive_visual_patterns_reject_unknown_states_and_ambiguous_graphs() -> None:
    script = (
        _vision_bootstrap()
        + r"""
function Image([int]$width,[int]$height) {
    $bitmap=New-Object Drawing.Bitmap $width,$height
    $graphics=[Drawing.Graphics]::FromImage($bitmap)
    $graphics.Clear([Drawing.Color]::FromArgb(40,40,44)); $graphics.Dispose()
    return $bitmap
}
function Toggle([bool]$on) {
    $image=Image 40 24; $g=[Drawing.Graphics]::FromImage($image)
    $g.FillRectangle([Drawing.Brushes]::Black,0,0,40,24)
    $color=if($on){[Drawing.Color]::FromArgb(230,35,30)}else{
        [Drawing.Color]::FromArgb(145,145,145)}
    $brush=New-Object Drawing.SolidBrush $color
    $x=if($on){25}else{7}; $g.FillEllipse($brush,$x,7,9,9)
    $brush.Dispose();$g.Dispose(); return $image
}
$off=Toggle $false; $on=Toggle $true; $unknown=Image 40 24
$toggle=@([FairlightVision]::ToggleState($off,[Drawing.Rectangle]::new(0,0,40,24)),
    [FairlightVision]::ToggleState($on,[Drawing.Rectangle]::new(0,0,40,24)),
    [FairlightVision]::ToggleState($unknown,[Drawing.Rectangle]::new(0,0,40,24)))
function Arm([bool]$on) {
    $image=Image 18 18; $g=[Drawing.Graphics]::FromImage($image)
    $g.Clear([Drawing.Color]::FromArgb(20,20,20))
    $color=if($on){[Drawing.Color]::FromArgb(230,35,30)}else{
        [Drawing.Color]::FromArgb(65,65,65)}
    $brush=New-Object Drawing.SolidBrush $color
    $g.FillRectangle($brush,1,1,16,16);$g.FillRectangle([Drawing.Brushes]::LightGray,7,4,3,9)
    $brush.Dispose();$g.Dispose();return $image
}
$armOff=Arm $false;$armOn=Arm $true;$armUnknown=Image 18 18
$arm=@([FairlightVision]::ArmState($armOff,[Drawing.Rectangle]::new(0,0,18,18)),
    [FairlightVision]::ArmState($armOn,[Drawing.Rectangle]::new(0,0,18,18)),
    [FairlightVision]::ArmState($armUnknown,[Drawing.Rectangle]::new(0,0,18,18)))
function Graph([Drawing.Bitmap]$image,[int]$x) {
    $g=[Drawing.Graphics]::FromImage($image)
    $brush=New-Object Drawing.SolidBrush ([Drawing.Color]::FromArgb(48,48,52))
    $g.FillRectangle($brush,$x,10,52,26);$g.DrawRectangle([Drawing.Pens]::Black,$x,10,52,26)
    $g.DrawLine([Drawing.Pens]::Cyan,$x+2,23,$x+50,23);$brush.Dispose();$g.Dispose()
}
$graph=Image 150 90;Graph $graph 3
$one=[FairlightVision]::Graph($graph,[Drawing.Rectangle]::new(0,0,65,70),60,$true)
Graph $graph 80
$two=[FairlightVision]::Graph($graph,[Drawing.Rectangle]::new(0,0,150,70),60,$true)
$none=[FairlightVision]::Graph($unknown,[Drawing.Rectangle]::new(0,0,40,24),40,$true)
$page=Image 100 40;$g=[Drawing.Graphics]::FromImage($page)
$pen=New-Object Drawing.Pen ([Drawing.Color]::FromArgb(230,80,15))
$g.DrawLine($pen,10,30,80,30);$pen.Dispose();$g.Dispose()
$pageProof=[FairlightVision]::PageUnderline($page,[Drawing.Rectangle]::new(15,10,50,10))
@{toggle=$toggle;arm=$arm;oneEmpty=$one.IsEmpty;twoEmpty=$two.IsEmpty;
    noneEmpty=$none.IsEmpty;page=$pageProof}|ConvertTo-Json -Compress
foreach($bitmap in @($off,$on,$unknown,$armOff,$armOn,$armUnknown,$graph,$page)){
    $bitmap.Dispose()}
"""
    )
    result = _run_offline(script)
    assert result["toggle"] == [0, 1, -1]
    assert result["arm"] == [0, 1, -1]
    assert result["oneEmpty"] is False
    assert result["twoEmpty"] is True
    assert result["noneEmpty"] is True
    assert result["page"] is True


def test_winrt_ocr_uses_only_an_in_memory_synthetic_bitmap() -> None:
    result = _run_offline(
        r"""
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null=[Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]
$null=[Windows.Globalization.Language,Windows.Foundation,ContentType=WindowsRuntime]
$language=[Windows.Globalization.Language]::new('en-US')
$engine=[Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($language)
$reflection=@([System.WindowsRuntimeSystemExtensions].GetMethods()|Where-Object{
    $_.Name -eq 'AsTask' -and $_.IsGenericMethodDefinition -and
    $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'}).Count
if($null -eq $engine) {
    @{available=$false;reflection=$reflection}|ConvertTo-Json -Compress
} else {
    function Get-FairlightCapture {
        $bitmap=New-Object Drawing.Bitmap 800,160
        $g=[Drawing.Graphics]::FromImage($bitmap)
        $g.Clear([Drawing.Color]::White)
        $font=New-Object Drawing.Font 'Segoe UI',32
        $g.DrawString('Synthetic Voice',$font,[Drawing.Brushes]::Black,20,20)
        $font.Dispose();$g.Dispose()
        return @{Bitmap=$bitmap;X=0;Y=0;Scale=2}
    }
    $ocr=Get-FairlightOcr 'synthetic bitmap, never a UI surface'
    @{available=$true;reflection=$reflection;text=@($ocr.Lines|ForEach-Object{
        $_.Text}) -join ' '}|ConvertTo-Json -Compress
}
"""
    )
    assert result["reflection"] == 1
    if not result["available"]:
        pytest.skip("The optional Windows English OCR language pack is unavailable")
    assert "Synthetic Voice" in result["text"]


def test_setters_are_idempotent_and_failed_readback_keeps_mutation_uncertain() -> None:
    result = _run_offline(
        r"""
$script:FairlightMutationTouched=$false
function Read-FairlightProcessing {
    return @{module='eq';enabled=$false;verification='windows_uia_reopened'} }
function Read-FairlightArm {
    return @{armed=$false;verification='windows_visual_reacquired'} }
function Open-FairlightProcessing { throw 'must not open a dialog for a no-op' }
function Get-FairlightArmBinding { throw 'must not click for a no-op' }
$processing=Set-FairlightProcessing @{} 'eq' $false
$arm=Set-FairlightArm @{} $false
$noMutation=$script:FairlightMutationTouched
function Open-FairlightProcessing { return 'synthetic owned dialog' }
function Read-FairlightProcessingDialog {
    return @{Enabled=$false;Control='synthetic checkbox'} }
function Close-FairlightDialog { }
function Invoke-FairlightControl { Mark-FairlightMutation }
$code=''
try { $null=Set-FairlightProcessing @{} 'eq' $true }
catch { $code=$_.Exception.Message }
@{processingChanged=$processing.changed;armChanged=$arm.changed;noMutation=$noMutation;
    touched=$script:FairlightMutationTouched;code=$code}|ConvertTo-Json -Compress
"""
    )
    assert result == {
        "processingChanged": False,
        "armChanged": False,
        "noMutation": False,
        "touched": True,
        "code": "FLUI_PROCESSING_READBACK_MISMATCH",
    }


def test_transport_controls_are_rejected_before_any_native_call() -> None:
    result = _run_offline(
        r"""
$script:BoundProcessId=123
function Assert-FairlightContext { }
function Assert-FairlightDpi { }
$codes=@()
foreach($name in @('Record','Play','Stop','FastForward')) {
    $scope=[pscustomobject]@{Current=[pscustomobject]@{ProcessId=123;
        IsOffscreen=$false;IsEnabled=$true;Name=$name;
        AutomationId=('x.btnTransport'+$name)}}
    try { Invoke-FairlightHit $scope 1 1 -Mutation }
    catch { $codes+=$_.Exception.Message }
}
@{codes=$codes}|ConvertTo-Json -Compress
"""
    )
    assert result["codes"] == ["FLUI_FORBIDDEN_OR_DISABLED_TARGET"] * 4


def test_write_guard_order_and_fixed_loader_cannot_be_selected_by_request() -> None:
    core = CORE.read_text(encoding="utf-8")
    entry = ENTRY.read_text(encoding="utf-8")
    hit = core.split("function Invoke-FairlightHit", 1)[1].split(
        "function Invoke-FairlightControl", 1
    )[0]
    assert hit.index("Assert-FairlightContext") < hit.index(
        "[FairlightNative]::SetCursorPos"
    )
    assert hit.index("::FromPoint") < hit.index("Mark-FairlightMutation")
    assert hit.index("Assert-FairlightStopped", hit.index("::FromPoint")) < hit.index(
        "[FairlightNative]::Click"
    )
    assert "Start-Sleep -Milliseconds 120" in hit
    assert "Thread.Sleep(80)" in core
    assert "'ResolveFairlightCore.psm1'" in entry
    assert "'ResolveFairlightPatch.psm1'" in entry
    assert "Join-Path $PSScriptRoot $request" not in entry
    assert "Toggle()" not in core
    assert "SendKeys" not in core
    assert "keybd_event" not in core
    assert "windows_visual_reopened" in core
    assert "windows_uia_reopened" in core
    assert "status = 'live_pending'" in entry
    assert "SetThreadDpiAwarenessContext([IntPtr]::new(-4))" in core
    assert "AreDpiAwarenessContextsEqual(" in core
    assert "Restore-FairlightDpi" in entry
    assert hit.index("Assert-FairlightDpi") < hit.index("::SetCursorPos")
