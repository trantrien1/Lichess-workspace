param(
    [ValidateSet('bootstrap', 'start', 'stop', 'status', 'check', 'browser', 'logs')]
    [string]$Action = 'status',
    [switch]$WithOutage,
    [switch]$HumanPlayers,
    [switch]$Full
)

$ErrorActionPreference = 'Stop'
$workspaceRoot = Split-Path $PSScriptRoot -Parent
Push-Location $workspaceRoot

function Invoke-Checked {
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Program failed with exit code $LASTEXITCODE" }
}

function Compose {
    Invoke-Checked docker (@('compose', '-f', 'infra/compose.yml', '--profile', 'prediction') + $args)
}

function Bootstrap {
    $lock = Get-Content upstream-lock.json -Raw | ConvertFrom-Json
    $previousLfs = $env:GIT_LFS_SKIP_SMUDGE
    $env:GIT_LFS_SKIP_SMUDGE = '1'
    try {
        foreach ($name in @('lila-docker', 'lila')) {
            $repo = $lock.repositories.$name
            if (!(Test-Path -LiteralPath ($repo.path + '/.git'))) {
                if (Test-Path -LiteralPath $repo.path) { throw "Existing non-Git path: $($repo.path)" }
                Invoke-Checked git @('-c', 'core.autocrlf=false', 'clone', '--no-checkout', '--depth', '1', $repo.url, $repo.path)
                Invoke-Checked git @('-C', $repo.path, 'config', 'core.autocrlf', 'input')
                Invoke-Checked git @('-C', $repo.path, 'fetch', '--depth', '1', 'origin', $repo.commit)
                Invoke-Checked git @('-C', $repo.path, 'checkout', '-b', 'codex/win-probability', $repo.commit)
            }
            $actual = & git -C $repo.path rev-parse HEAD
            if ($LASTEXITCODE -ne 0 -or $actual.Trim() -ne $repo.commit) {
                throw "Unexpected revision in $($repo.path); keep local work and resolve before setup."
            }
        }
        $lila = $lock.repositories.lila.path
        $patch = Join-Path $workspaceRoot 'patches/lila-win-probability.patch'
        if (!(Test-Path -LiteralPath $patch)) { throw 'Missing feature patch.' }
        & git -C $lila apply --reverse --check $patch 2>$null
        if ($LASTEXITCODE -ne 0) {
            Invoke-Checked git @('-C', $lila, 'apply', '--check', $patch)
            Invoke-Checked git @('-C', $lila, 'apply', $patch)
        }
    } finally { $env:GIT_LFS_SKIP_SMUDGE = $previousLfs }
    Compose build lila ui prediction
    Compose run --rm --no-deps -v "${workspaceRoot}/scripts:/bootstrap:ro" ui node /bootstrap/prepare-lila.mjs
    Compose run --rm --no-deps ui bash ./ui/build --debug
    Compose up -d --wait mongodb mongodb_secondary redis prediction lila_ws
    Compose exec -T mongodb mongosh --quiet lichess /lila/bin/mongodb/indexes.js
    Compose run --rm --no-deps lila bash ./lila.sh stage
}

function Start-Stack {
    $stage = 'infra/lila-docker/repos/lila/target/universal/stage/lib'
    $manifest = 'infra/lila-docker/repos/lila/public/compiled/manifest.json'
    if (!(Test-Path -LiteralPath $stage) -or !(Test-Path -LiteralPath $manifest)) {
        throw 'Missing compiled application or UI. Run .\scripts\dev.ps1 bootstrap first.'
    }
    Compose up -d --wait mongodb mongodb_secondary redis prediction lila_ws lila caddy
    $deadline = [DateTime]::UtcNow.AddSeconds(120)
    do {
        try {
            $response = Invoke-WebRequest 'http://127.0.0.1:8080/' -UseBasicParsing -TimeoutSec 3
            if ($response.StatusCode -eq 200) {
                Write-Host 'Lichess is ready at http://localhost:8080.'
                return
            }
        } catch { }
        Start-Sleep -Seconds 1
    } while ([DateTime]::UtcNow -lt $deadline)
    throw 'Lichess did not become ready within 120 seconds. Run .\scripts\dev.ps1 logs.'
}

try {
    switch ($Action) {
        'bootstrap' { Bootstrap }
        'start' { Start-Stack }
        'stop' { Compose stop }
        'status' { Compose ps -a }
        'logs' { Compose logs --tail 80 lila prediction lila_ws caddy }
        'check' {
            Invoke-Checked uv @('run', '--project', 'services/prediction', '--group', 'ml', '--frozen', 'python', '-m', 'pytest', 'services/prediction/tests', '-q')
            Invoke-Checked uv @('run', '--project', 'services/prediction', '--group', 'ml', '--frozen', 'ruff', 'check', 'services/prediction')
            if ($Full) {
                Compose run --rm --no-deps lila bash ./lila.sh ';scalafmtCheckAll;test'
                Compose run --rm --no-deps ui node ui/test --test-concurrency=2
            } else {
                Compose run --rm --no-deps ui node ui/test round
            }
            Compose run --rm --no-deps ui pnpm exec oxlint --type-aware ui/round/src/ctrl.ts ui/round/src/interfaces.ts ui/round/src/view/replay.ts ui/round/src/view/winProbability.ts ui/round/src/winProbability.ts ui/round/tests/winProbability.test.ts ui/round/tests/winProbabilityView.test.ts
            Compose run --rm --no-deps ui pnpm exec stylelint ui/round/css/_round.scss ui/round/css/_win-probability.scss
            Compose run --rm --no-deps ui pnpm exec oxfmt --check ui/round/src/ctrl.ts ui/round/src/interfaces.ts ui/round/src/view/replay.ts ui/round/src/view/winProbability.ts ui/round/src/winProbability.ts ui/round/tests/winProbability.test.ts ui/round/tests/winProbabilityView.test.ts ui/round/css/_round.scss ui/round/css/_win-probability.scss
            Compose run --rm --no-deps -v "${workspaceRoot}/scripts:/checks:ro" prediction python /checks/check_prediction.py
        }
        'browser' {
            $previousBrowserCache = $env:PLAYWRIGHT_BROWSERS_PATH
            $previousOutage = $env:TEST_OUTAGE
            $previousHumanPlayers = $env:HUMAN_PLAYERS
            try {
                $env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $workspaceRoot '.cache/playwright'
                $env:TEST_OUTAGE = if ($WithOutage) { '1' } else { '0' }
                $env:HUMAN_PLAYERS = if ($HumanPlayers) { '1' } else { '0' }
                $playwrightCli = Get-Item 'infra/lila-docker/repos/lila/node_modules/.pnpm/playwright-core@*/node_modules/playwright-core/cli.js' | Select-Object -First 1 -ExpandProperty FullName
                if (!$playwrightCli) { throw 'Run bootstrap to install Lichess dependencies first.' }
                Invoke-Checked node @($playwrightCli, 'install', 'chromium', '--only-shell')
                Invoke-Checked node @('scripts/browser-smoke.mjs')
            } finally {
                $env:PLAYWRIGHT_BROWSERS_PATH = $previousBrowserCache
                $env:TEST_OUTAGE = $previousOutage
                $env:HUMAN_PLAYERS = $previousHumanPlayers
            }
        }
    }
} finally { Pop-Location }
