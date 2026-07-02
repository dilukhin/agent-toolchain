# Bootstrap промпт для настройки Opencode

Этот промпт предназначен для использования с opencode (любым агентом) при первичной установке. Скопируйте соответствующую секцию после первого запуска opencode.

---

## Для Windows

```text
Установи и настрой все необходимое для работы с opencode:

1. Убедись что установлена Node.js 18+ и npm. Проверь команды node --version и npm --version.

2. Создай директорию `C:\Users\<username>\projects\stash\opencode.ai` 
   В ней создай файл api-key.txt и вставь туда заглушку "your-routerai-api-key-here"
   Этот файл нужно будет отредактировать и добавить реальный API ключ RouterAI

3. Настрой конфигурацию opencode в %APPDATA%\opencode:
   - Создай opencode.jsonc с поддержкой провайдера RouterAI (baseURL https://routerai.ru/api/v1)
   - Добавь следующие модели: openai/gpt-4o, openai/gpt-4o-mini, deepseek/deepseek-chat, 
     anthropic/claude-opus-4.7, qwen/qwen3.6-plus, deepseek/deepseek-v4-pro, minimax/minimax-m2.7,
     z-ai/glm-5, moonshotai/kimi-k2.5, gpt-5.2-codex, qwen/qwen3-coder-next, qwen/qwen3.5-122b-a10b
   - Используй `{file:C:\Users\<username>\projects\stash\opencode.ai\api-key.txt}` для API ключа
   
4. Создай AGENTS.md с глобальной памятью:
   - Маппинг путей WSL: /mnt/c/... → c:\...
   - Описание ssh_relay и bundle.py утилит
   - Правила: отвечать на русском, не сканировать node_modules/.git/кэши/логи, 
     не экспонировать секреты, сохранять прогресс рано

5. Установи зависимости через npm:
   cd %APPDATA%\opencode
   npm init -y
   npm install @opencode-ai/plugin@1.15.4 --save-dev

6. (Опционально) Установи вспомогательные утилиты:
   git clone https://github.com/dilukhin/ssh_relay.git C:\Users\<username>\projects\ssh_relay
   pip install paramiko
   git clone https://github.com/dilukhin/bundle.git C:\Users\<username>\projects\bundle

7. После установки предупреди что нужно:
   - Вставить реальный API ключ в api-key.txt
   - Запустить opencode для проверки
```

## Для Linux/Debian

```text
Установи и настрой все необходимое для работы с opencode:

1. Убедись что установлены Node.js 18+ и npm. Проверь командой node --version и npm --version.

2. Создай директорию ~/projects/stash/opencode.ai 
   В ней создай файл api-key.txt с содержимым "your-routerai-api-key-here"
   Этот файл нужно будет отредактировать и добавить реальный API ключ RouterAI

3. Настрой конфигурацию opencode в ~/.config/opencode:
   - Создай opencode.jsonc с поддержкой провайдера RouterAI (baseURL https://routerai.ru/api/v1)
   - Добавь модели: openai/gpt-4o, openai/gpt-4o-mini, deepseek/deepseek-chat,
     anthropic/claude-opus-4.7, qwen/qwen3.6-plus, deepseek/deepseek-v4-pro, minimax/minimax-m2.7,
     z-ai/glm-5, moonshotai/kimi-k2.5, openai/gpt-5.2-codex, qwen/qwen3-coder-next, qwen/qwen3.5-122b-a10b
   - Используй {file:/home/<username>/projects/stash/opencode.ai/api-key.txt} для API ключа

4. Создай AGENTS.md с глобальной памятью:
   - Описание ssh_relay (https://github.com/dilukhin/ssh_relay.git) и bundle.py (https://github.com/dilukhin/bundle.git)
   - Правила: отвечать на русском, не сканировать node_modules/.git/кэши/логи,
     не экспонировать секреты, сохранять прогресс рано

5. Установи зависимости через npm:
   cd ~/.config/opencode
   npm init -y
   npm install @opencode-ai/plugin@1.15.4 --save-dev

6. (Опционально) Установи вспомогательные утилиты:
   git clone https://github.com/dilukhin/ssh_relay.git ~/projects/ssh_relay
   pip3 install paramiko
   git clone https://github.com/dilukhin/bundle.git ~/projects/bundle

7. После установки предупреди что нужно:
   - Вставить реальный API ключ в api-key.txt
   - Запустить opencode для проверки
```

---

## Краткий вариант (для вставки напрямую)

### Windows PowerShell

