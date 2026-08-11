param(
    [string]$ConfigDir = "$env:USERPROFILE\.config\opencode",
    [string]$StashDir = "$env:USERPROFILE\projects\stash\opencode.ai",
    [switch]$SkipPackageInstall
)

$ErrorActionPreference = "Stop"
$ApiKeyFile = Join-Path $StashDir "api-key.txt"
$ConfigFile = Join-Path $ConfigDir "opencode.jsonc"
$AgentsFile = Join-Path $ConfigDir "AGENTS.md"

Write-Host "=== OpenCode setup for Windows ===" -ForegroundColor Cyan
New-Item -ItemType Directory -Path $ConfigDir, $StashDir -Force | Out-Null

if (-not $SkipPackageInstall) {
    Write-Host "Installing OpenCode CLI and @opencode-ai/plugin@1.15.4..." -ForegroundColor Yellow
    & npm install -g @opencode-ai/cli
    if ($LASTEXITCODE -ne 0) { throw "OpenCode CLI installation failed." }
    & npm install --prefix $ConfigDir --save-exact "@opencode-ai/plugin@1.15.4"
    if ($LASTEXITCODE -ne 0) { throw "OpenCode plugin installation failed." }
}

if (Test-Path -LiteralPath $ApiKeyFile) {
    Write-Host "Preserved existing API key file: $ApiKeyFile" -ForegroundColor Gray
} else {
    "your-routerai-api-key-here" | Set-Content -LiteralPath $ApiKeyFile -Encoding UTF8
    Write-Host "Created API key placeholder: $ApiKeyFile" -ForegroundColor Gray
}

if (Test-Path -LiteralPath $ConfigFile) {
    Write-Host "Preserved existing config: $ConfigFile" -ForegroundColor Gray
} else {
    $keyPath = [System.IO.Path]::GetFullPath($ApiKeyFile).Replace('\', '\\')
    $config = @"
{
  "`$schema": "https://opencode.ai/config.json",
  "provider": {
    "routerai": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "RouterAI",
      "options": {
        "baseURL": "https://routerai.ru/api/v1",
        "apiKey": "{file:$keyPath}"
      },
      "models": {
        "openai/gpt-4o": { "name": "OpenAI GPT-4o" },
        "openai/gpt-4o-mini": { "name": "OpenAI GPT-4o mini" },
        "deepseek/deepseek-chat": { "name": "DeepSeek Chat" },
        "anthropic/claude-opus-4.7": { "name": "Claude Opus 4.7" },
        "qwen/qwen3.6-plus": { "name": "Qwen 3.6 Plus" },
        "deepseek/deepseek-v4-pro": { "name": "DeepSeek V4 Pro" },
        "minimax/minimax-m2.7": { "name": "MiniMax M2.7" },
        "z-ai/glm-5": { "name": "GLM-5" },
        "moonshotai/kimi-k2.5": { "name": "Kimi K2.5" },
        "z-ai/glm-5-turbo": { "name": "GLM-5 Turbo" },
        "openai/gpt-5.2-codex": { "name": "GPT-5.2-Codex" },
        "qwen/qwen3-coder-next": { "name": "Qwen 3 Coder Next" },
        "qwen/qwen3.5-122b-a10b": { "name": "Qwen 3.5 122B A10B" }
      }
    }
  },
  "model": "opencode/deepseek-v4-flash-free",
  "small_model": "opencode/gpt-5-nano"
}
"@
    $config | Set-Content -LiteralPath $ConfigFile -Encoding UTF8
    Write-Host "Created config: $ConfigFile" -ForegroundColor Gray
}

if (Test-Path -LiteralPath $AgentsFile) {
    Write-Host "Preserved existing instructions: $AgentsFile" -ForegroundColor Gray
} else {
    @"
# Global OpenCode instructions

- Never expose secrets, tokens, passwords, or API keys.
- Do not scan .git, node_modules, build output, caches, or logs without a reason.
- Prefer concise, structured answers.
"@ | Set-Content -LiteralPath $AgentsFile -Encoding UTF8
    Write-Host "Created instructions: $AgentsFile" -ForegroundColor Gray
}

Write-Host "Setup complete. Add the RouterAI key to $ApiKeyFile and restart OpenCode." -ForegroundColor Green
Write-Host "BMAD is optional and project-local; run .\install_bmad_windows.ps1 <project-path>." -ForegroundColor Cyan
