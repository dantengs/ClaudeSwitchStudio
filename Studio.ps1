param([switch]$SmokeTest,[string]$SnapshotDir,[switch]$ReadOnly)
$ErrorActionPreference='Stop'
if($PSVersionTable.PSEdition -ne 'Desktop'){throw '请通过桌面快捷方式或 Launch.vbs 启动此工具。界面使用 Windows 自带的 PowerShell。'}
Add-Type -AssemblyName PresentationFramework,PresentationCore,WindowsBase
. (Join-Path $PSScriptRoot 'studio-core.ps1')
Initialize-Studio (Split-Path $PSScriptRoot -Parent) ''
if(-not(Test-Path -LiteralPath (Join-Path $script:Kernel 'claude-profile.ps1'))){$script:Kernel='D:\programfile\Claude Desktop Account Switcher'}
$script:State=$null; $script:Busy=$false; $script:Job=$null; $script:Dark=$true; $script:FontSize=15; $script:Page='accounts'
$script:OnboardingVersion=0;$script:OnboardingStep=0;$script:OnboardingSkipped=$false;$script:OnboardingTouched=$false;$script:OnboardingOpened=$false
$reader=New-Object Xml.XmlNodeReader ([xml][IO.File]::ReadAllText((Join-Path $PSScriptRoot 'Studio.xaml')))
$script:Window=[Windows.Markup.XamlReader]::Load($reader)
$script:UI=@{}
foreach($node in ([xml][IO.File]::ReadAllText((Join-Path $PSScriptRoot 'Studio.xaml'))).SelectNodes('//*[@*[local-name()="Name"]]')) {
    $name=$node.GetAttribute('Name','http://schemas.microsoft.com/winfx/2006/xaml')
    if($name){$script:UI[$name]=$script:Window.FindName($name)}
}
function Read-AppImage([string]$Path){
    if(-not(Test-Path -LiteralPath $Path)){return $null}
    $frame=[Windows.Media.Imaging.BitmapFrame]::Create([Uri]$Path,[Windows.Media.Imaging.BitmapCreateOptions]::PreservePixelFormat,[Windows.Media.Imaging.BitmapCacheOption]::OnLoad)
    $frame.Freeze();return $frame
}
$appIcon=Read-AppImage (Join-Path $PSScriptRoot 'assets\app.ico');if($appIcon){$script:Window.Icon=$appIcon}
$appMark=Read-AppImage (Join-Path $PSScriptRoot 'assets\app.png');if($appMark){$script:UI.BrandImage.Source=$appMark}
$chrome=New-Object Windows.Shell.WindowChrome
$chrome.CaptionHeight=0; $chrome.ResizeBorderThickness=New-Object Windows.Thickness(6);$chrome.GlassFrameThickness=New-Object Windows.Thickness(0)
[Windows.Shell.WindowChrome]::SetWindowChrome($script:Window,$chrome)