```powershell
$new = @{
    '$schema' = 'https://opencode.ai/config.json'
    provider = @{ routerai = @{
        npm = '@ai-sdk/openai-compatible'
        name = 'RouterAI'
        options = @{
            baseURL = 'https://routerai.ru/api/v1'
            apiKey = '{file:C:\Users\' + $env:USERNAME + '\projects\stash\opencode.ai\api-key.txt}'
        }
        models = @{
            'qwen/qwen3.6-plus' = @{ name = 'Qwen 3.6 Plus [main]' }
            'deepseek/deepseek-chat' = @{ name = 'DeepSeek Chat' }
            'anthropic/claude-opus-4.7' = @{ name = 'Claude Opus 4.7' }
            'openai/gpt-4o' = @{ name = 'GPT-4o' }
            'openai/gpt-4o-mini' = @{ name = 'GPT-4o mini' }
            'deepseek/deepseek-v4-pro' = @{ name = 'DeepSeek V4 Pro' }
            'minimax/minimax-m2.7' = @{ name = 'MiniMax M2.7' }
            'z-ai/glm-5' = @{ name = 'GLM-5 [architect]' }
            'moonshotai/kimi-k2.5' = @{ name = 'Kimi K2.5' }
            'openai/gpt-5.2-codex' = @{ name = 'GPT-5.2-Codex' }
            'qwen/qwen3-coder-next' = @{ name = 'Qwen Coder Next' }
            'qwen/qwen3.5-122b-a10b' = @{ name = 'Qwen 3.5 122B' }
        }
   }}
    model = 'opencode/deepseek-v4-flash-free'
    small_model = 'opencode/gpt-5-nano'
}
$cfgDir = "$env:APPDATA\opencode"; $stash = "$env:USERPROFILE\projects\stash\opencode.ai"
New-Item -ItemType Directory -Path $cfgDir,$stash -Force | Out-Null
'your-routerai-api-key-here' | Set-Content "$stash\api-key.txt" -Encoding UTF8
($new | ConvertTo-Json -Depth 10) | Set-Content "$cfgDir\opencode.jsonc" -Encoding UTF8
@"
# Глобальная память
ssh_relay: https://github.com/dilukhin/ssh_relay.git
bundle: https://github.com/dilukhin/bundle.git
Правила: ответ на русском, без воды. Не сканируй node_modules/.git.
Никогда не экспонируй секреты. Сохраняй прогресс рано.
"@ | Set-Content "$cfgDir\AGENTS.md" -Encoding UTF8
Set-Location $cfgDir; npm init -y --yes 2>$null; npm install @opencode-ai/plugin@1.15.4 --save-dev 2>$null
Write-Host "Готово! Вставьте API ключ в $stash\api-key.txt и запустите opencode" -ForegroundColor Green
```

### Linux Bash

```bash
cfg="$HOME/.config/opencode" stash="$HOME/projects/stash/opencode.ai"
mkdir -p "$cfg" "$stash"
echo "your-routerai-api-key-here" > "$stash/api-key.txt" && chmod 600 "$stash/api-key.txt"
cat > "$cfg/opencode.jsonc" << EOF
{
  "\$schema": "https://opencode.ai/config.json",
  "provider": {
    "routerai": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "RouterAI",
      "options": {
        "baseURL": "https://routerai.ru/api/v1",
        "apiKey": "{file:$HOME/projects/stash/opencode.ai/api-key.txt}"
      },
      "models": {
        "qwen/qwen3.6-plus": {"name": "Qwen 3.6 Plus [main]"},
        "deepseek/deepseek-chat": {"name": "DeepSeek Chat"},
        "anthropic/claude-opus-4.7": {"name": "Claude Opus 4.7"},
        "openai/gpt-4o": {"name": "GPT‑4o"},
        "openai/gpt-4o-mini": {"name": "GPT‑4o mini"},
        "deepseek/deepseek-v4-pro": {"name": "DeepSeek V4 Pro"},
        "minimax/minimax-m2.7": {"name": "MiniMax M2.7"},
        "z-ai/glm-5": {"name": "GLM‑5 [architect]"},
        "moonshotai/kimi-k2.5": {"name": "Kimi K2.5"},
        "openai/gpt-5.2-codex": {"name": "GPT‑5.2‑Codex"},
        "qwen/qwen3-coder-next": {"name": "Qwen Coder Next"},
        "qwen/qwen3.5-122b-a10b": {"name": "Qwen 3.5 122B"}
      }
    }
  },
  "model": "opencode/deepseek-v4-flash-free",
  "small_model": "opencode/gpt-5-nano"
}
EOF
cat > "$cfg/AGENTS.md" << 'EOF'
# Глобальная память
ssh_relay: https://github.com/dilukhin/ssh_relay.git
bundle: https://github.com/dilukhin/bundle.git
Правила: ответ на русском, без воды. Не сканируй node_modules/.git.
Никогда не экспонируй секреты. Сохраняй прогресс рано.
EOF
177: cd "$cfg" && npm init -y --yes >/dev/null 2>&1 && npm install @opencode-ai/plugin@1.15.4 --save-dev >/dev/null 2>&1 && cd - >/dev/null
138: echo "your-routerai-api-key-here" > "$stash/api-key.txt"
echo "Готово! Вставьте API ключ в $stash/api-key.txt и запустите opencode"
```

---

## Что делает bootstrap

1. **Создаёт структуру директорий** для конфигов и хранения ключей
2. **Генерирует opencode.jsonc** с полным списком моделей RouterAI
3. **Создает AGENTS.md** с базовой глобальной памятью
4. **Устанавливает @opencode-ai/plugin** для поддержки BMAD-скиллов
5. **Создает заглушку API-ключа** которую нужно заполнить вручную

## Следующие шаги после bootstrap

1. Откройте `api-key.txt` и вставьте реальный API ключ RouterAI
2. Запустите `opencode` для проверки
3. При необходимости установите скрипты:
   ```bash
   # Windows
   git clone https://github.com/dilukhin/ssh_relay.git %USERPROFILE%\projects\ssh_relay
   git clone https://github.com/dilukhin/bundle.git %USERPROFILE%\projects\bundle
   pip install paramiko

   # Linux  
   git clone https://github.com/dilukhin/ssh_relay.git ~/projects/ssh_relay
   git clone https://github.com/dilukhin/bundle.git ~/projects/bundle
   pip3 install paramiko
   ```
