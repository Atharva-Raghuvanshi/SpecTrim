param(
    [string]$ChromePath = "C:\Program Files\Google\Chrome\Application\chrome.exe",
    [string]$OutDir = "C:\Users\Admin\OneDrive\Desktop\Query Optimizer\docs\screenshots"
)

$targets = @(
    @{ Name = "api-docs-swagger.png"; Url = "http://localhost:8001/docs" },
    @{ Name = "ui-overview.png"; Url = "http://localhost:8001/ui/#overview" },
    @{ Name = "ui-specs.png"; Url = "http://localhost:8001/ui/#specs" },
    @{ Name = "ui-connect.png"; Url = "http://localhost:8001/ui/#connect" },
    @{ Name = "grafana-dashboard.png"; Url = "http://localhost:3000" },
    @{ Name = "grafana-qo-dashboard.png"; Url = "http://localhost:3000/d/qo-main/query-optimizer?orgId=1" },
    @{ Name = "prometheus-graph.png"; Url = "http://localhost:9090/graph?g0.expr=qo_http_requests_total&g0.tab=0" }
)

foreach ($t in $targets) {
    $dest = Join-Path $OutDir $t.Name
    Write-Host "Capturing $($t.Name) from $($t.Url)..."
    $args = @(
        "--headless=new",
        "--no-sandbox",
        "--disable-gpu",
        "--window-size=1440,900",
        "--hide-scrollbars",
        "--screenshot=$dest",
        $t.Url
    )
    Start-Process -FilePath $ChromePath -ArgumentList $args -Wait
    if (Test-Path $dest) {
        $size = (Get-Item $dest).Length
        Write-Host "Saved: $dest ($size bytes)"
    } else {
        Write-Warning "Failed to capture $dest"
    }
}