function Notice([string]$Text) {$script:UI.NoticeText.Text=$Text; $script:UI.Notice.Visibility='Visible'}
function Navigate([string]$Page) {
    $script:Page=$Page
    $titles=@{accounts=@('账号与提供商','选择工作身份，共用本地对话与项目记忆。');sessions=@('共享对话池','一条原文只展示一次，账号与提供商作为独立身份。');usage=@('缓存与用量','查看历史用量，区分本地共享与服务端缓存。');backups=@('备份中心','每次变更前，保留带时间戳的可核验副本。');settings=@('设置与本地记忆','更舒适的阅读，清晰可见的本地数据。')}
    foreach($key in @('accounts','sessions','usage','backups','settings')) {
        $control=$key.Substring(0,1).ToUpper()+$key.Substring(1)+'Page'
        $script:UI[$control].Visibility=$(if($Page -eq $key){'Visible'}else{'Collapsed'})
        $nav='Nav'+$key.Substring(0,1).ToUpper()+$key.Substring(1)
        $script:UI[$nav].Background=$(if($Page -eq $key){$script:Window.Resources['Tint']}else{[Windows.Media.Brushes]::Transparent})
        $script:UI[$nav].Foreground=$(if($Page -eq $key){$script:Window.Resources['Accent']}else{$script:Window.Resources['Muted']})
    }
    $script:UI.PageTitle.Text=$titles[$Page][0];$script:UI.PageSubtitle.Text=$titles[$Page][1]
    $script:UI.SaveBtn.Visibility=$(if($Page -eq 'accounts'){'Visible'}else{'Collapsed'})
}
function Set-Theme([bool]$Dark,[bool]$Persist=$true) {
    $script:Dark=$Dark
    $colors=$(if($Dark){@{PageBg='#15191F';Surface='#1F252D';SidebarBg='#13171C';Ink='#EFF3F8';Muted='#A6AFBE';Line='#333C48';Accent='#60A5FA';Tint='#24374D';Green='#63D5AA'}}else{@{PageBg='#F6F8FB';Surface='#FFFFFF';SidebarBg='#EDF2F8';Ink='#202B3A';Muted='#63738A';Line='#DCE3EC';Accent='#2873E3';Tint='#E4EEFD';Green='#20865C'}})
    foreach($key in $colors.Keys){
        $brush=New-Object Windows.Media.SolidColorBrush ([Windows.Media.ColorConverter]::ConvertFromString($colors[$key]))
        $script:Window.Resources[$key]=$brush.PSObject.BaseObject
    }
    Navigate $script:Page
    if($Persist){Save-Preferences}
}
function Save-Preferences {
    if($ReadOnly -or $SmokeTest){return}
    $path=Join-Path $script:Data 'preferences.json';$existing=Read-Json $path;$updated=@{}
    if($existing){foreach($property in $existing.PSObject.Properties){$updated[$property.Name]=$property.Value}}
    $updated.Dark=$script:Dark;$updated.FontSize=$script:FontSize
    $updated.OnboardingVersion=[Math]::Max([int](Prop $existing 'OnboardingVersion' 0),$script:OnboardingVersion)
    if($script:OnboardingTouched){$updated.OnboardingSkipped=$script:OnboardingSkipped}
    Write-Json $path $updated
}
function Show-Onboarding([int]$Step=0){
    $script:OnboardingOpened=$true;$script:OnboardingStep=[Math]::Max(0,[Math]::Min(2,$Step))
    $steps=@(
        @{Title='先查看，再决定';Body="你可以直接查看账号、共享对话池和历史用量。浏览这些页面不需要退出 Claude，也不会替你登录或切换。`n`n卡片里的历史账号，只表示本地有这个账号的记录，不代表已保存可切换的登录。";Hint='先认识页面：账号管理 → 共享对话池 → 缓存与用量。'},
        @{Title='登录状态，单独收录';Body="官方账号需要你先在 Claude Desktop 自行登录，再用「收录当前登录」保存。仅登录 Claude Code 不能代替桌面版登录。`n`n收录和切换前，工具会提醒你退出 Claude；执行前创建带时间戳的备份，工具不会强制关闭你的工作。";Hint='桌面登录快照是实验功能，未经官方支持，不能保证不会触发账号限制。'},
        @{Title='共享原文，理解缓存';Body="共享对话池合并展示本机的对话原文，同一条原文只显示一次；账号登录与第三方端点分别管理。`n`n本地原文和项目记忆的共享，不会继承其他账号在服务器上的提示词缓存，也不能保证继续对话时的费用不变。";Hint='完成后可在“设置与关于”随时重新查看本指引。指引不会执行登录、迁移或认证操作。'}
    )
    $stepInfo=$steps[$script:OnboardingStep]
    $script:UI.GuideStep.Text='第 '+($script:OnboardingStep+1)+' 步 / 3';$script:UI.GuideTitle.Text=$stepInfo.Title;$script:UI.GuideBody.Text=$stepInfo.Body;$script:UI.GuideHint.Text=$stepInfo.Hint
    $script:UI.GuideBackBtn.Visibility=$(if($script:OnboardingStep -eq 0){'Collapsed'}else{'Visible'})
    $script:UI.GuideNextBtn.Content=$(if($script:OnboardingStep -eq 2){'开始使用'}else{'下一步'})
    $script:UI.Onboarding.Visibility='Visible'
}
function Complete-Onboarding([bool]$Skipped=$false){
    $script:OnboardingVersion=1;$script:OnboardingSkipped=$Skipped;$script:OnboardingTouched=$true
    $script:UI.Onboarding.Visibility='Collapsed';Save-Preferences
}
function Set-ReadingSize([double]$Size,[bool]$Persist=$true){
    if($Size -notin @(15,17,19)){$Size=15};$script:FontSize=$Size
    foreach($pair in @(@{Key='BodySize';Size=$Size},@{Key='SubSize';Size=($Size-1.5)},@{Key='CardTitleSize';Size=($Size+2)})){
        $number=[double]$pair.Size;$script:Window.Resources[$pair.Key]=$number.PSObject.BaseObject
    }
    for($i=0;$i -lt $script:UI.FontSizeChoice.Items.Count;$i++){if([double]$script:UI.FontSizeChoice.Items[$i].Tag -eq $Size -and $script:UI.FontSizeChoice.SelectedIndex -ne $i){$script:UI.FontSizeChoice.SelectedIndex=$i;break}}
    if($Persist){Save-Preferences}
}
function Filter-Sessions {
    if(-not $script:State){return}
    $key='all';if($script:UI.SessionAccount.SelectedItem){$key=[string]$script:UI.SessionAccount.SelectedItem.AccountKey}
    $query=$script:UI.SearchBox.Text.Trim()
    $script:UI.SearchHint.Visibility=$(if($query){'Collapsed'}else{'Visible'})
    $rows=@($script:State.Pool | Where-Object {(($key -eq 'all' -and $_.IsRegistered) -or ($key -eq 'unregistered' -and -not $_.IsRegistered) -or @($_.OriginKeys) -contains $key) -and (-not $query -or ($_.Title+' '+$_.Cwd+' '+$_.Id+' '+$_.Origins).IndexOf($query,[StringComparison]::OrdinalIgnoreCase) -ge 0)})
    $script:UI.SessionGrid.ItemsSource=$rows
    $script:UI.SelectionLabel.Text='共 '+$rows.Count+' 条独立原文 · 已合并重复账号索引 · 分支单独保留'
}
function Select-Account([string]$Id){
    for($i=0;$i -lt $script:UI.SessionAccount.Items.Count;$i++){if([string]$script:UI.SessionAccount.Items[$i].Uuid -eq $Id){$script:UI.SessionAccount.SelectedIndex=$i;return}}
    if($script:UI.SessionAccount.Items.Count){$script:UI.SessionAccount.SelectedIndex=0}
}
function Update-State($State) {
    $script:State=$State
    $script:UI.AccountCards.ItemsSource=@($State.Accounts | Where-Object {$_.RootKey -eq 'official'})
    $script:UI.ProviderCards.ItemsSource=@($State.Providers.Providers)
    $script:UI.ProviderWarning.Text=[string]$State.Providers.Warning
    $script:UI.MemoryCards.ItemsSource=@($State.Memory)
    $script:UI.AccountCount.Text=[string]@($State.Accounts).Count
    $script:UI.SavedCount.Text=[string]@($State.Accounts | Where-Object {$_.Saved}).Count
    $script:UI.TranscriptCount.Text=[string]$State.RegisteredPoolCount
    $sel='*';if($script:UI.SessionAccount.SelectedItem){$sel=[string]$script:UI.SessionAccount.SelectedItem.Uuid}
    $script:UI.SessionAccount.ItemsSource=@([pscustomobject]@{Uuid='*';AccountKey='all';Name='全部已注册对话'})+@($State.Accounts)+@([pscustomobject]@{Uuid='?';AccountKey='unregistered';Name='未注册原文（仅供核对）'})
    if($sel -and @($State.Accounts.Uuid) -contains $sel){Select-Account $sel}
    elseif($State.Live){$script:UI.SessionAccount.SelectedIndex=0}
    elseif(@($State.Accounts).Count){$script:UI.SessionAccount.SelectedIndex=0}
    $usageId=''
    if($script:UI.UsageSession.SelectedItem){$usageId=$script:UI.UsageSession.SelectedItem.Id+'|'+$script:UI.UsageSession.SelectedItem.Account}
    foreach($s in $State.Pool){$s | Add-Member NoteProperty UsageLabel $s.Title -Force}
    $script:UI.UsageSession.ItemsSource=@($State.Pool)
    if($usageId){foreach($s in $State.Pool){if(($s.Id+'|'+$s.Account) -eq $usageId){$script:UI.UsageSession.SelectedItem=$s;break}}}
    if(-not $script:UI.UsageSession.SelectedItem -and @($State.Pool).Count){$script:UI.UsageSession.SelectedIndex=0}
    $script:UI.BackupCards.ItemsSource=@($State.Backups)
    $script:UI.BackupEmpty.Visibility=$(if(@($State.Backups).Count){'Collapsed'}else{'Visible'})
    $backend=Prop $State 'Backend' 'PowerShell 兼容适配器'
    $script:UI.KernelPathLabel.Text=$backend+"`n"+$PSScriptRoot
    $script:UI.WatcherLabel.Text=$(if($State.Watcher){'自动切换监控：正在运行（由原版工具管理）'}else{'自动切换监控：未运行'})
    Filter-Sessions
}
function Invoke-Job($Request,[scriptblock]$Callback,[string]$Message='正在处理…',[scriptblock]$OnError=$null) {
    if(($ReadOnly -or $SmokeTest) -and $Request.Action -notin @('state','usage','workspace-plan')){Notice '当前为只读预览。切换、保存和迁移需要你退出 Claude 后再进行。';return}
    if($script:Busy){Notice '当前操作尚未完成，请稍候。';return}
    $script:Busy=$true;$script:UI.BusyBar.Visibility='Visible';$script:UI.LocalLabel.Visibility='Collapsed';$script:UI.StatusText.Text=$Message
    $id=[guid]::NewGuid().ToString('N');$dir=Join-Path $script:Data 'requests';[void][IO.Directory]::CreateDirectory($dir)
    $requestPath=Join-Path $dir ($id+'.request.json');$responsePath=Join-Path $dir ($id+'.response.json')
    Write-Json $requestPath $Request
    $psi=New-Object Diagnostics.ProcessStartInfo
    $rust=Join-Path $PSScriptRoot 'claude-switch-core.exe'
    if(Test-Path -LiteralPath $rust){
        $psi.FileName=$rust
        $psi.Arguments='--request "'+$requestPath+'" --response "'+$responsePath+'"'
    }else{
        $script:Busy=$false;$script:UI.BusyBar.Visibility='Collapsed';$script:UI.LocalLabel.Visibility='Visible'
        Notice '缺少新版 Rust 核心。为避免执行旧版操作，已停止。请重新构建或安装。';return
    }
    $psi.UseShellExecute=$false;$psi.CreateNoWindow=$true
    $p=New-Object Diagnostics.Process;$p.StartInfo=$psi
    try{[void]$p.Start();$script:Job=@{Process=$p;Response=$responsePath;Callback=$Callback;OnError=$OnError;Start=[DateTime]::UtcNow}}
    catch{$script:Busy=$false;$script:UI.BusyBar.Visibility='Collapsed';$script:UI.LocalLabel.Visibility='Visible';Notice $_.Exception.Message}
}
function Refresh-State {Invoke-Job @{Action='state'} {param($value) Update-State $value} '正在读取账号与会话…'}
function Operation-Done($Value) {
    if($Value -is [string]){Notice $Value}
    elseif($Value.PSObject.Properties['Message']){Notice $Value.Message}
    elseif($Value.PSObject.Properties['Verified']){Notice ('备份完成，'+$Value.Files+' 个文件逐一校验通过。')}
    Refresh-State
}
function Show-Modal([string]$Title,[string]$Body,[bool]$Input,[string]$Value,[string]$Ok,[scriptblock]$Callback,[string]$CancelText='取消',[scriptblock]$OnCancel=$null) {
    if($script:Busy){Notice '请等待当前操作完成。';return}
    $script:UI.ModalTitle.Text=$Title;$script:UI.ModalBody.Text=$Body;$script:UI.ModalInput.Text=$Value
    $script:UI.ModalInput.Visibility=$(if($Input){'Visible'}else{'Collapsed'});$script:UI.ModalOk.Content=$Ok
    $script:UI.ModalCancel.Content=$CancelText;$script:ModalCancelCallback=$OnCancel
    $script:ModalCallback=$Callback;$script:UI.Modal.Visibility='Visible'
    if($Input){[void]$script:UI.ModalInput.Focus();$script:UI.ModalInput.SelectAll()}
}
function Show-Save($Account=$null,[string]$Display='', [string]$Problem='') {
    if(-not $Account){$Account=@($script:State.Accounts | Where-Object {$_.SwitchKind -eq 'official' -and $_.IsCurrent}) | Select-Object -First 1}
    if(-not $Account){Show-LoginGuide;return}
    if(-not $Display){$Display=$Account.Name}
    $body="收录对象："+$Account.Name+"`n`n请先从系统托盘完全退出 Claude Desktop，并结束 Claude Code 与第三方版。工具不会关闭你的工作。执行前会按时间备份，并核对账号身份。`n`n下面是显示名称，可直接使用邮箱，不作为文件夹名。仅检查本地登录缓存，不联网确认登录有效性。`n`n桌面登录快照为实验功能，未经官方支持，无法保证账号使用风险。"
    if($Problem){$body="上次未完成："+$Problem+"`n`n"+$body}
    Show-Modal '收录桌面版登录 · 请先退出 Claude' $body $true $Display '我已退出，收录登录' ({
        param($displayName)
        $failure=({param($errorText) Show-Save $Account $displayName $errorText}.GetNewClosure())
        Invoke-Job @{Action='save';ExpectedAccount=$Account.Uuid;DisplayName=$displayName;MaintenanceConfirmed=$true} {param($v) Operation-Done $v} '正在备份并核对桌面版登录…' $failure
    }.GetNewClosure())
}
function Show-LoginGuide($Account=$null,[switch]$SkipCurrentCapture) {
    $unsaved=@($script:State.Accounts | Where-Object {$_.SwitchKind -eq 'official' -and $_.IsCurrent -and -not $_.Saved}) | Select-Object -First 1
    if(-not $SkipCurrentCapture -and $unsaved -and (-not $Account -or $Account.Uuid -ne $unsaved.Uuid)){
        Show-Modal '先保留当前账号的登录' ("当前账号："+$unsaved.Name+"，尚未收录桌面版登录。`n`n先收录后，再回来添加其他账号，便于以后直接切回。若直接更换登录，之后可能需要再次登录当前账号。`n`n收录时仍会提示你先退出 Claude，并创建时间戳备份。") $false '' '先收录当前登录' ({Show-Save $unsaved}.GetNewClosure()) '继续添加，不收录' ({Show-LoginGuide $Account -SkipCurrentCapture}.GetNewClosure())
        return
    }
    $target=$(if($Account){$Account.Name}else{'要添加的官方账号'})
    Show-Modal ('添加桌面版登录 · '+$target) ("1. 请自行在 Claude Desktop 登录："+$target+"。仅登录 Claude Code 不会建立桌面版登录。`n`n2. 登录成功后，从系统托盘完全退出 Claude Desktop，并结束 Claude Code 与第三方版。`n`n3. 点击下方按钮。工具只读检查当前身份，匹配后再提示收录并创建时间戳备份。`n`n账号邮箱是本地识别信息；工具不会代你登录或退出账号。桌面登录快照为实验功能，未经官方支持，无法保证账号使用风险。") $false '' '我已登录并退出，检查身份' ({
        Invoke-Job @{Action='state'} ({param($value)
            Update-State $value
            $current=@($value.Accounts | Where-Object {$_.SwitchKind -eq 'official' -and $_.IsCurrent}) | Select-Object -First 1
            if(-not $current -or ($Account -and $current.Uuid -ne $Account.Uuid)){
                Show-Modal '当前身份尚未匹配' '尚未检测到所选账号的桌面版身份。请确认登录的是 Claude Desktop，并在退出后重试。本次未修改登录文件。' $false '' '返回添加登录' ({Show-LoginGuide $Account}.GetNewClosure());return
            }
            Show-Save $current
        }.GetNewClosure()) '正在只读核对桌面版身份…'
    }.GetNewClosure())
}
function Display-Usage($Value) {
    if(-not $Value.Found){$script:UI.UsageCaption.Text=$Value.Message;foreach($n in @('UsageRead','UsageWrite','UsageInput','UsageOutput')){$script:UI[$n].Text='—'};return}
    $script:UI.UsageRead.Text=('{0:N0}' -f [long]$Value.Read);$script:UI.UsageWrite.Text=('{0:N0}' -f [long]$Value.Write)
    $script:UI.UsageInput.Text=('{0:N0}' -f [long]$Value.Input);$script:UI.UsageOutput.Text=('{0:N0}' -f [long]$Value.Output)
    $script:UI.UsageRatio.Text='占该次输入的 '+$Value.ReadPercent+'% · 历史记录'
    $date=$Value.Time
    try{$date=([datetimeoffset]::Parse($Value.Time)).LocalDateTime.ToString('yyyy-MM-dd HH:mm')}catch{}
    $script:UI.UsageCaption.Text='最近记录：'+$date+'   ·   '+$Value.Model+'   ·   '+$Value.Requests+' 次已记录请求'
}
function Read-SelectedUsage {
    $s=$script:UI.UsageSession.SelectedItem
    if(-not $s){Notice '请先选择一条对话。';return}
    Invoke-Job @{Action='usage';Account=$s.Account;SessionId=$s.Id;PoolKey=$s.PoolKey;RootKey=$s.RootKey} {param($v) Display-Usage $v} '正在读取历史请求用量…'
}

