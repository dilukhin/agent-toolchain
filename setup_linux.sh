#!/usr/bin/env bash
set -euo pipefail

CONFIG_DIR="${OPENCODE_CONFIG_DIR:-$HOME/.config/opencode}"
STASH_DIR="${OPENCODE_STASH_DIR:-$HOME/projects/stash/opencode.ai}"
API_KEY_FILE="$STASH_DIR/api-key.txt"
CONFIG_FILE="$CONFIG_DIR/opencode.jsonc"
AGENTS_FILE="$CONFIG_DIR/AGENTS.md"

echo "=== OpenCode setup for Linux ==="
mkdir -p "$CONFIG_DIR" "$STASH_DIR"

if [[ "${OPENCODE_SETUP_SKIP_NPM:-0}" != "1" ]]; then
  echo "Installing OpenCode CLI and @opencode-ai/plugin@1.15.4..."
  if ! npm install -g @opencode-ai/cli; then
    sudo npm install -g @opencode-ai/cli
  fi
  npm install --prefix "$CONFIG_DIR" --save-exact @opencode-ai/plugin@1.15.4
fi

if [[ -f "$API_KEY_FILE" ]]; then
  echo "Preserved existing API key file: $API_KEY_FILE"
else
  printf '%s\n' 'your-routerai-api-key-here' > "$API_KEY_FILE"
  echo "Created API key placeholder: $API_KEY_FILE"
fi
chmod 600 "$API_KEY_FILE"

if [[ -f "$CONFIG_FILE" ]]; then
  echo "Preserved existing config: $CONFIG_FILE"
else
  cat > "$CONFIG_FILE" <<EOF
{
  "\$schema": "https://opencode.ai/config.json",
  "provider": {
    "routerai": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "RouterAI",
      "options": {
        "baseURL": "https://routerai.ru/api/v1",
        "apiKey": "{file:$API_KEY_FILE}"
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
EOF
  echo "Created config: $CONFIG_FILE"
fi

if [[ -f "$AGENTS_FILE" ]]; then
  echo "Preserved existing instructions: $AGENTS_FILE"
else
  cat > "$AGENTS_FILE" <<'EOF'
# Global OpenCode instructions

- Never expose secrets, tokens, passwords, or API keys.
- Do not scan .git, node_modules, build output, caches, or logs without a reason.
- Prefer concise, structured answers.
EOF
  echo "Created instructions: $AGENTS_FILE"
fi

echo "Setup complete. Add the RouterAI key to $API_KEY_FILE and restart OpenCode."
echo "BMAD is optional and project-local; run ./install_bmad_linux.sh <project-path>."
