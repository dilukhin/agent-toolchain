#!/bin/bash
# Bash скрипт для настройки Opencode на Linux/Debian

set -e

echo "=== Opencode Setup Script for Linux/Debian ==="

CONFIG_DIR="$HOME/.config/opencode"
AGENTS_DIR="$HOME/.agents"
STASH_DIR="$HOME/projects/stash/opencode.ai"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# 1. Create directories
echo "[1/6] Creating directories..."
mkdir -p "$CONFIG_DIR" "$AGENTS_DIR" "$STASH_DIR"

# 2. Install npm package
echo "[2/6] Installing @opencode-ai/cli globally..."
cd "$CONFIG_DIR"
npm install -g @opencode-ai/cli 2>/dev/null || sudo npm install -g @opencode-ai/cli
npm install --save-dev @opencode-ai/plugin@1.15.4

# 3. Create API key file placeholder  
echo "[3/6] Creating API key placeholder..."
API_KEY_FILE="$STASH_DIR/api-key.txt"
echo "your-routerai-api-key-here" > "$API_KEY_FILE"
echo "      Created: $API_KEY_FILE"

# 4. Generate opencode.jsonc
echo "[4/6] Generating opencode.jsonc..."
KEY_PATH=$(realpath "$API_KEY_FILE")
cat > "$CONFIG_DIR/opencode.jsonc" << EOF
{
  "\$schema": "https://opencode.ai/config.json",
  "provider": {
    "routerai": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "RouterAI",
      "options": {
        "baseURL": "https://routerai.ru/api/v1",
        "apiKey": "{file:$KEY_PATH}"
      },
      "models": {
        "openai/gpt-4o": { "name": "OpenAI GPT-4o [expensive]" },
        "openai/gpt-4o-mini": { "name": "OpenAI GPT-4o mini [cheap]" },
        "deepseek/deepseek-chat": { "name": "DeepSeek Chat" },
        "anthropic/claude-opus-4.7": { "name": "Claude Opus 4.7" },
        "qwen/qwen3.6-plus": { "name": "Qwen 3.6 Plus [main]" },
        "deepseek/deepseek-v4-pro": { "name": "DeepSeek V4 Pro" },
        "minimax/minimax-m2.7": { "name": "MiniMax M2.7" },
        "z-ai/glm-5": { "name": "GLM-5 [architect]" },
        "moonshotai/kimi-k2.5": { "name": "Kimi K2.5" },
        "openai/gpt-5.2-codex": { "name": "GPT-5.2-Codex" },
        "qwen/qwen3-coder-next": { "name": "Qwen Coder Next" },
        "qwen/qwen3.5-122b-a10b": { "name": "Qwen 3.5 122B" }
      }
    }
  },
  "model": "opencode/deepseek-v4-flash-free",
  "small_model": "opencode/gpt-5-nano"
}
EOF

# 5. Create AGENTS.md
echo "[5/6] Creating AGENTS.md..."
cat > "$CONFIG_DIR/AGENTS.md" << 'EOF'
# Глобальная память

Этот файл — глобальная память агента. Читается в начале каждой сессии.

Сохранённые факты:
- ssh_relay: Python 3.12+, paramiko, версия 0.5.0
- bundle.py: https://github.com/dilukhin/bundle.git

Правила:
- Отвечай на русском, без воды, структурированно
- Не сканируй node_modules, .git, кэши, логи
- Никогда не exposed секреты, API-ключи, токены
- Сохраняй прогресс рано при работе с документами
EOF

# 6. Create package.json
echo "[6/6] Creating package.json..."
cat > "$CONFIG_DIR/package.json" << 'EOF'
{
  "dependencies": {
    "@opencode-ai/plugin": "1.15.4"
  }
}
EOF
npm install

cd "$SCRIPT_DIR"

echo ""
echo "=== Setup Complete ==="
echo "Config directory: $CONFIG_DIR"
echo "Edit this file and insert your RouterAI API key:"
echo "  $API_KEY_FILE"
echo "Then run: opencode"