foreach($name in @('NavAccounts','NavSessions','NavUsage','NavBackups','NavSettings')){$script:UI[$name].Add_Click({param($sender,$event) Navigate ([string]$sender.Tag)})}
$script:UI.TitleBar.Add_MouseLeftButtonDown({param($sender,$event) if($event.ClickCount -eq 2){$script:Window.WindowState=$(if($script:Window.WindowState -eq 'Maximized'){'Normal'}else{'Maximized'})}else{$script:Window.DragMove()}})
$script:UI.MinBtn.Add_Click({$script:Window.WindowState='Minimized'})
$script:UI.MaxBtn.Add_Click({$script:Window.WindowState=$(if($script:Window.WindowState -eq 'Maximized'){'Normal'}else{'Maximized'})})
$script:UI.CloseBtn.Add_Click({if($script:Busy){Notice '操作仍在进行，请完成后再关闭。'}else{$script:Window.Close()}})
$script:Window.Add_Closing({param($sender,$event) if($script:Busy){$event.Cancel=$true;Notice '正在备份或处理数据，请等待操作完成。'}})
$script:UI.ThemeBtn.Add_Click({Set-Theme (-not $script:Dark)})
$script:UI.SettingsThemeBtn.Add_Click({Set-Theme (-not $script:Dark)})
$script:UI.RefreshBtn.Add_Click({Refresh-State})
$script:UI.SaveBtn.Add_Click({Invoke-Job @{Action='state'} {param($v) Update-State $v;Show-Save} '正在只读检查当前桌面版身份…'})
$script:UI.SetupBtn.Add_Click({Show-LoginGuide})
$script:UI.OnboardingBtn.Add_Click({Show-Onboarding})
$script:UI.GuideSkipBtn.Add_Click({Complete-Onboarding $true})
$script:UI.GuideBackBtn.Add_Click({Show-Onboarding ($script:OnboardingStep-1)})
$script:UI.GuideNextBtn.Add_Click({if($script:OnboardingStep -eq 2){Complete-Onboarding}else{Show-Onboarding ($script:OnboardingStep+1)}})
$script:UI.ModalCancel.Add_Click({$cancel=$script:ModalCancelCallback;$script:ModalCancelCallback=$null;$script:UI.Modal.Visibility='Collapsed';if($cancel){try{& $cancel}catch{Notice $_.Exception.Message}}})
$script:UI.ModalOk.Add_Click({$value=$script:UI.ModalInput.Text.Trim();$cb=$script:ModalCallback;$script:UI.Modal.Visibility='Collapsed';try{& $cb $value}catch{Notice $_.Exception.Message}})
$script:UI.ProviderCards.AddHandler([Windows.Controls.Button]::ClickEvent,[Windows.RoutedEventHandler]{param($sender,$event)
    $button=$event.OriginalSource;if($button -isnot [Windows.Controls.Button] -or $button.Tag -ne 'provider-switch'){return}
    $provider=$button.CommandParameter;$id=$provider.Id
    Show-Modal ('使用 '+$provider.Name) '请先退出 Claude Desktop、第三方版及 Claude Code。工具会先按时间备份现有配置，再切换本地提供商；对话和项目记忆不会复制。第三方推理会通过所选端点。' $false '' '我已退出，切换提供商' ({Invoke-Job @{Action='provider-switch';ProviderId=$id;MaintenanceConfirmed=$true} {param($v)Operation-Done $v} '正在备份并配置本地提供商…'}.GetNewClosure())
})
$script:UI.FontSizeChoice.Add_SelectionChanged({if($script:UI.FontSizeChoice.SelectedItem){Set-ReadingSize ([double]$script:UI.FontSizeChoice.SelectedItem.Tag) (-not $SmokeTest)}})
$script:UI.AccountCards.AddHandler([Windows.Controls.Button]::ClickEvent,[Windows.RoutedEventHandler]{param($sender,$event)
    $b=$event.OriginalSource;if($b -isnot [Windows.Controls.Button]){return};$a=$b.CommandParameter
    switch([string]$b.Tag){
        'view' {Select-Account ([string]$a.Uuid);Navigate 'sessions';Filter-Sessions}
        'label' {$id=$a.Uuid;Show-Modal '设置显示名称' '为这个本地账号起一个易识别的名称，不会修改账号本身。' $true $a.Name '保存名称' ({param($name) Invoke-Job @{Action='label';Account=$id;Label=$name} {param($v)Operation-Done $v} '正在更新显示名称…'}.GetNewClosure())}
        'account-action' {
            switch([string]$a.PrimaryAction){
                'capture' {Show-Save $a}
                'login-guide' {Show-LoginGuide $a}
                'providers' {Notice '请在下方“第三方提供商”区域选择端点，使用前会提示退出 Claude。'}
                'switch' {$profile=$a.Profile;$expected=$a.Uuid;Show-Modal ('切换到 '+$a.Name) '请先自行退出 Claude Desktop、第三方版及 Claude Code。工具会按时间备份，并收录当前账号的最新登录缓存，再恢复目标账号。登录是否有效请自行打开 Claude 确认；原文与记忆留在原处。桌面登录快照为实验功能，未经官方支持，无法保证账号使用风险。' $false '' '我已退出，切换' ({Invoke-Job @{Action='switch';Name=$profile;ExpectedAccount=$expected;MaintenanceConfirmed=$true} {param($v)Operation-Done $v} '正在备份当前登录并切换…'}.GetNewClosure())}
            }
        }
    }
})
$script:UI.SessionAccount.Add_SelectionChanged({Filter-Sessions})
$script:UI.SearchBox.Add_TextChanged({Filter-Sessions})
$script:UI.SessionGrid.Add_SelectionChanged({$script:UI.SelectionLabel.Text='已选择 '+$script:UI.SessionGrid.SelectedItems.Count+' 条 · 所有账号使用同一份本地原文'})
$script:UI.CopyBtn.Add_Click({Invoke-Job @{Action='workspace-plan'} {param($v)
    Show-Modal '共享池方案 · 只读预览' ($v.Message+"`n`n当前索引："+$v.OriginalIndexes+" 条`n独立对话："+$v.UniqueConversations+" 条`n账号专属状态目录："+@($v.AccountLocalState).Count+" 个`n`n没有移动数据，也没有创建目录联接。") $false '' '知道了' {}
} '正在核对共享目录与账号状态…'})
$script:UI.UsageFromSessionBtn.Add_Click({$s=$script:UI.SessionGrid.SelectedItem;if(-not $s){Notice '请先选择一条对话。';return};foreach($item in $script:State.Pool){if($item.PoolKey -eq $s.PoolKey){$script:UI.UsageSession.SelectedItem=$item;break}};Navigate 'usage';Read-SelectedUsage})
$script:UI.ReadUsageBtn.Add_Click({Read-SelectedUsage})
$script:UI.BackupBtn.Add_Click({Show-Modal '创建完整会话备份' '请先从系统托盘退出 Claude。备份包含索引、原文、记忆、文件历史及关联状态，逐文件校验；不包含登录凭据。' $false '' '开始备份' {Invoke-Job @{Action='backup'} {param($v)Operation-Done $v} '正在备份并校验本地会话…'}})
$script:UI.BackupCards.AddHandler([Windows.Controls.Button]::ClickEvent,[Windows.RoutedEventHandler]{param($sender,$event)
    $b=$event.OriginalSource;if($b -isnot [Windows.Controls.Button]){return};$backup=$b.CommandParameter
    if($b.Tag -eq 'open'){Start-Process explorer.exe -ArgumentList ('"'+$backup.Path+'"')}
    elseif($b.Tag -eq 'undo'){$id=$backup.Id;Show-Modal '撤回这次新增索引' '请先退出 Claude。仅移回本次迁移创建且尚未被修改的索引，保留源账号、目标已有会话和全部原文。若新增会话已被使用或修改，将停止撤回。' $false '' '我已退出，撤回新增' ({Invoke-Job @{Action='undo';Id=$id;MaintenanceConfirmed=$true} {param($v)Operation-Done $v} '正在校验并撤回新增索引…'}.GetNewClosure())}
})
function Open-Original {Start-Process (Join-Path $PSScriptRoot 'docs\USER_GUIDE.html')}
$script:UI.OriginalBtn.Add_Click({Open-Original});$script:UI.SettingsOriginalBtn.Add_Click({Open-Original})
$script:UI.InstallFolderBtn.Add_Click({Start-Process explorer.exe -ArgumentList ('"'+$PSScriptRoot+'"')})
$script:UI.CacheDocsBtn.Add_Click({Start-Process 'https://platform.claude.com/docs/en/build-with-claude/prompt-caching'})
$timer=New-Object Windows.Threading.DispatcherTimer;$timer.Interval=[TimeSpan]::FromMilliseconds(250)
$timer.Add_Tick({
    if(-not $script:Job){return}
    $job=$script:Job
    if(-not $job.Process.HasExited){return}
    $script:Job=$null;$script:Busy=$false;$script:UI.BusyBar.Visibility='Collapsed';$script:UI.LocalLabel.Visibility='Visible'
    try{
        if(-not(Test-Path -LiteralPath $job.Response)){throw '操作进程意外退出，请重试；原数据不会被自动覆盖。'}
        $response=Read-Json $job.Response
        if(-not $response.Ok){throw $response.Error}
        $script:UI.StatusText.Text='已更新 · '+(Get-Date -Format 'HH:mm:ss')
        & $job.Callback $response.Result
    }catch{$problem=$_.Exception.Message;Notice $problem;$script:UI.StatusText.Text='操作未完成 · 请查看提示';if($job.OnError){& $job.OnError $problem}}
    finally{$job.Process.Dispose()}
})
$timer.Start()
$preferences=Read-Json (Join-Path $script:Data 'preferences.json')
$script:OnboardingVersion=[int](Prop $preferences 'OnboardingVersion' 0);$script:OnboardingSkipped=[bool](Prop $preferences 'OnboardingSkipped' $false)
Set-Theme ([bool](Prop $preferences 'Dark' $true)) $false
Set-ReadingSize ([double](Prop $preferences 'FontSize' 15)) $false
for($i=0;$i -lt $script:UI.FontSizeChoice.Items.Count;$i++){if([double]$script:UI.FontSizeChoice.Items[$i].Tag -eq $script:FontSize){$script:UI.FontSizeChoice.SelectedIndex=$i;break}}
Navigate 'accounts'

