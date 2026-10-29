param([switch]$SmokeTest,[string]$SnapshotDir,[switch]$ReadOnly,[switch]$Tray)
$ErrorActionPreference='Stop'
if($PSVersionTable.PSEdition -ne 'Desktop'){throw '请通过桌面快捷方式或 Launch.vbs 启动此工具。界面使用 Windows 自带的 PowerShell。'}
Add-Type -AssemblyName PresentationFramework,PresentationCore,WindowsBase,System.Windows.Forms,System.Drawing
# Without its own AppUserModelID the window is grouped under powershell.exe on the taskbar.
# The installer gives the shortcuts the same ID, so a pinned icon and the window share a button.
Add-Type -Namespace Studio -Name Shell -MemberDefinition '[DllImport("shell32.dll")] public static extern int SetCurrentProcessExplicitAppUserModelID([MarshalAs(UnmanagedType.LPWStr)] string id);'
[void][Studio.Shell]::SetCurrentProcessExplicitAppUserModelID('ClaudeSwitchStudio.App')
. (Join-Path $PSScriptRoot 'studio-core.ps1')
Initialize-Studio (Split-Path $PSScriptRoot -Parent) ''
if(-not(Test-Path -LiteralPath (Join-Path $script:Kernel 'claude-profile.ps1'))){$script:Kernel='D:\programfile\Claude Desktop Account Switcher'}
$script:State=$null; $script:Busy=$false; $script:Job=$null; $script:Dark=$true; $script:FontSize=15; $script:Page='accounts'
$script:OnboardingVersion=0;$script:OnboardingStep=0;$script:OnboardingSkipped=$false;$script:OnboardingTouched=$false;$script:OnboardingOpened=$false
$script:OAuth=$null;$script:OAuthCancel=$null;$script:OAuthModal=$false;$script:VendorState=$null;$script:VendorEditing=$null;$script:VendorDetected=$null;$script:VendorFilling=$false;$script:CloudState=$null;$script:CloudChecked=$false
$script:ToolsLoaded=$false;$script:CodexState=$null;$script:McpState=$null;$script:SkillsState=$null
$script:TrayIcon=$null;$script:StatsLoaded=$false;$script:Exiting=$false;$script:CloseToTray=$true;$script:AutoSync=$true;$script:TrayHintShown=$false;$script:ShowEvent=$null;$script:ExitEvent=$null
$script:RunKey='HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
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

