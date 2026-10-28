# Render the editable SVG with Windows' vector drawing APIs; no external image service.
param([string]$AssetDirectory=(Join-Path (Split-Path $PSScriptRoot -Parent) 'assets'))
$ErrorActionPreference='Stop'
Add-Type -AssemblyName PresentationCore,WindowsBase
[xml]$svg=[IO.File]::ReadAllText((Join-Path $AssetDirectory 'app.svg'))
$visual=New-Object Windows.Media.DrawingVisual
$context=$visual.RenderOpen()
try{
    foreach($node in $svg.DocumentElement.ChildNodes){
        if($node.LocalName -eq 'title'){continue}
        $fill=$null;$pen=$null
        if($node.GetAttribute('fill') -and $node.GetAttribute('fill') -ne 'none'){$fill=[Windows.Media.BrushConverter]::new().ConvertFromString($node.GetAttribute('fill'))}
        if($node.GetAttribute('stroke')){
            $pen=New-Object Windows.Media.Pen([Windows.Media.BrushConverter]::new().ConvertFromString($node.GetAttribute('stroke'))),([double]$node.GetAttribute('stroke-width'))
            $pen.StartLineCap='Round';$pen.EndLineCap='Round';$pen.LineJoin='Round'
        }
        if($node.LocalName -eq 'rect'){
            $rectangle=New-Object Windows.Rect([double]$node.GetAttribute('x')),([double]$node.GetAttribute('y')),([double]$node.GetAttribute('width')),([double]$node.GetAttribute('height'))
            $context.DrawRoundedRectangle($fill,$pen,$rectangle,[double]$node.GetAttribute('rx'),[double]$node.GetAttribute('rx'))
        }elseif($node.LocalName -eq 'path'){
            $context.DrawGeometry($fill,$pen,[Windows.Media.Geometry]::Parse($node.GetAttribute('d')))
        }else{throw ('Unsupported icon element: '+$node.LocalName)}
    }
}finally{$context.Close()}
$bitmap=New-Object Windows.Media.Imaging.RenderTargetBitmap(512,512,96,96,[Windows.Media.PixelFormats]::Pbgra32)
$bitmap.Render($visual)
$encoder=New-Object Windows.Media.Imaging.PngBitmapEncoder
$encoder.Frames.Add([Windows.Media.Imaging.BitmapFrame]::Create($bitmap))
$stream=[IO.File]::Create((Join-Path $AssetDirectory 'app.png'))
try{$encoder.Save($stream)}finally{$stream.Dispose()}