function Read-NativePreview($Request){
    $dir=Join-Path $SnapshotDir 'checks';[void][IO.Directory]::CreateDirectory($dir)
    $id=[guid]::NewGuid().ToString('N');$req=Join-Path $dir ($id+'.request.json');$res=Join-Path $dir ($id+'.response.json')
    Write-Json $req $Request
    & (Join-Path $PSScriptRoot 'claude-switch-core.exe') --request $req --response $res --base $PSScriptRoot --kernel 'D:\programfile\Claude Desktop Account Switcher'
    if($LASTEXITCODE){throw 'Native read-only preview failed'}
    $result=Read-Json $res;if(-not $result.Ok){throw $result.Error};return $result.Result
}

if($SmokeTest){
    $preferencePath=Join-Path $script:Data 'preferences.json';$preferenceBefore=$(if(Test-Path -LiteralPath $preferencePath){[IO.File]::ReadAllText($preferencePath)}else{$null})
    Update-State (Read-NativePreview @{Action='state'})
    $script:UI.StatusText.Text='本地数据已读取 · 预览验证'
    if(-not $SnapshotDir){throw 'SmokeTest requires SnapshotDir'}
    [void][IO.Directory]::CreateDirectory($SnapshotDir)
    $script:Window.ShowActivated=$false
    $script:Window.Show()
    function Save-View([string]$Name){
        $script:Window.UpdateLayout();$script:Window.Dispatcher.Invoke([Windows.Threading.DispatcherPriority]::Render,[Action]{})
        $bitmap=New-Object Windows.Media.Imaging.RenderTargetBitmap([int]$script:Window.ActualWidth,[int]$script:Window.ActualHeight,96,96,[Windows.Media.PixelFormats]::Pbgra32)
        $bitmap.Render($script:Window)
        $encoder=New-Object Windows.Media.Imaging.PngBitmapEncoder;$encoder.Frames.Add([Windows.Media.Imaging.BitmapFrame]::Create($bitmap))
        $file=[IO.File]::Create((Join-Path $SnapshotDir ($Name+'.png')));try{$encoder.Save($file)}finally{$file.Dispose()}
    }
    Set-Theme $false $false;Save-View 'accounts-light'
    $navTextPositions=@()
    foreach($navName in @('NavAccounts','NavSessions','NavUsage','NavBackups','OriginalBtn','NavSettings')){
        $navGrid=$script:UI[$navName].Content
        if($navGrid -isnot [Windows.Controls.Grid] -or $navGrid.ColumnDefinitions[0].Width.Value -ne 24 -or $navGrid.ColumnDefinitions[1].Width.Value -ne 12){throw ('Navigation columns mismatch: '+$navName)}
        $path=$navGrid.Children[0].Children[0]
        if($path -isnot [Windows.Shapes.Path] -or $path.Width -ne 20 -or $path.Height -ne 20 -or $path.StrokeThickness -ne 1.6){throw ('Navigation vector mismatch: '+$navName)}
        $navTextPositions+=($navGrid.Children[1].TranslatePoint((New-Object Windows.Point(0,0)),$script:Window)).X
    }
    if(($navTextPositions|Measure-Object -Maximum).Maximum-($navTextPositions|Measure-Object -Minimum).Minimum -gt 0.1){throw 'Navigation text starts are not aligned'}
    Navigate 'sessions';Save-View 'sessions-light'
    $searchSample=@($script:State.Pool | Where-Object IsRegistered) | Select-Object -First 1
    if($searchSample){$script:UI.SearchBox.Text=[string]$searchSample.Title;if($script:UI.SessionGrid.Items.Count -eq 0){throw 'Session search failed'};$script:UI.SearchBox.Text=''}
    Navigate 'usage';$selected=$script:UI.UsageSession.SelectedItem;if($selected){Display-Usage (Read-NativePreview @{Action='usage';PoolKey=$selected.PoolKey})};Save-View 'usage-light'
    Navigate 'backups';Save-View 'backups-light'
    Navigate 'settings';Save-View 'settings-light'
    Set-ReadingSize 17 $false;Save-View 'settings-large';Set-ReadingSize 15 $false
    Set-Theme $true $false;Navigate 'accounts';Save-View 'accounts-dark'
    # Exercise this application's confirmation route with a nonexistent fixture provider.
    # Read-only mode must refuse before creating a request or launching an adapter.
    function Find-ProviderButton($Parent){
        if($Parent -is [Windows.Controls.Button] -and $Parent.Tag -eq 'provider-switch'){return $Parent}
        for($i=0;$i -lt [Windows.Media.VisualTreeHelper]::GetChildrenCount($Parent);$i++){
            $found=Find-ProviderButton ([Windows.Media.VisualTreeHelper]::GetChild($Parent,$i));if($found){return $found}
        }
        return $null
    }
    $probe=Find-ProviderButton $script:UI.ProviderCards
    if($probe){
        $probe.CommandParameter=[pscustomobject]@{Id='verification-nonexistent';Name='隔离界面验证'}
        $probe.RaiseEvent((New-Object Windows.RoutedEventArgs([Windows.Controls.Button]::ClickEvent,$probe)))
        if($script:UI.Modal.Visibility -ne 'Visible'){throw 'Provider confirmation was not displayed'}
        $script:UI.ModalOk.RaiseEvent((New-Object Windows.RoutedEventArgs([Windows.Controls.Button]::ClickEvent,$script:UI.ModalOk)))
        if($script:Busy -or $script:UI.NoticeText.Text -notmatch '只读预览'){throw 'Read-only UI confirmation guard failed'}
    }
    function Find-AccountButton($Parent){
        if($Parent -is [Windows.Controls.Button] -and $Parent.Tag -eq 'account-action'){return $Parent}
        for($i=0;$i -lt [Windows.Media.VisualTreeHelper]::GetChildrenCount($Parent);$i++){
            $found=Find-AccountButton ([Windows.Media.VisualTreeHelper]::GetChild($Parent,$i));if($found){return $found}
        }
        return $null
    }
    $accountProbe=Find-AccountButton $script:UI.AccountCards
    if(-not $accountProbe){throw 'Account action button missing'}
    foreach($action in @('login-guide','capture','switch')){
        $accountProbe.CommandParameter=[pscustomobject]@{Uuid='11111111-1111-4111-8111-111111111111';Name='name+tag@example.test';PrimaryAction=$action;Profile='verification-nonexistent';SwitchKind='official'}
        $accountProbe.RaiseEvent((New-Object Windows.RoutedEventArgs([Windows.Controls.Button]::ClickEvent,$accountProbe)))
        if($script:UI.Modal.Visibility -ne 'Visible'){throw ('Account confirmation missing: '+$action)}
        Save-View ('account-'+$action)
        if($action -eq 'login-guide'){$script:UI.Modal.Visibility='Collapsed';continue}
        if($action -eq 'capture' -and $script:UI.ModalInput.Text -ne 'name+tag@example.test'){throw 'Email display name did not reach capture wizard'}
        $script:UI.ModalOk.RaiseEvent((New-Object Windows.RoutedEventArgs([Windows.Controls.Button]::ClickEvent,$script:UI.ModalOk)))
        if($script:Busy -or $script:UI.NoticeText.Text -notmatch '只读预览'){throw ('Account read-only guard failed: '+$action)}
    }
    if($script:UI.Onboarding.Visibility -ne 'Collapsed'){throw 'Smoke test unexpectedly opened onboarding automatically'}
    $script:UI.OnboardingBtn.RaiseEvent((New-Object Windows.RoutedEventArgs([Windows.Controls.Button]::ClickEvent,$script:UI.OnboardingBtn)))
    if($script:UI.Onboarding.Visibility -ne 'Visible' -or $script:OnboardingStep -ne 0){throw 'Onboarding settings entry failed'}
    Save-View 'onboarding-1'
    $script:UI.GuideNextBtn.RaiseEvent((New-Object Windows.RoutedEventArgs([Windows.Controls.Button]::ClickEvent,$script:UI.GuideNextBtn)))
    if($script:OnboardingStep -ne 1){throw 'Onboarding next step failed'}
    Save-View 'onboarding-2'
    $script:UI.GuideBackBtn.RaiseEvent((New-Object Windows.RoutedEventArgs([Windows.Controls.Button]::ClickEvent,$script:UI.GuideBackBtn)))
    if($script:OnboardingStep -ne 0){throw 'Onboarding previous step failed'}
    $script:UI.GuideNextBtn.RaiseEvent((New-Object Windows.RoutedEventArgs([Windows.Controls.Button]::ClickEvent,$script:UI.GuideNextBtn)))
    $script:UI.GuideNextBtn.RaiseEvent((New-Object Windows.RoutedEventArgs([Windows.Controls.Button]::ClickEvent,$script:UI.GuideNextBtn)))
    if($script:OnboardingStep -ne 2){throw 'Onboarding final step failed'}
    Save-View 'onboarding-3'
    $script:UI.GuideNextBtn.RaiseEvent((New-Object Windows.RoutedEventArgs([Windows.Controls.Button]::ClickEvent,$script:UI.GuideNextBtn)))
    if($script:UI.Onboarding.Visibility -ne 'Collapsed'){throw 'Onboarding completion failed'}
    Show-Onboarding
    $script:UI.GuideSkipBtn.RaiseEvent((New-Object Windows.RoutedEventArgs([Windows.Controls.Button]::ClickEvent,$script:UI.GuideSkipBtn)))
    if($script:UI.Onboarding.Visibility -ne 'Collapsed' -or -not $script:OnboardingSkipped){throw 'Onboarding skip failed'}
    $preferenceAfter=$(if(Test-Path -LiteralPath $preferencePath){[IO.File]::ReadAllText($preferencePath)}else{$null})
    if($preferenceBefore -cne $preferenceAfter){throw 'Smoke test changed persisted preferences'}
    $script:Window.Close();$timer.Stop()
    [pscustomobject]@{Accounts=@($script:State.Accounts).Count;Sessions=@($script:State.Sessions).Count;Pool=$script:State.PoolCount;Providers=@($script:State.Providers.Providers).Count;ReadOnlyUiGuard=$true;AccountWizardRoutes=$true;EmailDisplayName=$true;SidebarVectorsAligned=$true;OnboardingNavigation=$true;PreviewPreferencesUnchanged=$true;Snapshots=$SnapshotDir;Passed=$true}|ConvertTo-Json
}else{
    $script:Window.Add_ContentRendered({Refresh-State;if(-not $ReadOnly -and -not $SmokeTest -and -not $script:OnboardingOpened -and $script:OnboardingVersion -lt 1){Show-Onboarding}})
    try{[void]$script:Window.ShowDialog()}catch{[void][IO.Directory]::CreateDirectory($script:Data);[IO.File]::WriteAllText((Join-Path $script:Data 'startup-error.log'),($_ | Out-String));throw}
    $timer.Stop()
}