function Notice([string]$Text) {
    $script:UI.NoticeText.Text=$Text
    $script:UI.Notice.BeginAnimation([Windows.UIElement]::OpacityProperty,$null);$script:UI.Notice.Opacity=1;$script:UI.Notice.Visibility='Visible'
    if($script:NoticeTimer){$script:NoticeTimer.Stop();$script:NoticeTimer.Start()}
    # Results of an operation started from the tray still reach the user while the window is hidden.
    if($script:TrayIcon -and -not $script:Window.IsVisible -and $Text){$script:TrayIcon.ShowBalloonTip(6000,'Claude Switch Studio',$Text,[Windows.Forms.ToolTipIcon]::Info)}
}
function Show-Main {
    $script:Window.Show()
    if($script:Window.WindowState -eq 'Minimized'){$script:Window.WindowState='Normal'}
    [void]$script:Window.Activate();$script:Window.Topmost=$true;$script:Window.Topmost=$false
}
function Exit-Studio {
    if($script:Busy){Show-Main;Notice '操作仍在进行，请等它完成后再退出。';return}
    $script:Exiting=$true;$script:Window.Close()
}
function Get-Autostart {
    try{return [bool](Get-ItemProperty -LiteralPath $script:RunKey -Name 'ClaudeSwitchStudio' -ErrorAction Stop)}catch{return $false}
}
function Set-Autostart([bool]$On) {
    if($ReadOnly -or $SmokeTest){Notice '当前为只读预览，未修改开机自启。';return}
    $exe=Join-Path $PSScriptRoot 'ClaudeSwitchStudio.exe'
    if($On -and -not(Test-Path -LiteralPath $exe)){Notice '从源码运行时无法设置开机自启，请使用安装版。';return}
    if($On){Set-ItemProperty -LiteralPath $script:RunKey -Name 'ClaudeSwitchStudio' -Value ('"'+$exe+'" --tray')}
    else{Remove-ItemProperty -LiteralPath $script:RunKey -Name 'ClaudeSwitchStudio' -ErrorAction SilentlyContinue}
    Notice $(if($On){'已开启开机自启：登录 Windows 后在托盘中运行。'}else{'已关闭开机自启。'})
}
function Open-Claude {
    $id=@(Get-StartApps | Where-Object {$_.AppID -like 'Claude_*!*'} | Select-Object -First 1 -ExpandProperty AppID)
    if($id){Start-Process explorer.exe ('shell:AppsFolder\'+$id[0])}else{Notice '未找到 Claude Desktop 的启动入口。'}
}
function New-TrayItem([string]$Text,[scriptblock]$Click=$null,$Tag=$null) {
    $item=New-Object Windows.Forms.ToolStripMenuItem $Text;$item.Tag=$Tag
    if($Click){$item.Add_Click($Click)}else{$item.Enabled=$false}
    return $item
}
# Rebuilt each time it opens, so the account list matches the latest refresh.
function Build-TrayMenu {
    $menu=$script:TrayIcon.ContextMenuStrip;$menu.Items.Clear()
    $open=New-TrayItem '打开主界面' {Show-Main};$open.Font=New-Object Drawing.Font($open.Font,[Drawing.FontStyle]::Bold);[void]$menu.Items.Add($open)
    [void]$menu.Items.Add((New-Object Windows.Forms.ToolStripSeparator))
    $accounts=@(if($script:State){$script:State.Accounts | Where-Object {$_.SwitchKind -eq 'official'}})
    $current=@($accounts | Where-Object {$_.IsCurrent}) | Select-Object -First 1
    [void]$menu.Items.Add((New-TrayItem ('当前：'+$(if($current){$current.Name}else{'未识别'}))))
    $switchMenu=New-TrayItem '切换账号' {}
    foreach($account in @($accounts | Where-Object {$_.Saved -and -not $_.IsCurrent})){
        [void]$switchMenu.DropDownItems.Add((New-TrayItem ([string]$account.Name) {param($sender,$e) Show-Main;Start-Switch $sender.Tag} $account))
    }
    if(-not $switchMenu.DropDownItems.Count){[void]$switchMenu.DropDownItems.Add((New-TrayItem '暂无其他已收录账号'))}
    [void]$switchMenu.DropDownItems.Add((New-Object Windows.Forms.ToolStripSeparator))
    [void]$switchMenu.DropDownItems.Add((New-TrayItem '添加账号…' {Show-Main;Show-LoginGuide}))
    [void]$menu.Items.Add($switchMenu)
    $codexMenu=New-TrayItem '切换 Codex 账号' {}
    foreach($account in @(if($script:CodexState){$script:CodexState.Accounts | Where-Object {$_.Saved -and -not $_.IsCurrent}})){
        [void]$codexMenu.DropDownItems.Add((New-TrayItem ([string]$account.Title) {param($sender,$e) Show-Main;Navigate 'accounts';Start-CodexSwitch $sender.Tag} $account))
    }
    if(-not $codexMenu.DropDownItems.Count){[void]$codexMenu.DropDownItems.Add((New-TrayItem $(if($script:CodexState){'暂无其他已收录的 Codex 账号'}else{'打开主界面后显示'})))}
    [void]$menu.Items.Add($codexMenu)
    [void]$menu.Items.Add((New-TrayItem '打开 Claude' {Open-Claude}))
    [void]$menu.Items.Add((New-TrayItem '刷新状态' {Refresh-State}))
    [void]$menu.Items.Add((New-Object Windows.Forms.ToolStripSeparator))
    $auto=New-TrayItem '开机自启' {Set-Autostart (-not (Get-Autostart));Sync-StartupOptions};$auto.Checked=Get-Autostart;[void]$menu.Items.Add($auto)
    [void]$menu.Items.Add((New-TrayItem '❤ 去 GitHub 点赞' {Start-Process 'https://github.com/dantengs/ClaudeSwitchStudio'}))
    [void]$menu.Items.Add((New-TrayItem '退出 Claude Switch Studio' {Exit-Studio}))
}
function Initialize-Tray {
    $script:TrayIcon=New-Object Windows.Forms.NotifyIcon
    $script:TrayIcon.Icon=New-Object Drawing.Icon((Join-Path $PSScriptRoot 'assets\app.ico'),[Windows.Forms.SystemInformation]::SmallIconSize)
    $script:TrayIcon.Text='Claude Switch Studio'
    $script:TrayIcon.ContextMenuStrip=New-Object Windows.Forms.ContextMenuStrip
    $script:TrayIcon.ContextMenuStrip.Add_Opening({Build-TrayMenu})
    $script:TrayIcon.Add_MouseClick({param($sender,$e) if($e.Button -eq [Windows.Forms.MouseButtons]::Left){Show-Main}})
    $script:TrayIcon.Visible=$true
}
function Sync-StartupOptions {
    $script:UI.AutostartCheck.IsChecked=Get-Autostart;$script:UI.CloseToTrayCheck.IsChecked=$script:CloseToTray;$script:UI.AutoSyncCheck.IsChecked=$script:AutoSync
}
function Navigate([string]$Page) {
    $script:Page=$Page
    $titles=@{accounts=@('账号管理','官方账号一键切换，第三方账号按厂商管理，接入表决定每个应用用哪个来源。');tools=@('MCP 与技能','按应用分别开关 MCP 服务和技能，修改前自动备份。');sessions=@('共享对话池','一条原文只展示一次，账号与提供商作为独立身份。');usage=@('用量统计','按账号、模型和时间汇总本机记录的 token 用量与缓存；「今日」为当天 0 点到 24 点。');backups=@('备份中心','每次变更前，保留带时间戳的可核验副本。');settings=@('设置与本地记忆','更舒适的阅读，清晰可见的本地数据。')}
    foreach($key in @('accounts','sessions','usage','tools','backups','settings')) {
        $control=$key.Substring(0,1).ToUpper()+$key.Substring(1)+'Page'
        $script:UI[$control].Visibility=$(if($Page -eq $key){'Visible'}else{'Collapsed'})
        $nav='Nav'+$key.Substring(0,1).ToUpper()+$key.Substring(1)
        $script:UI[$nav].Background=$(if($Page -eq $key){$script:Window.Resources['Tint']}else{[Windows.Media.Brushes]::Transparent})
        $script:UI[$nav].Foreground=$(if($Page -eq $key){$script:Window.Resources['Accent']}else{$script:Window.Resources['Muted']})
    }
    $script:UI.PageTitle.Text=$titles[$Page][0];$script:UI.PageSubtitle.Text=$titles[$Page][1]
    if($Page -eq 'usage' -and -not $script:StatsLoaded -and -not $script:Busy -and -not $SmokeTest){Load-Stats}
    if($Page -eq 'tools' -and -not $script:ToolsLoaded -and -not $script:Busy -and -not $SmokeTest){Load-Tools}
    if($Page -eq 'backups' -and -not $script:CloudState -and -not $script:Busy -and -not $SmokeTest){Load-Cloud}
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
    $updated.Dark=$script:Dark;$updated.FontSize=$script:FontSize;$updated.CloseToTray=$script:CloseToTray;$updated.AutoSync=$script:AutoSync
    $updated.OnboardingVersion=[Math]::Max([int](Prop $existing 'OnboardingVersion' 0),$script:OnboardingVersion)
    if($script:OnboardingTouched){$updated.OnboardingSkipped=$script:OnboardingSkipped}
    Write-Json $path $updated
}
function Show-Onboarding([int]$Step=0){
    $script:OnboardingOpened=$true;$script:OnboardingStep=[Math]::Max(0,[Math]::Min(2,$Step))
    $steps=@(
        @{Title='先查看，再决定';Body="你可以直接查看账号、共享对话池和历史用量。浏览这些页面不需要退出 Claude，也不会替你登录或切换。`n`n卡片里的历史账号，只表示本地有这个账号的记录，不代表已保存可切换的登录。";Hint='先认识页面：账号管理 → 共享对话池 → 缓存与用量。'},
        @{Title='添加多个账号，一键切换';Body="点「添加账号」，工具先保存当前账号，再让 Claude 打开登录页；你在 Claude 中登录新账号后，点「收录当前登录」。仅登录 Claude Code 不能代替桌面版登录。`n`n切换时工具会检测 Claude 是否在运行，经你确认后自动退出、备份、切换并重新打开 Claude。";Hint='桌面登录快照是实验功能，未经官方支持，不能保证不会触发账号限制。'},
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
    $script:UI.MemoryCards.ItemsSource=@($State.Memory)
    $script:UI.ClaudeSummary.Text='本地身份 '+@($State.Accounts).Count+' 个 · 可一键切换 '+@($State.Accounts | Where-Object {$_.Saved}).Count+' 个 · 去重对话 '+$State.RegisteredPoolCount+' 条 · 切换时自动检测 Claude，确认后退出并重新打开'
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
    Build-Routes
    if($script:TrayIcon){
        $current=@($State.Accounts | Where-Object {$_.SwitchKind -eq 'official' -and $_.IsCurrent}) | Select-Object -First 1
        $tip='Claude Switch Studio · '+$(if($current){$current.Name}else{'未识别当前账号'})
        $script:TrayIcon.Text=$tip.Substring(0,[Math]::Min(63,$tip.Length))  # NotifyIcon rejects longer tooltips.
    }
}
function Start-Switch($Account) {
    Confirm-ClaudeExit ('切换到 '+$Account.Name) '工具会按时间备份，先收录当前账号的最新登录缓存，再恢复目标账号。原文与记忆留在原处。桌面登录快照为实验功能，未经官方支持，无法保证账号使用风险。' '切换账号' @{Action='switch';Name=[string]$Account.Profile;ExpectedAccount=[string]$Account.Uuid} '正在备份当前登录并切换…'
}
function Invoke-Job($Request,[scriptblock]$Callback,[string]$Message='正在处理…',[scriptblock]$OnError=$null) {
    if(($ReadOnly -or $SmokeTest) -and $Request.Action -notin @('state','usage','usage-stats','workspace-plan','claude-processes','codex-processes','codex-state','mcp-state','skills-state','vendor-state','cloud-state')){Notice '当前为只读预览。切换、保存和迁移需要你退出 Claude 后再进行。';return}
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
    try{[void]$p.Start();$script:Job=@{Process=$p;Request=$requestPath;Response=$responsePath;Callback=$Callback;OnError=$OnError;Start=[DateTime]::UtcNow}}
    catch{$script:Busy=$false;$script:UI.BusyBar.Visibility='Collapsed';$script:UI.LocalLabel.Visibility='Visible';Notice $_.Exception.Message}
}
# Claude accounts, then Codex accounts, then third-party accounts: the access table needs all three.
function Refresh-State {Invoke-Job @{Action='state'} {param($value) Update-State $value;Load-Codex} '正在读取账号与会话…'}
function Operation-Done($Value) {
    if($Value -is [string]){Notice $Value}
    elseif($Value.PSObject.Properties['Message']){Notice $Value.Message}
    elseif($Value.PSObject.Properties['Verified']){Notice ('备份完成，'+$Value.Files+' 个文件逐一校验通过。');if(Start-CloudAfterBackup $Value){return}}
    if($script:Page -eq 'tools'){Load-Tools}else{Refresh-State}
}
# $Input is a PowerShell automatic variable and is always truthy inside a function, so use another name.
function Show-Modal([string]$Title,[string]$Body,[bool]$ShowInput,[string]$Value,[string]$Ok,[scriptblock]$Callback,[string]$CancelText='取消',[scriptblock]$OnCancel=$null) {
    if($script:Busy){Notice '请等待当前操作完成。';return}
    $script:UI.ModalTitle.Text=$Title;$script:UI.ModalBody.Text=$Body;$script:UI.ModalInput.Text=$Value
    $script:UI.ModalInput.Visibility=$(if($ShowInput){'Visible'}else{'Collapsed'});$script:UI.ModalOk.Content=$Ok
    $script:UI.ModalCancel.Content=$CancelText;$script:ModalCancelCallback=$OnCancel
    $script:ModalCallback=$Callback;$script:UI.Modal.Visibility='Visible'
    if($ShowInput){[void]$script:UI.ModalInput.Focus();$script:UI.ModalInput.SelectAll()}
}
function Exit-Text($Processes) {
    $app=[string](Prop $Processes 'Label' 'Claude Desktop');$short=$app.Split(' ')[0]
    if(-not $Processes.Blocking){return $app+' 当前未运行，可以直接执行。'}
    $text='检测到 '+$app+' 正在运行（'+$Processes.Desktop+' 个进程'
    if([int]$Processes.Agents){$text+='，其中 '+$Processes.Agents+' 个是它启动的后台任务'}
    $tail=$(if($short -eq 'Claude'){'独立运行的 Claude Code 命令行不受影响。'}else{''})
    return $text+"）。`n确认后工具会自动退出 "+$short+"，完成后重新打开。进行中的回复和任务会被中断，请先等它们结束。"+$tail
}
function Send-ClaudeRequest([hashtable]$Request,[bool]$WithName,[string]$Value,[bool]$Quit,[string]$Message,[scriptblock]$OnError,[string]$Short='Claude') {
    $copy=@{};foreach($key in $Request.Keys){$copy[$key]=$Request[$key]}
    if($WithName){$copy.DisplayName=$Value}
    $copy.MaintenanceConfirmed=$true;$copy.RelaunchClaude=$true;$copy.SyncConversations=[bool]$script:AutoSync
    if($Quit){$copy.QuitClaude=$true;$Message='正在退出 '+$Short+' · '+$Message}
    Invoke-Job $copy {param($v) Operation-Done $v} $Message $OnError
}
# One confirmation for every login or provider change: check the app first, then ask once.
function Confirm-ClaudeExit([string]$Title,[string]$Body,[string]$Ok,[hashtable]$Request,[string]$Message,[bool]$WithName=$false,[string]$Value='',[scriptblock]$OnError=$null,[string]$App='claude') {
    $show={param($p)
        # A nested GetNewClosure only captures this block's locals, so copy the outer values first.
        $request=$Request;$named=[bool]$WithName;$message=$Message;$failure=$OnError
        $short=([string](Prop $p 'Label' 'Claude Desktop')).Split(' ')[0]
        if($p.Blocking -and -not $p.CanQuit){
            $why=$(if([bool](Prop $p 'CliBlocks' $false)){'发现独立运行的 '+[string]$p.CliLabel+'，它可能在刷新登录时改写账号文件，工具不会关闭它。请先结束这些会话后继续。'}else{'发现无法识别来源的 '+$short+' 进程，工具不会自动关闭它。请从系统托盘手动退出后继续。'})
            Show-Modal $Title ($Body+"`n`n"+$why) $named $Value '我已结束，继续' ({param($v) Send-ClaudeRequest $request $named $v $false $message $failure $short}.GetNewClosure())
            return
        }
        $quit=[bool]$p.Blocking
        $label=$(if($quit){'退出 '+$short+' 并'+$Ok}else{$Ok})
        Show-Modal $Title ($Body+"`n`n"+(Exit-Text $p)) $named $Value $label ({param($v) Send-ClaudeRequest $request $named $v $quit $message $failure $short}.GetNewClosure())
    }.GetNewClosure()
    $appLabel=$(if($App -eq 'codex'){'Codex'}elseif($App -eq 'agytools'){'Antigravity Tools'}else{'Claude Desktop'})
    if($ReadOnly -or $SmokeTest){& $show ([pscustomobject]@{Blocking=$false;CanQuit=$true;Desktop=0;Agents=0;Label=$appLabel});return}
    Invoke-Job @{Action=$(if($App -eq 'codex'){'codex-processes'}elseif($App -eq 'agytools'){'agy-processes'}else{'claude-processes'})} $show ('正在检测 '+$appLabel+' 是否运行…')
}
function Format-Stamp([string]$Stamp) {
    if($Stamp -match '^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})'){return $Matches[1]+'-'+$Matches[2]+'-'+$Matches[3]+' '+$Matches[4]+':'+$Matches[5]}
    return ''
}
function Display-Codex($Value) {
    $script:CodexState=$Value;$script:CodexLoaded=$true
    $rows=@($Value.Accounts)
    foreach($a in $rows){
        $title=$(if($a.DisplayName){[string]$a.DisplayName}elseif($a.Email){[string]$a.Email}else{'Codex 账号 '+([string]$a.Account).Substring(0,[Math]::Min(8,([string]$a.Account).Length))})
        $a | Add-Member NoteProperty Title $title -Force
        $a | Add-Member NoteProperty Initial $title.Substring(0,1).ToUpper() -Force
        $a | Add-Member NoteProperty StatusText $(if($a.IsCurrent){'当前登录'}elseif($a.Saved){'已收录'}else{'未收录'}) -Force
        $a | Add-Member NoteProperty PlanText $(if($a.Plan){([string]$a.Plan).ToUpper()}else{'套餐未知'}) -Force
        $saved=Format-Stamp ([string]$a.SavedAt)
        $a | Add-Member NoteProperty SavedText $(if($saved){'收录于 '+$saved}elseif($a.Saved){'已收录'}else{'未收录'}) -Force
        $quota=@()
        $limits=$a.RateLimits
        if($limits){
            foreach($w in @($limits.Windows)){
                $used=[double]$w.Used
                $color=$(if($used -ge 85){'#E5484D'}elseif($used -ge 60){'#E5A23B'}else{'#2FA36B'})
                $quota+=[pscustomobject]@{Label=[string]$w.Label+'额度';Text=('已用 {0:0}%' -f $used)+$(if($w.Resets){' · 重置 '+$w.Resets}else{''});BarWidth=[string][Math]::Max(3,[Math]::Min(100,$used)*3);Brush=$color}
            }
        }
        $a | Add-Member NoteProperty Quota $quota -Force
        $at='';if($limits -and $limits.At){try{$at=([datetimeoffset]::Parse([string]$limits.At)).LocalDateTime.ToString('MM-dd HH:mm')}catch{}}
        $a | Add-Member NoteProperty QuotaNote $(if($quota.Count){'额度记录于 '+$at+'，来自 Codex 会话'}else{'还没有这个账号的额度记录；用它在 Codex 中对话后会出现。'}) -Force
        $a | Add-Member NoteProperty ActionLabel $(if($a.IsCurrent -and $a.Saved){'更新登录状态'}elseif($a.IsCurrent){'收录当前登录'}else{'切换账号'}) -Force
        $a | Add-Member NoteProperty AccountShort ('ID '+([string]$a.Account).Substring(0,[Math]::Min(12,([string]$a.Account).Length))+'…') -Force
    }
    $script:UI.CodexCards.ItemsSource=$rows
    $script:UI.CodexEmpty.Visibility=$(if($rows.Count){'Collapsed'}else{'Visible'})
    $current=@($rows | Where-Object {$_.IsCurrent}) | Select-Object -First 1
    $script:UI.CodexSummary.Text='已收录 '+@($rows | Where-Object {$_.Saved}).Count+' 个账号 · 当前：'+$(if($current){$current.Title}else{'未登录'})+' · 添加账号在后台授权，不影响正在使用的 Codex'
    Build-Routes
}
function Load-Codex {Invoke-Job @{Action='codex-state'} {param($v) Display-Codex $v;Load-Vendors} '正在读取 Codex 账号与额度…'}
function Start-CodexSwitch($Account) {
    Confirm-ClaudeExit ('切换到 '+$Account.Title) '工具会按时间备份，先保存当前 Codex 账号的最新登录，再换成目标账号。会话记录在本机共享，不需要移动。登录快照为实验功能，未经官方支持。' '切换账号' @{Action='codex-switch';Account=[string]$Account.Account} '正在备份并切换 Codex 账号…' -App 'codex'
}
function Start-CodexSave([string]$Expected='') {
    # Only reads ~/.codex/auth.json, so Codex may keep running.
    $request=@{Action='codex-save';MaintenanceConfirmed=$true};if($Expected){$request.ExpectedAccount=$Expected}
    Invoke-Job $request {param($v) Operation-Done $v} '正在备份并收录 Codex 当前登录…'
}
# Adding an account runs the official `codex login` in the background, like Antigravity Tools'
# OAuth: the token lands in the saved-account list and the running Codex is left alone.
function Start-CodexAdd {
    if($script:OAuth){Show-OAuthWaiting;return}
    Show-Modal '添加 Codex 账号' "工具会在后台运行 Codex 自带的官方登录（codex login），并在浏览器中打开 OpenAI 官方登录页。`n`n1. 在浏览器中登录要添加的账号并授权。`n2. 完成后账号自动加入列表，授权文件只保存在本机（仅当前 Windows 用户可读）。`n`n正在使用的 Codex 不会退出，也不会切换账号；需要时再在卡片上点「切换账号」。" $false '' '打开浏览器授权' {
        Invoke-Job @{Action='codex-oauth-start';MaintenanceConfirmed=$true} {param($v)
            $script:OAuth=@{Id=[string]$v.Id;Url=[string]$v.Url;Started=[DateTime]::UtcNow}
            $script:UI.CodexAddBtn.Content='等待浏览器授权…'
            Show-OAuthWaiting
        } '正在启动官方 Codex 登录…'
    }
}
function Show-OAuthWaiting {
    $url=[string]$script:OAuth.Url
    $body="请在浏览器中完成 OpenAI 登录。完成后本窗口会自动关闭，账号会出现在列表中。"+$(if($url){"`n`n浏览器没有打开时，可以复制下面的链接自行打开："}else{''})
    $script:OAuthModal=$true
    Show-Modal '等待浏览器授权' $body ([bool]$url) $url '复制登录链接' {param($v) if($v){[Windows.Clipboard]::SetText($v);Notice '登录链接已复制。授权完成前，可再次点击「等待浏览器授权…」查看或取消。'}} '取消添加' {Stop-OAuth}
}
function Stop-OAuth {
    if($script:OAuth){$script:OAuthCancel=[string]$script:OAuth.Id}
    $script:OAuth=$null;$script:UI.CodexAddBtn.Content='＋  添加账号'
    Poll-OAuth  # Sends the cancel now, or on the next tick if a status check is still running.
}
function Poll-OAuth {
    if($script:Busy){return}
    if($script:OAuthCancel){
        $id=$script:OAuthCancel;$script:OAuthCancel=$null
        Invoke-Job @{Action='codex-oauth-cancel';Id=$id;MaintenanceConfirmed=$true} {param($v) Notice $v.Message} '正在取消授权…'
        return
    }
    if(-not $script:OAuth){return}
    if(([DateTime]::UtcNow-$script:OAuth.Started).TotalMinutes -gt 10){Notice '浏览器授权超过 10 分钟未完成，已取消。';Stop-OAuth;return}
    Invoke-Job @{Action='codex-oauth-status';Id=[string]$script:OAuth.Id;MaintenanceConfirmed=$true} {param($v)
        if(-not $script:OAuth){return}  # Cancelled while this check was running.
        if($v.State -eq 'waiting'){if($v.Url -and -not $script:OAuth.Url){$script:OAuth.Url=[string]$v.Url};return}
        $script:OAuth=$null;$script:UI.CodexAddBtn.Content='＋  添加账号'
        if($script:OAuthModal -and $script:UI.Modal.Visibility -eq 'Visible' -and $script:UI.ModalTitle.Text -eq '等待浏览器授权'){$script:UI.Modal.Visibility='Collapsed'}
        $script:OAuthModal=$false
        Notice $v.Message
        if($v.State -eq 'done'){Load-Codex}
    } '正在等待浏览器授权…'
}
function Set-BulkLabels {
    foreach($pair in @(@('McpAllClaude',$script:McpState,'Servers','Claude','Claude Code'),@('McpAllCodex',$script:McpState,'Servers','Codex','Codex'),@('SkillAllClaude',$script:SkillsState,'Skills','Claude','Claude Code'),@('SkillAllCodex',$script:SkillsState,'Skills','Codex','Codex'))){
        $rows=@(if($pair[1]){$pair[1].($pair[2])});$all=$rows.Count -gt 0 -and @($rows | Where-Object {-not $_.($pair[3])}).Count -eq 0
        $script:UI[$pair[0]].Content=$pair[4]+$(if($all){' 全不选'}else{' 全选'});$script:UI[$pair[0]].IsEnabled=$rows.Count -gt 0
    }
}
function Start-Bulk([string]$Kind,[string]$App) {
    $state=$(if($Kind -eq 'mcp'){$script:McpState}else{$script:SkillsState});$rows=@(if($state){$state.$(if($Kind -eq 'mcp'){'Servers'}else{'Skills'})})
    $field=$(if($App -eq 'claude'){'Claude'}else{'Codex'});$where=$(if($App -eq 'claude'){'Claude Code'}else{'Codex'});$noun=$(if($Kind -eq 'mcp'){'MCP 服务'}else{'技能'})
    $enable=@($rows | Where-Object {-not $_.$field}).Count -gt 0
    $count=@($rows | Where-Object {[bool]$_.$field -ne $enable}).Count
    $body='将在 '+$where+' 中'+$(if($enable){'开启'}else{'关闭'})+' '+$count+' 个'+$noun+'。'+$(if($enable){'开启时从另一个应用或本工具的库复制配置。'}else{'关闭前每一项都会先备份，并在本工具的库中保留副本，随时可以重新开启。'})+'重新启动 '+$where+' 后生效。'
    $request=@{Action=$Kind+'-bulk';App=$App;Enabled=$enable;MaintenanceConfirmed=$true}
    Show-Modal ($(if($enable){'全部开启'}else{'全部关闭'})+' · '+$where) $body $false '' $(if($enable){'全部开启'}else{'全部关闭'}) ({Invoke-Job $request {param($v) Notice $v.Message;Load-Tools} '正在批量修改…'}.GetNewClosure())
}
function Load-Vendors {Invoke-Job @{Action='vendor-state'} {param($v) Display-Vendors $v;Start-CloudCheck} '正在读取第三方账号…'}
function Display-Vendors($Value) {
    $script:VendorState=$Value
    $names=@{'claude-code'='Claude Code';'codex'='Codex';'claude-desktop'='Claude Desktop'}
    $groups=@()
    foreach($group in @($Value.Accounts | Group-Object VendorName)){
        $rows=@($group.Group)
        foreach($a in $rows){
            $ability=@();if($a.CanClaude){$ability+='Claude'};if($a.CanCodex){$ability+='Codex'}
            $a | Add-Member NoteProperty Ability $(if($ability.Count){'可接入 '+($ability -join ' / ')}else{'暂不能接入'}) -Force
            $a | Add-Member NoteProperty Endpoint $(if($a.Anthropic){[string]$a.Anthropic}else{[string]$a.OpenAI}) -Force
            $a | Add-Member NoteProperty ModelText ('默认模型：'+$(if($a.Model){[string]$a.Model}else{'未设置'})+$(if(@($a.Models).Count){' · 检测到 '+@($a.Models).Count+' 个'}else{''})) -Force
            $used=@(@($a.UsedBy) | Where-Object {$_} | ForEach-Object {$names[[string]$_]})
            $a | Add-Member NoteProperty InUse ([bool]$used.Count) -Force
            $a | Add-Member NoteProperty UsedText $(if($used.Count){'正在用于 '+($used -join '、')}else{''}) -Force
        }
        $groups+=[pscustomobject]@{Title=[string]$group.Name+' · '+$rows.Count+' 个';Accounts=$rows}
    }
    $script:UI.VendorGroups.ItemsSource=$groups
    $count=@($Value.Accounts).Count
    $script:UI.VendorEmpty.Visibility=$(if($count){'Collapsed'}else{'Visible'})
    $script:UI.VendorSummary.Text=$(if($count){[string]$count+' 个账号，按厂商分组 · Key 加密保存在本机'}else{'DeepSeek、Kimi、智谱、通义千问、MiniMax、OpenRouter、Gemini、Antigravity Tools 或自定义'})
    $script:UI.VendorImportBtn.IsEnabled=[bool]$Value.CcSwitch
    Build-Routes
}
function New-Source([string]$Label,[string]$Kind,$Item,[bool]$Enabled,[string]$Key){[pscustomobject]@{Label=$Label;Kind=$Kind;Item=$Item;Enabled=$Enabled;Key=$Key}}
function Select-Source($Box,[string]$Key){
    $items=@($Box.ItemsSource)
    for($i=0;$i -lt $items.Count;$i++){if($items[$i].Key -eq $Key){$Box.SelectedIndex=$i;return}}
    $Box.SelectedIndex=-1
}
function Vendor-Sources([string]$Need) {
    foreach($v in @($script:VendorState.Accounts)){
        $ok=[bool]$v.$Need
        $why=$(if($ok){''}elseif($Need -eq 'CanClaude'){'（没有 Anthropic 接口）'}else{'（没有 Codex 接口）'})
        New-Source ('第三方 · '+$v.Name+$why) 'vendor' $v $ok ('vendor:'+$v.Id)
    }
}
# The access table: one source per app. Official accounts switch logins; third-party accounts edit configuration.
function Build-Routes {
    if(-not $script:VendorState){$script:UI.RouteNote.Text='正在读取接入状态…';return}
    $routes=$script:VendorState.Routes
    $claude=@(if($script:State){$script:State.Accounts | Where-Object {$_.SwitchKind -eq 'official' -and $_.RootKey -eq 'official'}})
    $current=@($claude | Where-Object {$_.IsCurrent}) | Select-Object -First 1
    $r=$routes.'claude-desktop'
    $items=@($claude | Where-Object {$_.Saved -or $_.IsCurrent} | ForEach-Object {New-Source ('官方 · '+$_.Name+$(if($_.IsCurrent){'（当前登录）'}else{''})) 'claude-account' $_ $true ('claude:'+$_.Uuid)})
    if($r.Source -eq 'external'){$items+=New-Source ([string]$r.Label+'（当前）') 'external' $null $false 'external'}
    $items+=@(Vendor-Sources 'CanClaude')
    $script:UI.RouteDesktopSource.ItemsSource=$items
    Select-Source $script:UI.RouteDesktopSource $(if($r.Source -eq 'official'){if($current){'claude:'+$current.Uuid}else{''}}elseif($r.Source -eq 'external'){'external'}else{'vendor:'+$r.Source})
    $script:UI.RouteDesktopState.Text='当前：'+$(if($r.Source -eq 'official'){'官方 · '+$(if($current){$current.Name}else{'未识别'})}else{'第三方 · '+$r.Label})
    $r=$routes.'claude-code'
    $items=@(New-Source $(if($r.Source -eq 'external'){'保持现有设置（'+$r.Label+'）'}else{'官方登录（接入前的设置）'}) 'official' $null $true 'official')+@(Vendor-Sources 'CanClaude')
    $script:UI.RouteCodeSource.ItemsSource=$items
    Select-Source $script:UI.RouteCodeSource $(if($r.Source -in @('official','external')){'official'}else{'vendor:'+$r.Source})
    $script:UI.RouteCodeState.Text='当前：'+$(if($r.Source -eq 'official'){'官方登录'}elseif($r.Source -eq 'external'){[string]$r.Label}else{'第三方 · '+$r.Label+' · '+$r.Model})
    $r=$routes.codex
    $codex=@(if($script:CodexState){$script:CodexState.Accounts | Where-Object {$_.Saved -or $_.IsCurrent}})
    $items=@($codex | ForEach-Object {New-Source ('官方 · '+$_.Title+$(if($_.IsCurrent){'（当前登录）'}else{''})) 'codex-account' $_ ([bool]$_.Saved -or $_.IsCurrent) ('codex:'+$_.Account)})
    if($r.Source -eq 'external'){$items+=New-Source ([string]$r.Label+'（当前）') 'external' $null $false 'external'}
    $items+=@(Vendor-Sources 'CanCodex')
    $script:UI.RouteCodexSource.ItemsSource=$items
    $codexCurrent=$(if($script:CodexState){[string]$script:CodexState.Current}else{''})
    Select-Source $script:UI.RouteCodexSource $(if($r.Source -eq 'official'){'codex:'+$codexCurrent}elseif($r.Source -eq 'external'){'external'}else{'vendor:'+$r.Source})
    $codexName=@($codex | Where-Object {$_.IsCurrent} | ForEach-Object {$_.Title})
    $script:UI.RouteCodexState.Text='当前：'+$(if($r.Source -eq 'official'){'官方 · '+$(if($codexName){$codexName[0]}else{'未登录'})}elseif($r.Source -eq 'external'){[string]$r.Label}else{'第三方 · '+$r.Label+' · '+$r.Model})
    foreach($row in @('Code','Codex')){Update-RouteModel $row}
    $script:UI.RouteNote.Text='切换官方账号会检测并退出、重开对应应用；Claude Code 改的是 ~/.claude/settings.json，新开的会话立即生效。灰色选项表示该厂商没有对应的接口，无法直接接入。'
}
function Update-RouteModel([string]$Row) {
    $box=$script:UI['Route'+$Row+'Model'];$item=$script:UI['Route'+$Row+'Source'].SelectedItem
    if(-not $item -or $item.Kind -ne 'vendor'){$box.ItemsSource=$null;$box.IsEnabled=$false;return}
    $a=$item.Item
    $models=@(@([string]$a.Model)+@($a.Models)+@([string]$a.Tiers.Opus,[string]$a.Tiers.Sonnet,[string]$a.Tiers.Haiku) | Where-Object {$_} | Select-Object -Unique)
    $box.ItemsSource=$models;$box.IsEnabled=$models.Count -gt 0
    $route=$script:VendorState.Routes.$(if($Row -eq 'Code'){'claude-code'}else{'codex'})
    $want=$(if($route.Source -eq $a.Id -and $route.Model){[string]$route.Model}else{[string]$a.Model})
    if($want -and $models -contains $want){$box.SelectedItem=$want}elseif($models.Count){$box.SelectedIndex=0}
}
function Start-Route([string]$Row) {
    $item=$script:UI['Route'+$Row+'Source'].SelectedItem
    if(-not $item -or -not $item.Enabled){Notice '请先在下拉框中选择一个可用的来源。';return}
    $routes=$script:VendorState.Routes
    if($Row -eq 'Desktop'){
        if($item.Kind -eq 'claude-account'){
            $a=$item.Item
            if($a.IsCurrent -and $routes.'claude-desktop'.Source -eq 'official'){Notice 'Claude Desktop 已在使用这个官方账号。';return}
            if(-not $a.Saved){Show-Save $a;return}
            Start-Switch $a;return
        }
        $v=$item.Item
        Confirm-ClaudeExit ('Claude Desktop 使用「'+$v.Name+'」') ('Claude Desktop 会切换到第三方模式，推理通过 '+$v.Anthropic+'。当前官方账号的登录会先保存，之后在接入表选回官方账号即可切回。对话和项目记忆不会复制。') '切换' @{Action='provider-switch';ProviderId=('vendor:'+$v.Id)} '正在备份并配置 Claude Desktop…'
        return
    }
    if($Row -eq 'Code'){
        $request=@{Action='route-claude-code';MaintenanceConfirmed=$true}
        if($item.Kind -eq 'official'){
            if($routes.'claude-code'.Source -in @('official','external')){Notice 'Claude Code 已在使用这个设置。';return}
            $request.Source='official';$title='Claude Code 恢复原设置'
            $body='把 ~/.claude/settings.json 中接入第三方之前的设置原样恢复（官方登录或你原来的配置）。修改前会备份。'
        }else{
            $model=[string]$script:UI.RouteCodeModel.SelectedItem
            if(-not $model){Notice '这个账号还没有设置模型，请先编辑账号并检测可用模型。';return}
            $request.Source=[string]$item.Item.Id;$request.Model=$model;$title='Claude Code 使用「'+$item.Item.Name+'」'
            $body='在 ~/.claude/settings.json 中把 Claude Code 指向 '+$item.Item.Anthropic+'，默认模型 '+$model+'，并按账号设置 Opus / Sonnet / Haiku 档位。接入前的设置会保存，切回时原样恢复。'
        }
        $body+="`n`n这会影响所有 Claude Code 会话，包括 Claude Desktop 里的 Code。新开的会话立即生效；已经打开的会话仍用原设置，不会被关闭。"
        Show-Modal $title $body $false '' '应用' ({Invoke-Job $request {param($v) Operation-Done $v} '正在修改 Claude Code 设置…'}.GetNewClosure())
        return
    }
    if($item.Kind -eq 'codex-account'){
        $a=$item.Item;$onVendor=$routes.codex.Source -notin @('official','external')
        if($a.IsCurrent -and -not $onVendor){Notice 'Codex 已在使用这个官方账号。';return}
        $request=@{Action='codex-route';Source='official'}
        if(-not $a.IsCurrent){$request.Account=[string]$a.Account}
        $body=$(if($onVendor){'恢复 Codex 接入第三方之前的模型提供方和模型。'}else{''})+$(if(-not $a.IsCurrent){'先保存当前 Codex 账号的最新登录，再换成「'+$a.Title+'」。'}else{''})+'修改前会备份。登录快照为实验功能，未经官方支持。'
        Confirm-ClaudeExit ('Codex 使用官方账号 '+$a.Title) $body '切换' $request '正在备份并切换 Codex…' -App 'codex'
        return
    }
    $model=[string]$script:UI.RouteCodexModel.SelectedItem
    if(-not $model){Notice '这个账号还没有设置模型，请先编辑账号并检测可用模型。';return}
    $v=$item.Item
    Confirm-ClaudeExit ('Codex 使用「'+$v.Name+'」') ('在 ~/.codex/config.toml 中加入「'+$v.Name+'（第三方）」模型提供方并设为默认，地址 '+$v.Codex+'，模型 '+$model+'。API Key 写在这个提供方里，ChatGPT 登录保持不变。接入前的提供方和模型会保存，切回时原样恢复。Codex 应用和命令行共用这份配置。') '切换' @{Action='codex-route';Source=[string]$v.Id;Model=$model} '正在备份并配置 Codex…' -App 'codex'
}
function Show-VendorError([string]$Text) {$script:UI.VendorEditorError.Text=$Text;$script:UI.VendorEditorError.Visibility=$(if($Text){'Visible'}else{'Collapsed'})}
function Test-VendorLinked {return [bool]$script:UI.VendorLinked.IsChecked -and $script:UI.VendorLinked.Visibility -eq 'Visible'}
function Sync-VendorKey {
    $linked=Test-VendorLinked;$script:UI.VendorKey.IsEnabled=-not $linked
    $edit=$script:VendorEditing
    $script:UI.VendorKeyHint.Text=$(if($linked){'接入时从 Antigravity Tools 读取它的代理密钥；请保持 Antigravity Tools 的 API 代理运行。'}elseif($edit -and $edit.KeyHint -and -not $edit.Linked){'留空则保留已保存的 Key（'+$edit.KeyHint+'）。'}else{''})
}
function Set-VendorPreset($Preset,[bool]$Fill) {
    if(-not $Preset){return}
    $agy=$Preset.Key -eq 'antigravity'
    $script:UI.VendorLinked.Visibility=$(if($agy){'Visible'}else{'Collapsed'})
    $script:UI.VendorSiteBtn.IsEnabled=[bool]$Preset.Site
    if($Fill){
        $script:UI.VendorAnthropic.Text=[string]$Preset.Anthropic;$script:UI.VendorOpenAI.Text=[string]$Preset.OpenAI;$script:UI.VendorCodex.Text=[string]$Preset.Codex
        $name=$script:UI.VendorName.Text.Trim()
        if(-not $name -or @($script:VendorState.Presets | ForEach-Object {[string]$_.Name}) -contains $name){$script:UI.VendorName.Text=$(if($Preset.Key -eq 'custom'){''}else{[string]$Preset.Name})}
        $script:UI.VendorLinked.IsChecked=$agy -and [bool]$script:VendorState.AntigravityTools.HasKey
    }
    Sync-VendorKey
}
function Open-VendorEditor($Row=$null) {
    if(-not $script:VendorState){Notice '第三方账号还在读取，请稍候。';return}
    $script:VendorEditing=$Row;$script:VendorDetected=$null;$script:VendorFilling=$true
    $presets=@($script:VendorState.Presets);$script:UI.VendorPreset.ItemsSource=$presets
    $want=$(if($Row){[string]$Row.Vendor}else{'deepseek'})
    $script:UI.VendorPreset.SelectedIndex=[Math]::Max(0,[array]::IndexOf(@($presets | ForEach-Object {[string]$_.Key}),$want))
    $script:UI.VendorEditorTitle.Text=$(if($Row){'编辑第三方账号'}else{'添加第三方账号'})
    $script:UI.VendorKey.Password='';$script:UI.VendorModelList.ItemsSource=$null;$script:UI.VendorModelList.Visibility='Collapsed';$script:UI.VendorDetectText.Text='';Show-VendorError ''
    if($Row){
        $script:UI.VendorName.Text=[string]$Row.Name;$script:UI.VendorAnthropic.Text=[string]$Row.Anthropic;$script:UI.VendorOpenAI.Text=[string]$Row.OpenAI
        $script:UI.VendorCodex.Text=[string]$Row.Codex;$script:UI.VendorLinked.IsChecked=[bool]$Row.Linked
        $script:UI.VendorModel.Text=[string]$Row.Model
        $script:UI.VendorOpus.Text=[string]$Row.Tiers.Opus;$script:UI.VendorSonnet.Text=[string]$Row.Tiers.Sonnet;$script:UI.VendorHaiku.Text=[string]$Row.Tiers.Haiku
        Set-VendorPreset $script:UI.VendorPreset.SelectedItem $false
    }else{
        foreach($name in @('VendorName','VendorModel','VendorOpus','VendorSonnet','VendorHaiku')){$script:UI[$name].Text=''}
        Set-VendorPreset $script:UI.VendorPreset.SelectedItem $true
    }
    $script:VendorFilling=$false
    $script:UI.VendorEditor.Visibility='Visible'
}
function Start-VendorDetect {
    $linked=Test-VendorLinked
    $request=@{Action='vendor-models';MaintenanceConfirmed=$true;Anthropic=$script:UI.VendorAnthropic.Text.Trim();OpenAI=$script:UI.VendorOpenAI.Text.Trim();Codex=$script:UI.VendorCodex.Text.Trim();LinkedKey=$linked}
    $key=$script:UI.VendorKey.Password.Trim()
    if($key -and -not $linked){$request.Key=$key}
    elseif($script:VendorEditing -and -not $linked){$request.Id=[string]$script:VendorEditing.Id}
    elseif(-not $linked){$script:UI.VendorDetectText.Text='请先填写 API Key。';return}
    $script:UI.VendorDetectText.Text='正在检测…'
    Invoke-Job $request {param($v)
        $script:VendorDetected=@($v.Models)
        $script:UI.VendorModelList.ItemsSource=$script:VendorDetected;$script:UI.VendorModelList.Visibility='Visible'
        $chosen=$script:UI.VendorModel.Text.Trim()
        if($chosen -and $script:VendorDetected -contains $chosen){$script:UI.VendorModelList.SelectedItem=$chosen}
        $script:UI.VendorDetectText.Text=[string]$v.Message+' 在下拉框中选择即可填入默认模型。'
    } '正在检测可用模型…' {param($problem) $script:UI.VendorDetectText.Text=$problem}
}
function Save-Vendor {
    $preset=$script:UI.VendorPreset.SelectedItem;if(-not $preset){Show-VendorError '请选择厂商。';return}
    $linked=Test-VendorLinked
    $request=@{Action='vendor-save';MaintenanceConfirmed=$true;Vendor=[string]$preset.Key;Name=$script:UI.VendorName.Text.Trim()
        Anthropic=$script:UI.VendorAnthropic.Text.Trim();OpenAI=$script:UI.VendorOpenAI.Text.Trim();Codex=$script:UI.VendorCodex.Text.Trim()
        LinkedKey=$linked;Model=$script:UI.VendorModel.Text.Trim()
        Tiers=@{Opus=$script:UI.VendorOpus.Text.Trim();Sonnet=$script:UI.VendorSonnet.Text.Trim();Haiku=$script:UI.VendorHaiku.Text.Trim()}}
    if($script:VendorEditing){$request.Id=[string]$script:VendorEditing.Id}
    $key=$script:UI.VendorKey.Password.Trim();if($key -and -not $linked){$request.Key=$key}
    if($null -ne $script:VendorDetected){$request.Models=[string[]]$script:VendorDetected}
    Show-VendorError ''
    Invoke-Job $request {param($v) $script:UI.VendorEditor.Visibility='Collapsed';$script:UI.VendorKey.Password='';Operation-Done $v} '正在加密保存第三方账号…' {param($problem) Show-VendorError $problem}
}
function Start-VendorDelete($Row) {
    $id=[string]$Row.Id
    Show-Modal '删除第三方账号' ('「'+$Row.Name+'」和本工具加密保存的 Key 将被删除，删除前备份账号列表。厂商那边的 Key 不受影响。') $false '' '删除' ({Invoke-Job @{Action='vendor-delete';Id=$id;MaintenanceConfirmed=$true} {param($v) Operation-Done $v} '正在删除第三方账号…'}.GetNewClosure())
}
function Display-Cloud($Value) {
    $script:CloudState=$Value
    $script:UI.CloudUrl.Text=[string]$Value.Url;$script:UI.CloudUser.Text=[string]$Value.User;$script:UI.CloudFolder.Text=[string]$Value.Folder;$script:UI.CloudKeep.Text=[string]$Value.Keep
    $script:UI.CloudPassword.Password='';$script:UI.CloudPassphrase.Password=''
    for($i=0;$i -lt $script:UI.CloudMode.Items.Count;$i++){if([string]$script:UI.CloudMode.Items[$i].Tag -eq [string]$Value.Mode){$script:UI.CloudMode.SelectedIndex=$i}}
    $script:UI.CloudAutoCheck.IsChecked=[bool]$Value.AutoCheck
    $script:UI.CloudHint.Text=$(if($Value.HasPassword){'WebDAV 密码已加密保存'}else{'还没有保存 WebDAV 密码'})+' · '+$(if($Value.HasPassphrase){'加密密码已保存（留空则保持不变）'}else{'还没有设置加密密码'})
    $last=Format-Stamp ([string]$Value.LastUpload)
    $script:UI.CloudStatus.Text=$(if($Value.Mode -eq 'local'){'未启用 · 备份只保存在本机'}else{'已启用'+$(if($last){' · 最近上传 '+$last}else{''})})
}
function Load-Cloud([scriptblock]$Then=$null) {Invoke-Job @{Action='cloud-state'} ({param($v) Display-Cloud $v;if($Then){& $Then}}.GetNewClosure()) '正在读取云备份设置…'}
function Save-Cloud {
    $request=@{Action='cloud-save';MaintenanceConfirmed=$true;Url=$script:UI.CloudUrl.Text.Trim();User=$script:UI.CloudUser.Text.Trim();Folder=$script:UI.CloudFolder.Text.Trim()
        Keep=[int]($script:UI.CloudKeep.Text.Trim() -replace '\D','');Mode=[string]$script:UI.CloudMode.SelectedItem.Tag;AutoCheck=[bool]$script:UI.CloudAutoCheck.IsChecked}
    if($script:UI.CloudPassword.Password){$request.Password=$script:UI.CloudPassword.Password}
    if($script:UI.CloudPassphrase.Password){$request.Passphrase=$script:UI.CloudPassphrase.Password}
    Invoke-Job $request {param($v) Notice $v.Message;Load-Cloud} '正在加密保存云备份设置…'
}
function Show-CloudFiles {
    Invoke-Job @{Action='cloud-list';MaintenanceConfirmed=$true} {param($v)
        $rows=@($v.Files)
        foreach($r in $rows){
            $r | Add-Member NoteProperty Title $(if($r.Kind -eq 'settings'){'设置与第三方账号'}else{'会话备份 · '+$r.BackupId}) -Force
            $r | Add-Member NoteProperty Detail ([string]$r.Time+' · '+$(if($r.ThisMachine){'本机上传'}else{'其他电脑上传（'+$r.Machine+'）'})+' · '+('{0:0.0} MB' -f ([double]$r.Bytes/1MB))) -Force
        }
        $script:UI.CloudFiles.ItemsSource=$rows
        Notice $v.Message
    } '正在读取云端备份列表…'
}
function Start-CloudUpload([string]$BackupId='',[scriptblock]$Then=$null) {
    $request=@{Action='cloud-upload';MaintenanceConfirmed=$true};if($BackupId){$request.BackupId=$BackupId}
    Invoke-Job $request ({param($v) Notice $v.Message;if($Then){& $Then}else{Load-Cloud}}.GetNewClosure()) '正在加密并上传到 WebDAV…'
}
# After a local backup: upload it when WebDAV is chosen; "WebDAV only" then moves the local copy to the Recycle Bin.
function Start-CloudAfterBackup($Value) {
    $mode=[string](Prop $script:CloudState 'Mode' 'local');$id=[string](Prop $Value 'Id' '')
    if($mode -eq 'local' -or -not $id){return $false}
    Start-CloudUpload $id ({
        if($mode -eq 'webdav'){Invoke-Job @{Action='backup-delete';Id=$id} {param($v) Notice '备份已加密上传到 WebDAV，本机副本已移到回收站。';Refresh-State} '正在移除本机副本…'}
        else{Load-Cloud {Refresh-State}}
    }.GetNewClosure())
    return $true
}
function Start-CloudCheck {
    if($script:CloudChecked -or $SmokeTest){return};$script:CloudChecked=$true
    Load-Cloud {
        if($script:CloudState.Mode -eq 'local' -or -not $script:CloudState.AutoCheck){return}
        Invoke-Job @{Action='cloud-check';MaintenanceConfirmed=$true} {param($v)
            $count=@($v.Newer).Count
            if($count){Notice ('云端有 '+$count+' 份其他电脑上传的新备份，可在「备份中心 → 查看云端备份」中恢复到本机。')}
        } '正在检查云端备份…'
    }
}
function Display-Tools($Mcp,$Skills) {
    if($Mcp){
        $script:McpState=$Mcp
        foreach($r in @($Mcp.Servers)){$r | Add-Member NoteProperty Detail ([string]$r.Kind+' · '+[string]$r.Summary) -Force;$r | Add-Member NoteProperty BadgeVisibility 'Collapsed' -Force;$r | Add-Member NoteProperty Badge '' -Force}
        $script:UI.McpList.ItemsSource=@($Mcp.Servers);$script:UI.McpEmpty.Visibility=$(if(@($Mcp.Servers).Count){'Collapsed'}else{'Visible'})
        $script:UI.McpPaths.Text='Claude Code：'+$Mcp.ClaudePath+'   ·   Codex：'+$Mcp.CodexPath
    }
    if($Skills){
        $script:SkillsState=$Skills
        foreach($r in @($Skills.Skills)){$r | Add-Member NoteProperty Detail $(if($r.Description){[string]$r.Description}else{'（没有描述）'}) -Force;$r | Add-Member NoteProperty Badge '两边内容不同' -Force;$r | Add-Member NoteProperty BadgeVisibility $(if($r.Differs){'Visible'}else{'Collapsed'}) -Force}
        $script:UI.SkillList.ItemsSource=@($Skills.Skills)
        $script:UI.SkillPaths.Text=@($Skills.Skills).Count.ToString()+' 个技能   ·   Claude Code：'+$Skills.ClaudePath+'   ·   Codex：'+$Skills.CodexPath
    }
    $script:ToolsLoaded=$true
    Set-BulkLabels
}
function Load-Tools {Invoke-Job @{Action='mcp-state'} {param($m) Display-Tools $m $null;Invoke-Job @{Action='skills-state'} {param($k) Display-Tools $null $k} '正在读取技能…'} '正在读取 MCP 服务…'}
function Show-Save($Account=$null,[string]$Display='', [string]$Problem='') {
    if(-not $Account){$Account=@($script:State.Accounts | Where-Object {$_.SwitchKind -eq 'official' -and $_.IsCurrent}) | Select-Object -First 1}
    if(-not $Account){Show-LoginGuide;return}
    if(-not $Display){$Display=$Account.Name}
    $body="收录对象："+$Account.Name+"`n`n下面是显示名称，可直接使用邮箱，不作为文件夹名。执行前按时间备份并核对账号身份；仅检查本地登录缓存，不联网确认登录有效性。桌面登录快照为实验功能，未经官方支持，无法保证账号使用风险。"
    if($Problem){$body="上次未完成："+$Problem+"`n`n"+$body}
    $failure=({param($errorText) Show-Save $Account $Display $errorText}.GetNewClosure())
    Confirm-ClaudeExit '收录桌面版登录' $body '收录登录' @{Action='save';ExpectedAccount=$Account.Uuid} '正在备份并核对桌面版登录…' $true $Display $failure
}
function Show-LoginGuide($Account=$null) {
    $target=$(if($Account){'：'+$Account.Name}else{''})
    $body="Claude Desktop 没有向第三方开放登录授权，所以新账号在 Claude 官方登录页中登录：`n`n1. 工具先保存当前账号的登录快照，再把本地登录文件移入时间戳备份，让 Claude 回到登录页。不会在服务器上注销，已保存的账号随时可切回。`n2. Claude 重新打开后，登录要添加的账号"+$target+"。`n3. 回到本工具，点「收录当前登录」完成添加。`n`n换号请用本工具，不要用 Claude 菜单里的「退出登录」：服务器端注销可能使已保存的登录失效。桌面登录快照为实验功能，未经官方支持。"
    Confirm-ClaudeExit ('添加官方账号'+$target) $body '打开登录页' @{Action='login-new'} '正在保存当前账号并准备登录页…'
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
function Display-Stats($Value) {
    $t=$Value.Totals
    $script:UI.UsTotal.Text=$t.TotalText;$script:UI.UsRequests.Text=('{0:N0} 次请求' -f [long]$t.Requests)
    $script:UI.UsIO.Text=$t.InputText+' / '+$t.OutputText
    $script:UI.UsCache.Text=$t.CacheReadText+' / '+$t.CacheWriteText;$script:UI.UsHit.Text='缓存命中 '+$t.CacheHitPercent+'%'
    $script:UI.UsCost.Text=$t.CostText
    foreach($row in @($Value.Accounts)){
        $row | Add-Member NoteProperty ShareText ([string]$row.SharePercent+'%') -Force
        $row | Add-Member NoteProperty BarWidth ([string][Math]::Max(2,[double]$row.SharePercent*1.1)) -Force
        $row | Add-Member NoteProperty Detail ('输入 '+$row.InputText+'  ·  输出 '+$row.OutputText+'  ·  缓存读 '+$row.CacheReadText+'  ·  缓存写 '+$row.CacheWriteText+'  ·  命中 '+$row.CacheHitPercent+'%') -Force
        $row | Add-Member NoteProperty QuotaLabel $(if($row.QuotaText){'最近限额提示：'+$row.QuotaText}else{''}) -Force
        $row | Add-Member NoteProperty QuotaVisibility $(if($row.QuotaText){'Visible'}else{'Collapsed'}) -Force
    }
    foreach($row in @($Value.Models)){$row | Add-Member NoteProperty Detail (('{0:N0} 次请求' -f [long]$row.Requests)+'  ·  占 '+$row.SharePercent+'%') -Force}
    # Timeline: oldest on the left, bars rise from the axis; only every few buckets get a label.
    $trend=@($Value.Trend);[array]::Reverse($trend)
    $peakRow=$trend | Sort-Object {[double]$_.Total} -Descending | Select-Object -First 1
    $peak=[Math]::Max(1,[double]$peakRow.Total)
    $hourly=[bool](Prop $Value 'Hourly' ([int](Prop $Value 'Hours' 0) -gt 0))
    $every=[Math]::Max(1,[Math]::Ceiling($trend.Count/$(if($hourly){8}else{10})))
    for($i=0;$i -lt $trend.Count;$i++){
        $row=$trend[$i];$total=[double]$row.Total
        $row | Add-Member NoteProperty BarHeight ([string]$(if($total -gt 0){[Math]::Max(2,$total/$peak*136)}else{0})) -Force
        $row | Add-Member NoteProperty BarBrush $(if($row -eq $peakRow -and $total -gt 0){'#E5A23B'}else{'#60A5FA'}) -Force
        $row | Add-Member NoteProperty AxisLabel $(if(($trend.Count-1-$i) % $every -eq 0){[string]$row.Label}else{''}) -Force
        $row | Add-Member NoteProperty Tip ($row.Date+$(if($hourly){':00'}else{''})+' · '+$row.TotalText+' token · '+$row.Requests+' 次请求') -Force
    }
    $script:UI.UsageTrendPeak.Text=$(if($peakRow -and [double]$peakRow.Total -gt 0){'峰值 '+$peakRow.Label+' · '+$peakRow.TotalText+'（橙色）'}else{'这段时间没有记录'})
    $script:UI.UsageAccounts.ItemsSource=@($Value.Accounts);$script:UI.UsageModels.ItemsSource=@($Value.Models);$script:UI.UsageTrend.ItemsSource=$trend
    $script:UI.UsCostNote.Text=$(if($Value.PriceSource){$Value.PriceSource}else{'未找到价格表'})
    $script:UI.UsageNote.Text=$Value.Note+' 费用按官方 API 价格折算（区分 5 分钟与 1 小时缓存写入、快速模式和长提示档位），是等价金额；Pro/Max 订阅不按 token 计费。'
    $script:UI.UsageTrendTitle.Text=[string](Prop $Value 'TrendTitle' '每日趋势')
    $script:UI.UsageStamp.Text='统计于 '+(Get-Date -Format 'HH:mm:ss')
    $script:StatsLoaded=$true
}
function Load-Stats([bool]$RefreshPrices=$false) {
    $tag='7';if($script:UI.UsageRange.SelectedItem){$tag=[string]$script:UI.UsageRange.SelectedItem.Tag}
    $request=@{Action='usage-stats';RefreshPrices=$RefreshPrices;App='claude'}
    if($script:UI.UsageApp.SelectedItem){$request.App=[string]$script:UI.UsageApp.SelectedItem.Tag}
    if($tag -like 'h*'){$request.Hours=[int]$tag.Substring(1)}else{$request.Days=[int]$tag}
    Invoke-Job $request {param($v) Display-Stats $v} $(if($RefreshPrices){'正在读取官方价格并统计…'}else{'正在统计 token 用量…'})
}
function Open-Pricing {
    $path=Join-Path $script:Data 'pricing.json'
    if(-not(Test-Path -LiteralPath $path)){
        if($ReadOnly -or $SmokeTest){Notice '当前为只读预览，未创建价格文件。';return}
        Write-Json $path ([ordered]@{Note='单位：美元 / 每百万 token。请按你的账单或官方价格页填写，保存后点「重新统计」。不填写的模型不计入费用。';Models=[ordered]@{};Example=[ordered]@{'模型名，例如 claude-opus-5-5'=[ordered]@{Input=0;Output=0;CacheRead=0;CacheWrite=0}}})
    }
    Start-Process notepad.exe -ArgumentList ('"'+$path+'"')
}
function Read-SelectedUsage {
    $s=$script:UI.UsageSession.SelectedItem
    if(-not $s){Notice '请先选择一条对话。';return}
    Invoke-Job @{Action='usage';Account=$s.Account;SessionId=$s.Id;PoolKey=$s.PoolKey;RootKey=$s.RootKey} {param($v) Display-Usage $v} '正在读取历史请求用量…'
}

foreach($name in @('NavAccounts','NavSessions','NavUsage','NavTools','NavBackups','NavSettings')){$script:UI[$name].Add_Click({param($sender,$event) Navigate ([string]$sender.Tag)})}
$script:UI.TitleBar.Add_MouseLeftButtonDown({param($sender,$event) if($event.ClickCount -eq 2){$script:Window.WindowState=$(if($script:Window.WindowState -eq 'Maximized'){'Normal'}else{'Maximized'})}else{$script:Window.DragMove()}})
$script:UI.MinBtn.Add_Click({$script:Window.WindowState='Minimized'})
$script:UI.MaxBtn.Add_Click({$script:Window.WindowState=$(if($script:Window.WindowState -eq 'Maximized'){'Normal'}else{'Maximized'})})
$script:UI.CloseBtn.Add_Click({$script:Window.Close()})
# The X button hides to the tray; only the tray's exit item (or turning the option off) really quits.
$script:Window.Add_Closing({param($sender,$e)
    if($script:TrayIcon -and $script:CloseToTray -and -not $script:Exiting){
        $e.Cancel=$true;$script:Window.Hide()
        if(-not $script:TrayHintShown){$script:TrayHintShown=$true;$script:TrayIcon.ShowBalloonTip(4000,'Claude Switch Studio','已最小化到托盘。右键托盘图标可切换账号或退出。',[Windows.Forms.ToolTipIcon]::Info)}
        return
    }
    if($script:Busy){$e.Cancel=$true;$script:Exiting=$false;Notice '正在备份或处理数据，请等待操作完成。'}
})
$script:Window.Add_Closed({
    if($script:TrayIcon){$script:TrayIcon.Visible=$false;$script:TrayIcon.Dispose();$script:TrayIcon=$null}
    $script:Window.Dispatcher.InvokeShutdown()
})
$script:UI.CloseToTrayCheck.Add_Click({$script:CloseToTray=[bool]$script:UI.CloseToTrayCheck.IsChecked;Save-Preferences})
$script:UI.AutoSyncCheck.Add_Click({$script:AutoSync=[bool]$script:UI.AutoSyncCheck.IsChecked;Save-Preferences})
$script:UI.SyncBtn.Add_Click({Confirm-ClaudeExit '同步所有账号的对话列表' '把每个官方账号的对话补到其他账号的列表里，并统一各账号的对话标题、工作目录和侧栏文件夹（以最后修改的为准），之后在任一账号登录看到的都一样。不删除条目；在某账号删除过的对话不会被加回该账号；改写前先备份原文件。原文本来就在本机共享，不会复制。' '立即同步' @{Action='sync-conversations'} '正在同步对话列表…'})
$script:UI.AutostartCheck.Add_Click({Set-Autostart ([bool]$script:UI.AutostartCheck.IsChecked);Sync-StartupOptions})
$script:UI.ThemeBtn.Add_Click({Set-Theme (-not $script:Dark)})
$script:UI.SettingsThemeBtn.Add_Click({Set-Theme (-not $script:Dark)})
$script:UI.RefreshBtn.Add_Click({switch($script:Page){'tools'{Load-Tools}'usage'{Load-Stats}default{Refresh-State}}})
$script:UI.SaveBtn.Add_Click({Invoke-Job @{Action='state'} {param($v) Update-State $v;Show-Save} '正在只读检查当前桌面版身份…'})
$script:UI.AddAccountBtn.Add_Click({Show-LoginGuide})
$script:UI.OnboardingBtn.Add_Click({Show-Onboarding})
$script:UI.GuideSkipBtn.Add_Click({Complete-Onboarding $true})
$script:UI.GuideBackBtn.Add_Click({Show-Onboarding ($script:OnboardingStep-1)})
$script:UI.GuideNextBtn.Add_Click({if($script:OnboardingStep -eq 2){Complete-Onboarding}else{Show-Onboarding ($script:OnboardingStep+1)}})
$script:UI.ModalCancel.Add_Click({$cancel=$script:ModalCancelCallback;$script:ModalCancelCallback=$null;$script:UI.Modal.Visibility='Collapsed';if($cancel){try{& $cancel}catch{Notice $_.Exception.Message}}})
$script:UI.ModalOk.Add_Click({$value=$script:UI.ModalInput.Text.Trim();$cb=$script:ModalCallback;$script:UI.Modal.Visibility='Collapsed';try{& $cb $value}catch{Notice $_.Exception.Message}})
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
                'providers' {Notice '请在上方接入表的 Claude Desktop 一行选择第三方账号。'}
                'switch' {Start-Switch $a}
            }
        }
    }
})
$script:UI.CodexSaveBtn.Add_Click({Start-CodexSave})
$script:UI.CodexAddBtn.Add_Click({Start-CodexAdd})
foreach($row in @('Desktop','Code','Codex')){$script:UI['Route'+$row+'Btn'].Add_Click({param($sender,$e) Start-Route ([string]$sender.Name).Substring(5,([string]$sender.Name).Length-8)})}
foreach($row in @('Code','Codex')){$script:UI['Route'+$row+'Source'].Add_SelectionChanged({param($sender,$e) Update-RouteModel ([string]$sender.Name).Substring(5,([string]$sender.Name).Length-11)})}
$script:UI.VendorAddBtn.Add_Click({Open-VendorEditor})
$script:UI.CloudSaveBtn.Add_Click({Save-Cloud})
$script:UI.CloudListBtn.Add_Click({Show-CloudFiles})
$script:UI.CloudUploadBtn.Add_Click({Start-CloudUpload})
$script:UI.CloudFiles.AddHandler([Windows.Controls.Button]::ClickEvent,[Windows.RoutedEventHandler]{param($sender,$e)
    $b=$e.OriginalSource;if($b -isnot [Windows.Controls.Button] -or $b.Tag -ne 'cloud-restore'){return};$row=$b.CommandParameter;$name=[string]$row.Name
    $body=$(if($row.Kind -eq 'settings'){'下载并解密这份设置备份，把本机还没有的第三方账号加进来（Key 用本机的 Windows 数据保护重新加密）。本机已有的账号和设置不会被覆盖。'}else{'下载并解密这份会话备份，放回本机的备份中心。本机已有同一份备份时跳过。不会改动正在使用的会话和登录。'})
    Show-Modal ('恢复 · '+$row.Title) $body $false '' '恢复到本机' ({Invoke-Job @{Action='cloud-restore';Name=$name;MaintenanceConfirmed=$true} {param($v) Operation-Done $v} '正在下载并解密…'}.GetNewClosure())
})
$script:UI.VendorImportBtn.Add_Click({Show-Modal '从 CC Switch 导入' '读取 CC Switch 中 Claude 和 Codex 的提供商（地址、Key 和模型），加密保存为本工具的第三方账号；同一个 Key 的 Claude 与 Codex 配置合并成一个账号。只读取，不修改 CC Switch；已导入过的会跳过。' $false '' '导入' {Invoke-Job @{Action='vendor-import';MaintenanceConfirmed=$true} {param($v) Operation-Done $v} '正在从 CC Switch 导入…'}})
$script:UI.VendorGroups.AddHandler([Windows.Controls.Button]::ClickEvent,[Windows.RoutedEventHandler]{param($sender,$e)
    $b=$e.OriginalSource;if($b -isnot [Windows.Controls.Button]){return}
    if($b.Tag -eq 'vendor-edit'){Open-VendorEditor $b.CommandParameter}elseif($b.Tag -eq 'vendor-delete'){Start-VendorDelete $b.CommandParameter}
})
$script:UI.VendorPreset.Add_SelectionChanged({if(-not $script:VendorFilling){Set-VendorPreset $script:UI.VendorPreset.SelectedItem $true}})
$script:UI.VendorLinked.Add_Click({Sync-VendorKey})
$script:UI.VendorModelList.Add_SelectionChanged({if($script:UI.VendorModelList.SelectedItem){$script:UI.VendorModel.Text=[string]$script:UI.VendorModelList.SelectedItem}})
$script:UI.VendorDetectBtn.Add_Click({Start-VendorDetect})
$script:UI.VendorSaveBtn.Add_Click({Save-Vendor})
$script:UI.VendorCancelBtn.Add_Click({$script:UI.VendorEditor.Visibility='Collapsed';$script:UI.VendorKey.Password=''})
$script:UI.VendorSiteBtn.Add_Click({$site=[string]$script:UI.VendorPreset.SelectedItem.Site;if($site){Start-Process $site}})
foreach($name in @('McpAllClaude','McpAllCodex','SkillAllClaude','SkillAllCodex')){
    $script:UI[$name].Add_Click({param($sender,$e) Start-Bulk $(if($sender.Name -like 'Mcp*'){'mcp'}else{'skill'}) ([string]$sender.Tag)})
}
$script:OAuthTimer=New-Object Windows.Threading.DispatcherTimer;$script:OAuthTimer.Interval=[TimeSpan]::FromSeconds(2)
$script:OAuthTimer.Add_Tick({Poll-OAuth});$script:OAuthTimer.Start()
$script:UI.CodexCards.AddHandler([Windows.Controls.Button]::ClickEvent,[Windows.RoutedEventHandler]{param($sender,$e)
    $b=$e.OriginalSource;if($b -isnot [Windows.Controls.Button] -or $b.Tag -ne 'codex-action'){return};$a=$b.CommandParameter
    if($a.IsCurrent){Start-CodexSave ([string]$a.Account)}else{Start-CodexSwitch $a}
})
# One handler for both lists: a checkbox's Tag says which list and which app it toggles.
$toolToggle=[Windows.RoutedEventHandler]{param($sender,$e)
    $box=$e.OriginalSource;if($box -isnot [Windows.Controls.CheckBox]){return}
    $kind,$app=([string]$box.Tag).Split('-');$row=$box.DataContext;$on=[bool]$box.IsChecked
    $where=$(if($app -eq 'claude'){'Claude Code'}else{'Codex'})
    $request=@{Action=$(if($kind -eq 'mcp'){'mcp-toggle'}else{'skill-toggle'});Name=[string]$row.Name;App=$app;Enabled=$on;MaintenanceConfirmed=$true}
    $redisplay={Display-Tools $script:McpState $script:SkillsState}
    $wasBusy=$script:Busy
    Invoke-Job $request {param($v) Notice $v.Message;Load-Tools} ('正在'+$(if($on){'开启'}else{'关闭'})+' '+$where+' 中的 '+$row.Name+'…') $redisplay
    if($wasBusy -or -not $script:Busy){& $redisplay}  # Refused (busy or read-only): put the checkbox back.
}
$script:UI.McpList.AddHandler([Windows.Controls.Primitives.ButtonBase]::ClickEvent,$toolToggle)
$script:UI.SkillList.AddHandler([Windows.Controls.Primitives.ButtonBase]::ClickEvent,$toolToggle)
$script:UI.SessionAccount.Add_SelectionChanged({Filter-Sessions})
$script:UI.SearchBox.Add_TextChanged({Filter-Sessions})
$script:UI.SessionGrid.Add_SelectionChanged({$script:UI.SelectionLabel.Text='已选择 '+$script:UI.SessionGrid.SelectedItems.Count+' 条 · 所有账号使用同一份本地原文'})
$script:UI.CopyBtn.Add_Click({Invoke-Job @{Action='workspace-plan'} {param($v)
    Show-Modal '共享池方案 · 只读预览' ($v.Message+"`n`n当前索引："+$v.OriginalIndexes+" 条`n独立对话："+$v.UniqueConversations+" 条`n账号专属状态目录："+@($v.AccountLocalState).Count+" 个`n`n没有移动数据，也没有创建目录联接。") $false '' '知道了' {}
} '正在核对共享目录与账号状态…'})
$script:UI.UsageFromSessionBtn.Add_Click({$s=$script:UI.SessionGrid.SelectedItem;if(-not $s){Notice '请先选择一条对话。';return};foreach($item in $script:State.Pool){if($item.PoolKey -eq $s.PoolKey){$script:UI.UsageSession.SelectedItem=$item;break}};Navigate 'usage';Read-SelectedUsage})
$script:UI.ReadUsageBtn.Add_Click({Read-SelectedUsage})
$script:UI.UsageRange.SelectedIndex=2;$script:UI.UsageApp.SelectedIndex=0
$script:UI.UsageApp.Add_SelectionChanged({if($script:UI.UsagePage.Visibility -eq 'Visible' -and -not $SmokeTest){Load-Stats}})
$script:UI.UsageRange.Add_SelectionChanged({if($script:UI.UsagePage.Visibility -eq 'Visible' -and -not $SmokeTest){Load-Stats}})
$script:UI.UsageRefreshBtn.Add_Click({Load-Stats})
$script:UI.PricingBtn.Add_Click({Open-Pricing})
$script:UI.UpdatePricesBtn.Add_Click({Load-Stats $true})
$script:UI.BackupBtn.Add_Click({
    $suggested=[string](Prop $script:State 'NextBackupName' '新增备份 1')
    Confirm-ClaudeExit '创建完整会话备份' '备份包含索引、原文、记忆、文件历史及关联状态，逐文件校验；不包含登录凭据。下面可以给这份备份起名。' '开始备份' @{Action='backup'} '正在备份并校验本地会话…' $true $suggested
})
$script:UI.BackupCards.AddHandler([Windows.Controls.Button]::ClickEvent,[Windows.RoutedEventHandler]{param($sender,$event)
    $b=$event.OriginalSource;if($b -isnot [Windows.Controls.Button]){return};$backup=$b.CommandParameter
    if($b.Tag -eq 'open'){Start-Process explorer.exe -ArgumentList ('"'+$backup.Path+'"')}
    elseif($b.Tag -eq 'cloud-upload'){$id=[string]$backup.Id;Show-Modal '上传到云端' ('「'+$backup.Name+'」（'+$backup.Size+'）会先在本机加密，再上传到 WebDAV；同时上传一份设置备份。本机副本保留。') $false '' '加密上传' ({Start-CloudUpload $id}.GetNewClosure())}
    elseif($b.Tag -eq 'rename'){$id=$backup.Id;Show-Modal '重命名备份' '只修改本工具显示的名称，备份内容不变。' $true ([string]$backup.Name) '保存名称' ({param($name) Invoke-Job @{Action='backup-rename';Id=$id;Name=$name} {param($v)Operation-Done $v} '正在更新备份名称…'}.GetNewClosure())}
    elseif($b.Tag -eq 'delete'){
        $id=$backup.Id
        $body='「'+$backup.Name+'」（'+$backup.Time+'，'+$backup.Size+'）将移到 Windows 回收站，需要时可从回收站还原。清空回收站后无法恢复。'
        if($backup.CanUndo){$body+="`n`n这份备份记录着一次迁移的新增索引。删除后无法再用「撤回新增」撤回那次迁移。"}
        Show-Modal '删除备份' $body $false '' '移到回收站' ({Invoke-Job @{Action='backup-delete';Id=$id} {param($v)Operation-Done $v} '正在把备份移到回收站…'}.GetNewClosure())
    }
    elseif($b.Tag -eq 'undo'){$id=$backup.Id;Show-Modal '撤回这次新增索引' '请先退出 Claude。仅移回本次迁移创建且尚未被修改的索引，保留源账号、目标已有会话和全部原文。若新增会话已被使用或修改，将停止撤回。' $false '' '我已退出，撤回新增' ({Invoke-Job @{Action='undo';Id=$id;MaintenanceConfirmed=$true} {param($v)Operation-Done $v} '正在校验并撤回新增索引…'}.GetNewClosure())}
})
function Open-Original {Start-Process (Join-Path $PSScriptRoot 'docs\USER_GUIDE.html')}
$script:UI.OriginalBtn.Add_Click({Open-Original});$script:UI.SettingsOriginalBtn.Add_Click({Open-Original})
$script:UI.StarBtn.Add_Click({Start-Process 'https://github.com/dantengs/ClaudeSwitchStudio'})
$script:UI.GitHubBtn.Add_Click({Start-Process 'https://github.com/dantengs/ClaudeSwitchStudio'})
$script:UI.InstallFolderBtn.Add_Click({Start-Process explorer.exe -ArgumentList ('"'+$PSScriptRoot+'"')})
$script:UI.CacheDocsBtn.Add_Click({Start-Process 'https://platform.claude.com/docs/en/build-with-claude/prompt-caching'})
# Each message fades out after 2 seconds, so an old result never lingers; hovering keeps it for reading.
$script:NoticeTimer=New-Object Windows.Threading.DispatcherTimer;$script:NoticeTimer.Interval=[TimeSpan]::FromSeconds(2)
$script:NoticeTimer.Add_Tick({
    if($script:UI.Notice.IsMouseOver){return}
    $script:NoticeTimer.Stop()
    $fade=New-Object Windows.Media.Animation.DoubleAnimation(1.0,0.0,(New-Object Windows.Duration([TimeSpan]::FromMilliseconds(400))))
    $fade.Add_Completed({if(-not $script:NoticeTimer.IsEnabled){$script:UI.Notice.Visibility='Collapsed'}})
    $script:UI.Notice.BeginAnimation([Windows.UIElement]::OpacityProperty,$fade)
})
$timer=New-Object Windows.Threading.DispatcherTimer;$timer.Interval=[TimeSpan]::FromMilliseconds(250)
$timer.Add_Tick({
    if($script:ShowEvent -and $script:ShowEvent.WaitOne(0)){Show-Main}  # A second launch asked for this window.
    if($script:ExitEvent -and $script:ExitEvent.WaitOne(0)){Exit-Studio}  # ClaudeSwitchStudio.exe --exit
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
    finally{$job.Process.Dispose();foreach($file in @($job.Request,$job.Response)){try{[IO.File]::Delete($file)}catch{}}}
})
$timer.Start()
$preferences=Read-Json (Join-Path $script:Data 'preferences.json')
$script:OnboardingVersion=[int](Prop $preferences 'OnboardingVersion' 0);$script:OnboardingSkipped=[bool](Prop $preferences 'OnboardingSkipped' $false)
$script:CloseToTray=[bool](Prop $preferences 'CloseToTray' $true);$script:AutoSync=[bool](Prop $preferences 'AutoSync' $true)
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
    foreach($navName in @('NavAccounts','NavSessions','NavUsage','NavTools','NavBackups','OriginalBtn','NavSettings')){
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
    Navigate 'usage';Display-Stats (Read-NativePreview @{Action='usage-stats';Hours=24});Save-View 'usage-24h';$script:UI.UsageApp.SelectedIndex=1;Display-Stats (Read-NativePreview @{Action='usage-stats';App='codex';Days=30});Save-View 'usage-codex';$script:UI.UsageApp.SelectedIndex=0;Display-Stats (Read-NativePreview @{Action='usage-stats';Days=0});if(@($script:UI.UsageAccounts.Items).Count -eq 0){throw 'Usage statistics did not render'};$selected=$script:UI.UsageSession.SelectedItem;if($selected){Display-Usage (Read-NativePreview @{Action='usage';PoolKey=$selected.PoolKey})};Save-View 'usage-light'
    Navigate 'accounts';Display-Codex (Read-NativePreview @{Action='codex-state'});Display-Vendors (Read-NativePreview @{Action='vendor-state'})
    if(@($script:UI.RouteCodeSource.ItemsSource).Count -lt 1 -or -not $script:UI.RouteCodeSource.SelectedItem){throw 'Access table did not render'}
    Save-View 'accounts-routes'
    $script:UI.AccountsPage.ScrollToEnd();Save-View 'accounts-vendors';$script:UI.AccountsPage.ScrollToHome()
    Open-VendorEditor
    if($script:UI.VendorEditor.Visibility -ne 'Visible' -or $script:UI.VendorAnthropic.Text -notlike 'https://*'){throw 'Vendor editor did not open with a preset'}
    $script:UI.VendorPreset.SelectedIndex=@($script:UI.VendorPreset.ItemsSource).Count-1
    if($script:UI.VendorAnthropic.Text -or $script:UI.VendorName.Text){throw 'Custom preset did not clear the addresses'}
    $script:UI.VendorPreset.SelectedIndex=0;$script:UI.VendorKey.Password='sk-preview-only';Save-View 'vendor-editor'
    $script:UI.VendorSaveBtn.RaiseEvent((New-Object Windows.RoutedEventArgs([Windows.Controls.Button]::ClickEvent,$script:UI.VendorSaveBtn)))
    if($script:Busy -or $script:UI.NoticeText.Text -notmatch '只读预览'){throw 'Vendor save read-only guard failed'}
    $script:UI.VendorCancelBtn.RaiseEvent((New-Object Windows.RoutedEventArgs([Windows.Controls.Button]::ClickEvent,$script:UI.VendorCancelBtn)))
    if($script:UI.VendorKey.Password){throw 'Vendor key was kept after cancel'}
    Start-Route 'Code'
    if($script:UI.NoticeText.Text -notmatch '已在使用|只读预览' -and $script:UI.Modal.Visibility -ne 'Visible'){throw 'Claude Code route gave no feedback'}
    $script:UI.Modal.Visibility='Collapsed'
    Navigate 'usage';$script:UI.UsageApp.SelectedIndex=2;Display-Stats (Read-NativePreview @{Action='usage-stats';App='antigravity';Days=30});Save-View 'usage-agy';$script:UI.UsageApp.SelectedIndex=0
    Navigate 'tools';Display-Tools (Read-NativePreview @{Action='mcp-state'}) (Read-NativePreview @{Action='skills-state'});Save-View 'tools-light'
    # A toggle in read-only mode must be refused and the checkbox put back.
    function Find-CheckBox($Parent){
        if($Parent -is [Windows.Controls.CheckBox]){return $Parent}
        for($i=0;$i -lt [Windows.Media.VisualTreeHelper]::GetChildrenCount($Parent);$i++){$found=Find-CheckBox ([Windows.Media.VisualTreeHelper]::GetChild($Parent,$i));if($found){return $found}}
        return $null
    }
    $script:Window.UpdateLayout();$box=Find-CheckBox $script:UI.SkillList
    if($box){
        $before=[bool]$box.IsChecked;$box.IsChecked=-not $before
        $box.RaiseEvent((New-Object Windows.RoutedEventArgs([Windows.Controls.Primitives.ButtonBase]::ClickEvent,$box)))
        $script:Window.UpdateLayout();$again=Find-CheckBox $script:UI.SkillList
        if($script:Busy -or $script:UI.NoticeText.Text -notmatch '只读预览' -or [bool]$again.IsChecked -ne $before){throw 'Skill toggle read-only guard failed'}
    }
    Navigate 'backups';Display-Cloud (Read-NativePreview @{Action='cloud-state'});Save-View 'backups-light'
    $script:UI.CloudSaveBtn.RaiseEvent((New-Object Windows.RoutedEventArgs([Windows.Controls.Button]::ClickEvent,$script:UI.CloudSaveBtn)))
    if($script:Busy -or $script:UI.NoticeText.Text -notmatch '只读预览'){throw 'Cloud settings read-only guard failed'}
    Navigate 'settings';Save-View 'settings-light'
    Set-ReadingSize 17 $false;Save-View 'settings-large';Set-ReadingSize 15 $false
    Set-Theme $true $false;Navigate 'accounts';Save-View 'accounts-dark'
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
    $script:UI.AddAccountBtn.RaiseEvent((New-Object Windows.RoutedEventArgs([Windows.Controls.Button]::ClickEvent,$script:UI.AddAccountBtn)))
    if($script:UI.Modal.Visibility -ne 'Visible' -or [string]$script:UI.ModalOk.Content -notmatch '登录页'){throw 'Add account wizard missing'}
    if($script:UI.ModalInput.Visibility -ne 'Collapsed'){throw 'Confirmation without a name shows an input box'}
    $script:UI.ModalCancel.RaiseEvent((New-Object Windows.RoutedEventArgs([Windows.Controls.Button]::ClickEvent,$script:UI.ModalCancel)))
    $script:UI.BackupBtn.RaiseEvent((New-Object Windows.RoutedEventArgs([Windows.Controls.Button]::ClickEvent,$script:UI.BackupBtn)))
    if($script:UI.Modal.Visibility -ne 'Visible' -or $script:UI.ModalInput.Visibility -ne 'Visible' -or $script:UI.ModalInput.Text -notmatch '^新增备份 \d+$'){throw 'Backup naming dialog missing'}
    Save-View 'backup-create'
    Save-View 'account-add'
    $script:UI.ModalOk.RaiseEvent((New-Object Windows.RoutedEventArgs([Windows.Controls.Button]::ClickEvent,$script:UI.ModalOk)))
    if($script:Busy -or $script:UI.NoticeText.Text -notmatch '只读预览'){throw 'Add account read-only guard failed'}
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
    # Build the real tray menu and quit through its own exit item, as a user would.
    Initialize-Tray;Build-TrayMenu
    $trayItems=@($script:TrayIcon.ContextMenuStrip.Items | ForEach-Object {[string]$_.Text})
    foreach($expected in @('打开主界面','切换账号','打开 Claude','开机自启','退出 Claude Switch Studio')){if($trayItems -notcontains $expected){throw ('Tray menu item missing: '+$expected+' in '+($trayItems -join ' | '))}}
    $exitItem=@($script:TrayIcon.ContextMenuStrip.Items | Where-Object {[string]$_.Text -eq '退出 Claude Switch Studio'})[0]
    $exitItem.PerformClick()
    if(-not $script:Exiting -or $script:Window.IsVisible -or $script:TrayIcon){throw 'Tray exit item did not quit'}
    $timer.Stop()
    [pscustomobject]@{Accounts=@($script:State.Accounts).Count;Sessions=@($script:State.Sessions).Count;Pool=$script:State.PoolCount;Vendors=@($script:VendorState.Accounts).Count;ReadOnlyUiGuard=$true;AccountWizardRoutes=$true;AddAccountWizard=$true;EmailDisplayName=$true;SidebarVectorsAligned=$true;OnboardingNavigation=$true;PreviewPreferencesUnchanged=$true;Snapshots=$SnapshotDir;Passed=$true}|ConvertTo-Json
}else{
    $script:ShowEvent=New-Object Threading.EventWaitHandle($false,[Threading.EventResetMode]::AutoReset,'ClaudeSwitchStudio.Show')
    $script:ExitEvent=New-Object Threading.EventWaitHandle($false,[Threading.EventResetMode]::AutoReset,'ClaudeSwitchStudio.Exit')
    Initialize-Tray;Sync-StartupOptions
    $script:Window.Add_ContentRendered({if(-not $script:State -and -not $script:Busy){Refresh-State};if(-not $ReadOnly -and -not $script:OnboardingOpened -and $script:OnboardingVersion -lt 1){Show-Onboarding}})
    try{
        # A dispatcher loop instead of ShowDialog lets the window hide while the tray keeps the app alive.
        if($Tray){Refresh-State}else{$script:Window.Show()}
        [Windows.Threading.Dispatcher]::Run()
    }catch{[void][IO.Directory]::CreateDirectory($script:Data);[IO.File]::WriteAllText((Join-Path $script:Data 'startup-error.log'),($_ | Out-String));throw}
    finally{$timer.Stop();if($script:TrayIcon){$script:TrayIcon.Dispose()};if($script:ShowEvent){$script:ShowEvent.Dispose()}}
}
