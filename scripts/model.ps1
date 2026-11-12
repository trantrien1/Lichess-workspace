param(
    [ValidateSet('collect', 'train')]
    [string]$Action = 'train'
)

$ErrorActionPreference = 'Stop'
$workspaceRoot = Split-Path $PSScriptRoot -Parent
Push-Location (Join-Path $workspaceRoot 'services/prediction')

function Run-Python {
    & uv run --group ml --frozen python @args
    if ($LASTEXITCODE -ne 0) { throw "Model command failed with exit code $LASTEXITCODE" }
}

try {
    if ($Action -eq 'collect') {
        foreach ($split in @(
            @{ Month = '2026-08'; Games = 8000; MaxMiB = 128 },
            @{ Month = '2026-09'; Games = 2000; MaxMiB = 64 },
            @{ Month = '2026-10'; Games = 2000; MaxMiB = 64 }
        )) {
            $output = "../../data/human/$($split.Month)-balanced.jsonl"
            if (Test-Path -LiteralPath $output) {
                if (!(Test-Path -LiteralPath ($output -replace '\.jsonl$', '.manifest.json'))) {
                    throw "Dataset has no manifest: $output. Preserve it and investigate before retrying."
                }
                Write-Host "Using existing dataset: $output (checksum verified during train)."
                continue
            }
            Run-Python -m training.collect --month $split.Month --games $split.Games --max-mib $split.MaxMiB --output $output
        }
    } else {
        # Reproduction output is separate: this command never replaces the shipped model.
        Run-Python -m training.train `
            --train ../../data/human/2026-08-balanced.jsonl `
            --calibration ../../data/human/2026-09-balanced.jsonl `
            --test ../../data/human/2026-10-balanced.jsonl `
            --model ../../artifacts/human-reproduction/human-v1.json `
            --report ../../artifacts/human-reproduction
    }
} finally { Pop-Location }
