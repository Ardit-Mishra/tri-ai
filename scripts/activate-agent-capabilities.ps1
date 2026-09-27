[CmdletBinding()]
param(
    [string]$CapabilityRoot = (Join-Path $HOME ".tri-ai\capabilities\sources"),
    [string[]]$Targets = @(
        (Join-Path $HOME ".codex\skills"),
        (Join-Path $HOME ".claude\skills"),
        (Join-Path (Resolve-Path "$PSScriptRoot\..") ".agents\skills")
    )
)

$ErrorActionPreference = "Stop"

$skillSources = [ordered]@{
    "diagram-design" = "diagram-design\skills\diagram-design"
    "design-motion-principles" = "design-motion-principles\skills\design-motion-principles"
    "archify" = "archify\archify"
    "taste-skill" = "taste-skill\skills\taste-skill"
}

foreach ($name in @(
    "biopython", "bioservices", "bulk-rnaseq", "clinical-decision-support",
    "deepchem", "literature-review", "medchem", "molecular-dynamics", "rdkit", "torchdrug"
)) {
    $skillSources["science-$name"] = "scientific-agent-skills\skills\$name"
}

foreach ($name in @(
    "analyzing-sbom-for-supply-chain-vulnerabilities", "auditing-aws-s3-bucket-permissions",
    "auditing-cloud-with-cis-benchmarks", "auditing-gcp-iam-permissions",
    "auditing-terraform-infrastructure-for-security", "implementing-threat-modeling-with-mitre-attack",
    "performing-serverless-function-security-review", "performing-threat-modeling-with-owasp-threat-dragon"
)) {
    $skillSources["security-$name"] = "anthropic-cybersecurity-skills\skills\$name"
}

foreach ($name in @(
    "decompose-spec", "implement-spec", "orchestrate-build", "reconcile-build", "refresh-repo-docs",
    "review-pr", "self-review", "synthesize-spec"
)) {
    $skillSources["engineering-$name"] = "agent-skills\skills\$name"
}

foreach ($name in @(
    "ai-seo", "analytics", "competitor-profiling", "content-strategy", "copywriting",
    "customer-research", "launch", "marketing-plan", "product-marketing", "seo-audit"
)) {
    $skillSources["marketing-$name"] = "marketingskills\skills\$name"
}

function Add-CapabilityLink {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$TargetRoot
    )

    if (-not (Test-Path -LiteralPath $Source -PathType Container)) {
        throw "Required capability source is missing: $Source"
    }
    New-Item -ItemType Directory -Force -Path $TargetRoot | Out-Null
    $destination = Join-Path $TargetRoot $Name
    if (Test-Path -LiteralPath $destination) {
        Write-Host "Kept existing skill: $destination" -ForegroundColor DarkYellow
        return
    }
    New-Item -ItemType Junction -Path $destination -Target $Source | Out-Null
    Write-Host "Activated $Name -> $Source" -ForegroundColor Green
}

foreach ($target in $Targets) {
    foreach ($entry in $skillSources.GetEnumerator()) {
        Add-CapabilityLink -Name $entry.Key -Source (Join-Path $CapabilityRoot $entry.Value) -TargetRoot $target
    }
}

Write-Host "Activated $($skillSources.Count) governed capability skills in $($Targets.Count) agent locations." -ForegroundColor Cyan
