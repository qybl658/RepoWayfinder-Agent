param([int]$TargetPort, [int]$OwnedPidValue, [ValidateSet('ready','closed')][string]$ExpectedState)
$ErrorActionPreference = 'Stop'
$results = @()
$clock = [System.Diagnostics.Stopwatch]::StartNew()
$slept = 0
if ($ExpectedState -eq 'ready') {
    $tcpReady = $false
    $deadline = (Get-Date).AddSeconds(15)
    while ((Get-Date) -lt $deadline) {
        if ((Test-NetConnection -ComputerName 127.0.0.1 -Port $TargetPort -WarningAction SilentlyContinue).TcpTestSucceeded) { $tcpReady = $true; break }
        Start-Sleep -Milliseconds 300
        $slept += 300
    }
    $observed = $tcpReady
} else {
    $observed = $false
    foreach ($i in 1..20) {
        if (-not (Test-NetConnection -ComputerName 127.0.0.1 -Port $TargetPort -WarningAction SilentlyContinue).TcpTestSucceeded) { $observed = $true; break }
        Start-Sleep -Milliseconds 250
        $slept += 250
    }
}
$clock.Stop()
$results += [ordered]@{method='original_test_net_connection_pattern'; correct=$observed; seconds=$clock.Elapsed.TotalSeconds; explicit_sleep_milliseconds=$slept}

$clock.Restart()
$handler = [System.Net.Http.HttpClientHandler]::new()
$handler.UseProxy = $false
$client = [System.Net.Http.HttpClient]::new($handler)
$client.Timeout = [TimeSpan]::FromSeconds(1)
try {
    $body = $client.GetStringAsync("http://127.0.0.1:$TargetPort/").GetAwaiter().GetResult()
    $observed = $ExpectedState -eq 'ready' -and $body -eq 'PROBE-OK'
} catch {
    $observed = $ExpectedState -eq 'closed'
} finally {
    $client.Dispose()
    $handler.Dispose()
}
$clock.Stop()
$results += [ordered]@{method='bounded_http_known_body'; correct=$observed; seconds=$clock.Elapsed.TotalSeconds; explicit_sleep_milliseconds=0}

$clock.Restart()
$socket = [System.Net.Sockets.TcpClient]::new()
try {
    $pending = $socket.BeginConnect('127.0.0.1', $TargetPort, $null, $null)
    $connected = $pending.AsyncWaitHandle.WaitOne(500)
    if ($connected) {
        try { $socket.EndConnect($pending) } catch { $connected = $false }
    }
    $connected = $connected -and $socket.Connected
    $observed = if ($ExpectedState -eq 'ready') { $connected } else { -not $connected }
} finally { $socket.Dispose() }
$clock.Stop()
$results += [ordered]@{method='bounded_tcp_socket'; correct=$observed; seconds=$clock.Elapsed.TotalSeconds; explicit_sleep_milliseconds=0}

$clock.Restart()
$alive = $null -ne (Get-Process -Id $OwnedPidValue -ErrorAction SilentlyContinue)
$observed = if ($ExpectedState -eq 'ready') { $alive } else { -not $alive }
$clock.Stop()
$results += [ordered]@{method='owned_process_state'; correct=$observed; seconds=$clock.Elapsed.TotalSeconds; explicit_sleep_milliseconds=0}

[ordered]@{state=$ExpectedState; methods=$results} | ConvertTo-Json -Depth 5 -Compress
