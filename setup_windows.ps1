# PowerShell скрипт для настройки Opencode на Windows
$ErrorActionPreference = "Stop"

Write-Host "=== Opencode Setup Script for Windows ===" -ForegroundColor Cyan

$CONFIG_DIR = "$env:APPDATA\opencode"
$AGENTS_DIR = "$env:USERPROFILE\.agents"
$STASH_DIR = "$env:USERPROFILE\projects\stash\opencode.ai"
$SCRIPT_ROOT = $PSScriptRoot

# 1. Create directories
Write-Host "[1/6] Creating directories..." -ForegroundColor Yellow
New-Item -ItemType Directory -Path $CONFIG_DIR -Force | Out-Null
New-Item -ItemType Directory -Path $AGENTS_DIR -Force | Out-Null
New-Item -ItemType Directory -Path $STASH_DIR -Force | Out-Null

# 2. Install npm package
Write-Host "[2/6] Installing @opencode-ai/cli globally..." -ForegroundColor Yellow
Set-Location $CONFIG_DIR
npm install -g @opencode-ai/cli --silent 2>$null
npm install --save-dev @opencode-ai/plugin@1.15.4 --silent 2>$null
Write-Host "      Done (may require admin rights)" -ForegroundColor Gray

# 3. Create API key file placeholder
Write-Host "[3/6] Creating API key placeholder..." -ForegroundColor Yellow
$API_KEY_FILE = "$STASH_DIR\api-key.txt"
"your-routerai-api-key-here" | Set-Content $API_KEY_FILE -Encoding UTF8
Write-Host "      Created: $API_KEY_FILE" -ForegroundColor Gray

# 4. Generate opencode.jsonc
Write-Host "[4/6] Generating opencode.jsonc..." -ForegroundColor Yellow
$keyPath = (Resolve-Path $API_KEY_FILE).Path.Replace('/', '\')
$json = @"
`"{`"\$schema`": `"https://opencode.ai/config.json`", `"provider`": `"{`"routerai`": `"{`"npm`": `"@ai-sdk/openai-compatible`", `"name`": `"RouterAI`", `"options`": `"{`"baseURL`": `"https://routerai.ru/api/v1`", `"apiKey`": `"`"{file:$keyPath}`"`"`"`"`"`"`"}, `"models`": `"{`"qwen/qwen3.6-plus`": `"{`"name`": `"Qwen3.6Plus`"`"}"`"`"`"`"}`"`"`"`"`"}
"@
# Simplified version
$json = @{
    '`$schema' = 'https://opencode.ai/config.json'
    provider = @{
        routerai = @{
            npm = '@ai-sdk/openai-compatible'
            name = 'RouterAI'
            options = @{
                baseURL = 'https://routerai.ru/api/v1'
                apiKey = "{file:$keyPath}"
            }
            models = @{
                'openai/gpt-4o' = @{ name = 'OpenAI GPT-4o [expensive]' }
                'openai/gpt-4o-mini' = @{ name = 'OpenAI GPT-4o mini [cheap]' }
                'deepseek/deepseek-chat' = @{ name = 'DeepSeek Chat' }
                'anthropic/claude-opus-4.7' = @{ name = 'Claude Opus 4.7' }
                'qwen/qwen3.6-plus' = @{ name = 'Qwen 3.6 Plus [main]' }
                'deepseek/deepseek-v4-pro' = @{ name = 'DeepSeek V4 Pro' }
                'minimax/minimax-m2.7' = @{ name = 'MiniMax M2.7' }
                'z-ai/glm-5' = @{ name = 'GLM-5 [architect]' }
                'moonshotai/kimi-k2.5' = @{ name = 'Kimi K2.5' }
                'openai/gpt-5.2-codex' = @{ name = 'GPT-5.2-Codex' }
                'qwen/qwen3-coder-next' = @{ name = 'Qwen Coder Next' }
                'qwen/qwen3.5-122b-a10b' = @{ name = 'Qwen 3.5 122B' }
            }
        }
    }
    model = 'opencode/deepseek-v4-flash-free'
    small_model = 'opencode/gpt-5-nano'
} | ConvertTo-Json -Depth 10
$json | Set-Content "$CONFIG_DIR\opencode.jsonc" -Encoding UTF8

# 5. Create AGENTS.md
Write-Host "[5/6] Creating AGENTS.md..." -ForegroundColor Yellow
@"
# Глобальная память

Этот файл — глобальная память агента. Читается в начале каждой сессии.

Сохранённые факты:
- Маппинг путей WSL: /mnt/c/Users/<user>/... → c:\Users\<user>\...
- ssh_relay: Python 3.12+, paramiko, версия 0.5.0
- bundle.py: https://github.com/dilukhin/bundle.git

Правила:
- Отвечай на русском, без воды, структурированно
- Не сканируй node_modules, .git, кэши, логи
- Никогда не exposed секреты, API-ключи, токены
- Сохраняй прогресс рано при работе с документами
"@ | Set-Content "$CONFIG_DIR\AGENTS.md" -Encoding UTF8

# 6. Create package.json
Write-Host "[6/6] Creating package.json..." -ForegroundColor Yellow
@{
    dependencies = @{
        '@opencode-ai/plugin' = '1.15.4'
    }
} | ConvertTo-Json | Set-Content "$CONFIG_DIR\package.json" -Encoding UTF8
npm install --silent 2>$null

Set-Location $SCRIPT_ROOT

Write-Host ""
Write-Host "=== Setup Complete ===" -ForegroundColor Green
Write-Host "Config directory: $CONFIG_DIR" -ForegroundColor Gray
Write-Host "Edit this file and insert your RouterAI API key:" -ForegroundColor Yellow
Write-Host "  $API_KEY_FILE" -ForegroundColor White
Write-Host "Then run: opencode" -ForegroundColor Cyan
