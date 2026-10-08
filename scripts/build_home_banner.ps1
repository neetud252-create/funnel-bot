param(
    [string]$OutputPath = (Join-Path $PSScriptRoot '../assets/home_banner_loop.mp4')
)

# Offline asset build only. The deployed bot sends the finished MP4.
$ErrorActionPreference = 'Stop'
$sourcePath = Join-Path $PSScriptRoot '../assets/home_banner.png'

# Six seconds at 24 fps. Periodic lights meet at the loop boundary; the
# original image and lettering remain stationary and fully legible.
$pulse = '(0.12+0.12*sin(2*PI*T/3))*exp(-pow((X-487)/72,2)-pow((Y-161)/82,2))'
$baseGlow = '(0.06+0.06*sin(2*PI*T/6))*exp(-pow((X-501)/115,2)-pow((Y-281)/18,2))'
$orbitOne = '0.9*exp(-pow((X-(498+110*cos(2*PI*T/6)))/7,2)-pow((Y-(280+24*sin(2*PI*T/6)))/3,2))'
$orbitTwo = '0.6*exp(-pow((X-(498+110*cos(2*PI*T/6+PI)))/5,2)-pow((Y-(280+24*sin(2*PI*T/6+PI)))/3,2))'
$network = '(0.17+0.17*sin(2*PI*T/3-1))*exp(-pow((X-495)/83,2)-pow((Y-44)/46,2))'
$lights = "($pulse+$baseGlow+$orbitOne+$orbitTwo+$network)"
$filter = "[0:v]scale=1280:720:flags=lanczos,setsar=1,format=gbrp[base];" +
    "nullsrc=s=640x360:r=24:d=6,format=gbrp," +
    "geq=r='55*$lights':g='255*$lights':b='172*$lights'," +
    "scale=1280:720:flags=bilinear[lights];" +
    '[base][lights]blend=all_mode=screen:all_opacity=0.85,format=yuv420p[out]'

& ffmpeg -hide_banner -loglevel warning -y -loop 1 -framerate 24 -i $sourcePath `
    -filter_complex $filter -map '[out]' -t 6 -an -c:v libx264 -preset medium `
    -crf 19 -pix_fmt yuv420p -movflags +faststart $OutputPath
if ($LASTEXITCODE -ne 0) { throw 'Banner animation encoding failed.' }
Get-Item -LiteralPath $OutputPath | Select-Object FullName,Length
