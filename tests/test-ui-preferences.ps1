# Isolated preference merge regression. No Claude data or UI is opened.
$ErrorActionPreference='Stop'
$sourceRoot=Split-Path $PSScriptRoot -Parent
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $sourceRoot 'Studio.ps1'),[ref]$tokens,[ref]$errors)
if($errors){throw 'Studio source has syntax errors'}
. (Join-Path $sourceRoot 'studio-core.ps1')
$definition=$ast.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Save-Preferences'},$true) | Select-Object -First 1
Invoke-Expression $definition.Extent.Text
$fixture=Join-Path $PSScriptRoot ('.tmp\ui-preferences-'+[guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($fixture)
$path=Join-Path $fixture 'preferences.json'
try{
    $script:Data=$fixture;$ReadOnly=$false;$SmokeTest=$false
    Write-Json $path @{Dark=$false;FontSize=15;OnboardingVersion=3;OnboardingSkipped=$true;FutureSetting='preserved'}
    $script:Dark=$true;$script:FontSize=19;$script:OnboardingVersion=0;$script:OnboardingTouched=$false
    Save-Preferences
    $saved=Read-Json $path
    if(-not $saved.Dark -or $saved.FontSize -ne 19 -or $saved.OnboardingVersion -ne 3 -or $saved.FutureSetting -ne 'preserved' -or -not $saved.OnboardingSkipped){throw 'Appearance save discarded onboarding or unknown settings'}
    $script:OnboardingVersion=1;$script:OnboardingTouched=$true;$script:OnboardingSkipped=$false
    Save-Preferences
    $saved=Read-Json $path
    if($saved.OnboardingVersion -ne 3 -or $saved.OnboardingSkipped){throw 'Onboarding save reset a newer guide version or skip status'}
    $before=[IO.File]::ReadAllText($path)
    $ReadOnly=$true;$script:Dark=$false;Save-Preferences
    if([IO.File]::ReadAllText($path) -cne $before){throw 'ReadOnly mode persisted preferences'}
    $ReadOnly=$false;$SmokeTest=$true;Save-Preferences
    if([IO.File]::ReadAllText($path) -cne $before){throw 'SmokeTest mode persisted preferences'}
    'Preference merge, guide version preservation, ReadOnly and SmokeTest guards passed.'
}finally{
    # Only remove the one known fixture file and then its empty directory.
    if(Test-Path -LiteralPath $path){Remove-Item -LiteralPath $path}
    [IO.Directory]::Delete($fixture)
}
