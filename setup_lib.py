"""File ownership, config migration, skill validation, and dependency reconciliation."""
from __future__ import annotations

import copy
import hashlib
import json
import locale
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

MANIFEST_SCHEMA = 1
STATE_MISSING = "missing"
STATE_OK = "up-to-date"
STATE_CONFIGURED = "configured"
STATE_OUTDATED = "outdated"
STATE_FAILED = "failed"
STATE_CONFLICT = "modified/conflict"
STATE_INFO = "info"
STATE_SKIPPED = "skipped"
VALID_STATES = {
    STATE_MISSING,
    STATE_OK,
    STATE_CONFIGURED,
    STATE_OUTDATED,
    STATE_FAILED,
    STATE_CONFLICT,
    STATE_INFO,
    STATE_SKIPPED,
}

AGENTS_BLOCK_START = "<!-- opencode_setup:managed:start -->"
AGENTS_BLOCK_END = "<!-- opencode_setup:managed:end -->"

_ANSI_GREEN = "\x1b[32m"
_ANSI_RED = "\x1b[31m"
_ANSI_RESET = "\x1b[0m"


@dataclass
class Result:
    component: str
    state: str
    detail: str = ""


def _enable_windows_ansi(stream: Any) -> bool:
    if os.name != "nt":
        return True
    if stream is not sys.stdout:
        return False
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        if handle in (0, -1):
            return False
        mode = ctypes.c_uint()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)) == 0:
            return False
        enabled = 0x0004  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        if mode.value & enabled:
            return True
        return bool(kernel32.SetConsoleMode(handle, mode.value | enabled))
    except Exception:
        return False


def _stream_supports_color(stream: Any) -> bool:
    if "NO_COLOR" in os.environ or os.environ.get("TERM", "").lower() == "dumb":
        return False
    try:
        if not stream.isatty():
            return False
    except (AttributeError, OSError):
        return False
    return _enable_windows_ansi(stream)


class Reporter:
    def __init__(self) -> None:
        self.results: list[Result] = []

    def add(self, component: str, state: str, detail: str = "") -> None:
        if state not in VALID_STATES:
            raise ValueError(f"invalid state: {state}")

        # Compatibility for helpers that report a post-action validation/migration row.
        # A successful action is not "up-to-date": it was changed by this run.
        base_component: str | None = None
        suffix = None
        for candidate in (" validation", " migration", " recovery"):
            if component.endswith(candidate):
                base_component = component[:-len(candidate)]
                suffix = candidate
                break
        if component == "OpenCode autoupdate policy":
            base_component = "OpenCode config"
            suffix = " policy"

        if base_component:
            if state == STATE_OK:
                state = STATE_CONFIGURED
            elif state == STATE_CONFLICT and suffix == " recovery":
                state = STATE_FAILED
            for index in range(len(self.results) - 1, -1, -1):
                existing = self.results[index]
                if existing.component == base_component and existing.state not in {STATE_CONFLICT, STATE_FAILED}:
                    self.results[index] = Result(base_component, state, detail)
                    return
            component = base_component

        # A configured component can be validated again later in the same run. Preserve
        # the fact that this run changed it instead of degrading the result to up-to-date.
        if state == STATE_OK:
            for index in range(len(self.results) - 1, -1, -1):
                existing = self.results[index]
                if existing.component != component:
                    continue
                if existing.state == STATE_CONFIGURED:
                    combined = existing.detail
                    if detail and detail not in combined:
                        combined = f"{combined}; итоговая проверка: {detail}" if combined else detail
                    self.results[index] = Result(component, STATE_CONFIGURED, combined)
                    return
                break

        self.results.append(Result(component, state, detail))

    @property
    def has_conflict(self) -> bool:
        return any(x.state in {STATE_CONFLICT, STATE_FAILED} for x in self.results)

    def render(self, *, stream: Any | None = None, color: bool | None = None) -> None:
        stream = sys.stdout if stream is None else stream
        use_color = _stream_supports_color(stream) if color is None else color
        print("STATE              COMPONENT                     DETAILS", file=stream)
        print("-----------------  ----------------------------  ----------------------------------------", file=stream)
        for x in self.results:
            state_field = f"{x.state:<17}"
            if use_color and x.state == STATE_CONFIGURED:
                state_field = f"{_ANSI_GREEN}{state_field}{_ANSI_RESET}"
            elif use_color and x.state in {STATE_FAILED, STATE_CONFLICT}:
                state_field = f"{_ANSI_RED}{state_field}{_ANSI_RESET}"
            print(f"{state_field}  {x.component:<28}  {x.detail}", file=stream)


def _decode_subprocess_output(data: bytes | None) -> str:
    if not data:
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        encoding = locale.getpreferredencoding(False) or "utf-8"
        try:
            return data.decode(encoding, errors="replace")
        except LookupError:
            return data.decode("utf-8", errors="replace")


def run(cmd: list[str], cwd: Path | None = None, env: dict[str, str] | None = None, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
    raw = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=timeout,
    )
    return subprocess.CompletedProcess(
        raw.args,
        raw.returncode,
        _decode_subprocess_output(raw.stdout),
        _decode_subprocess_output(raw.stderr),
    )


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".opencode-setup.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def load_manifest(path: Path) -> tuple[dict[str, Any], str | None]:
    empty = {"schema": MANIFEST_SCHEMA, "managed_files": {}}
    if not path.exists():
        return empty, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return empty, f"manifest is unreadable: {exc}"
    if data.get("schema") != MANIFEST_SCHEMA or not isinstance(data.get("managed_files"), dict):
        return empty, "manifest schema is unsupported"
    return data, None


def save_manifest(path: Path, manifest: dict[str, Any]) -> None:
    payload = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    atomic_write(path, payload.encode("utf-8"))


def backup_file(path: Path, state_dir: Path, component: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", component)
    dest = state_dir / "backups" / stamp / safe / path.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, dest)
    return dest


def validate_skill(path: Path, expected_name: str) -> tuple[bool, str]:
    if not path.is_file():
        return False, "SKILL.md is missing"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        return False, "SKILL.md is not UTF-8"
    if not lines or lines[0].strip() != "---":
        return False, "YAML front matter is missing"
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        return False, "YAML front matter is not closed"
    fields: dict[str, str] = {}
    for line in lines[1:end]:
        if not line.strip() or line.startswith((" ", "\t")) or ":" not in line:
            continue
        key, value = line.split(":", 1)
        fields[key.strip()] = value.strip().strip('"\'')
    name, description = fields.get("name", ""), fields.get("description", "")
    if name != expected_name:
        return False, f"front matter name={name!r} does not match {expected_name!r}"
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
        return False, "skill name is not portable lowercase kebab-case"
    if not description or len(description) > 1024:
        return False, "description must contain 1..1024 characters"
    return True, "valid"


def reconcile_file(*, component: str, destination: Path, source_data: bytes, source_label: str,
                   manifest: dict[str, Any], reporter: Reporter, check: bool, force: bool,
                   state_dir: Path, legacy_hashes: Iterable[str] = ()) -> bool:
    managed = manifest["managed_files"]
    previous = managed.get(component)
    desired_hash = sha256_bytes(source_data)
    legacy = set(legacy_hashes)

    if not destination.exists():
        if check:
            reporter.add(component, STATE_MISSING,
                         f"{destination}; обычный apply создаст управляемый файл автоматически")
            return False
        atomic_write(destination, source_data)
        managed[component] = {"path": str(destination), "sha256": desired_hash, "source": source_label}
        reporter.add(component, STATE_CONFIGURED, f"создан управляемый файл: {destination}")
        return True
    if not destination.is_file():
        reporter.add(component, STATE_CONFLICT, f"destination is not a regular file: {destination}")
        return False

    current_hash = sha256_bytes(destination.read_bytes())
    legacy_owned = previous is None and current_hash in legacy
    if previous and previous.get("path") != str(destination):
        reporter.add(component, STATE_CONFLICT, "manifest points to a different destination")
        return False
    if previous is None and current_hash == desired_hash:
        if check:
            reporter.add(component, STATE_OUTDATED,
                         "содержимое уже совпадает с целевым, но ownership ещё не принят; обычный apply примет ownership")
            return False
        managed[component] = {"path": str(destination), "sha256": desired_hash, "source": source_label}
        reporter.add(component, STATE_CONFIGURED, "содержимое уже совпадало с целевым; ownership принят этим запуском")
        return True
    if previous is None and not legacy_owned:
        reporter.add(component, STATE_CONFLICT, "existing file is not owned by opencode_setup")
        return False

    previous_hash = previous.get("sha256") if previous else current_hash
    if not legacy_owned and current_hash != previous_hash:
        if not force:
            reporter.add(component, STATE_CONFLICT, "managed file was modified locally; preserved")
            return False
        if check:
            reporter.add(component, STATE_CONFLICT, "managed file modified; --force would backup and replace")
            return False
        backup = backup_file(destination, state_dir, component)
        atomic_write(destination, source_data)
        managed[component] = {"path": str(destination), "sha256": desired_hash, "source": source_label}
        reporter.add(component, STATE_CONFIGURED, f"локально изменённый управляемый файл заменён после backup: {backup}")
        return True

    if current_hash == desired_hash:
        metadata_change = previous is None or previous.get("source") != source_label
        if metadata_change:
            if check:
                reporter.add(component, STATE_OUTDATED,
                             "содержимое актуально, но ownership metadata устарела; обычный apply обновит metadata")
                return False
            managed[component] = {"path": str(destination), "sha256": desired_hash, "source": source_label}
            reporter.add(component, STATE_CONFIGURED, "содержимое уже было актуально; ownership metadata обновлена")
            return True
        reporter.add(component, STATE_OK, str(destination))
        return False

    if check:
        reporter.add(component, STATE_OUTDATED,
                     "managed source changed; обычный apply обновит управляемый файл автоматически")
        return False
    atomic_write(destination, source_data)
    managed[component] = {"path": str(destination), "sha256": desired_hash, "source": source_label}
    reporter.add(component, STATE_CONFIGURED, "управляемый файл обновлён до текущего источника")
    return True


def _strip_jsonc_comments(text: str) -> tuple[str, bool]:
    out: list[str] = []
    i = 0
    in_string = False
    escape = False
    changed = False
    while i < len(text):
        ch = text[i]
        if in_string:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < len(text) and text[i + 1] == "/":
            changed = True
            i += 2
            while i < len(text) and text[i] not in "\r\n":
                i += 1
            continue
        if ch == "/" and i + 1 < len(text) and text[i + 1] == "*":
            changed = True
            i += 2
            while i + 1 < len(text) and not (text[i] == "*" and text[i + 1] == "/"):
                if text[i] in "\r\n":
                    out.append(text[i])
                i += 1
            i = min(len(text), i + 2)
            continue
        out.append(ch)
        i += 1
    return "".join(out), changed


def _strip_jsonc_trailing_commas(text: str) -> tuple[str, bool]:
    out: list[str] = []
    i = 0
    in_string = False
    escape = False
    changed = False
    while i < len(text):
        ch = text[i]
        if in_string:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == ",":
            j = i + 1
            while j < len(text) and text[j].isspace():
                j += 1
            if j < len(text) and text[j] in "}]":
                changed = True
                i += 1
                continue
        out.append(ch)
        i += 1
    return "".join(out), changed


def parse_jsonc_object(data: bytes) -> tuple[dict[str, Any] | None, str | None, bool]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None, "file is not UTF-8", False
    without_comments, comments = _strip_jsonc_comments(text)
    normalized, trailing = _strip_jsonc_trailing_commas(without_comments)
    try:
        value = json.loads(normalized)
    except json.JSONDecodeError as exc:
        return None, f"JSONC parse failed: {exc}", comments or trailing
    if not isinstance(value, dict):
        return None, "top-level config is not an object", comments or trailing
    return value, None, comments or trailing


def routerai_provider(config: dict[str, Any]) -> dict[str, Any] | None:
    provider = config.get("provider")
    if not isinstance(provider, dict):
        return None
    routerai = provider.get("routerai")
    return routerai if isinstance(routerai, dict) else None


def routerai_file_credential(config: dict[str, Any]) -> str | None:
    routerai = routerai_provider(config)
    if routerai is None:
        return None
    options = routerai.get("options")
    if not isinstance(options, dict):
        return None
    value = options.get("apiKey")
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"\{file:(.+)\}", value.strip())
    return match.group(1) if match else None


def resolve_credential_path(value: str, config_dir: Path) -> Path:
    expanded = os.path.expandvars(os.path.expanduser(value))
    path = Path(expanded)
    if not path.is_absolute():
        path = config_dir / path
    return path.resolve()


def merge_routerai_config(existing: dict[str, Any], desired: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    existing_router = routerai_provider(existing)
    desired_router = routerai_provider(desired)
    if existing_router is None:
        return None, "existing config has no compatible provider.routerai object"
    if desired_router is None:
        return None, "template has no provider.routerai object"

    merged = copy.deepcopy(existing)
    providers = merged.setdefault("provider", {})
    if not isinstance(providers, dict):
        return None, "existing provider value is not an object"
    router = providers.setdefault("routerai", {})
    if not isinstance(router, dict):
        return None, "existing provider.routerai value is not an object"

    for key in ("npm", "name"):
        if key in desired_router:
            router[key] = copy.deepcopy(desired_router[key])

    desired_options = desired_router.get("options", {})
    options = router.setdefault("options", {})
    if not isinstance(options, dict) or not isinstance(desired_options, dict):
        return None, "RouterAI options are not objects"
    if "baseURL" in desired_options:
        options["baseURL"] = desired_options["baseURL"]
    if "apiKey" in desired_options:
        options["apiKey"] = desired_options["apiKey"]

    desired_models = desired_router.get("models", {})
    models = router.setdefault("models", {})
    if not isinstance(models, dict) or not isinstance(desired_models, dict):
        return None, "RouterAI models are not objects"
    for name, spec in desired_models.items():
        if name not in models:
            models[name] = copy.deepcopy(spec)

    if "$schema" not in merged and "$schema" in desired:
        merged["$schema"] = desired["$schema"]
    for key in ("model", "small_model"):
        if key not in merged and key in desired:
            merged[key] = copy.deepcopy(desired[key])
    return merged, None


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def reconcile_opencode_config(*, destination: Path, desired_data: bytes, source_label: str,
                              manifest: dict[str, Any], reporter: Reporter, check: bool,
                              force: bool, state_dir: Path) -> bool:
    component = "OpenCode config"
    managed = manifest["managed_files"]
    previous = managed.get(component)

    if not destination.exists():
        return reconcile_file(component=component, destination=destination, source_data=desired_data,
                              source_label=source_label, manifest=manifest, reporter=reporter,
                              check=check, force=force, state_dir=state_dir)
    if not destination.is_file():
        reporter.add(component, STATE_CONFLICT, f"destination is not a regular file: {destination}")
        return False

    current_data = destination.read_bytes()
    current_hash = sha256_bytes(current_data)
    if previous and previous.get("path") != str(destination):
        reporter.add(component, STATE_CONFLICT, "manifest points to a different destination")
        return False
    if previous and current_hash != previous.get("sha256") and not force:
        reporter.add(component, STATE_CONFLICT, "managed config was modified locally; preserved")
        return False
    if previous and current_hash != previous.get("sha256") and check:
        reporter.add(component, STATE_CONFLICT, "managed config modified; --force would backup and safely merge")
        return False

    existing, error, has_jsonc_features = parse_jsonc_object(current_data)
    desired, desired_error, _ = parse_jsonc_object(desired_data)
    if error or existing is None:
        reporter.add(component, STATE_CONFLICT, error or "existing config cannot be parsed")
        return False
    if desired_error or desired is None:
        reporter.add(component, STATE_CONFLICT, desired_error or "template cannot be parsed")
        return False

    merged, merge_error = merge_routerai_config(existing, desired)
    if merge_error or merged is None:
        reporter.add(component, STATE_CONFLICT, merge_error or "existing config is not safely mergeable")
        return False

    semantic_change = merged != existing
    if previous is None and semantic_change and has_jsonc_features:
        reporter.add(component, STATE_CONFLICT,
                     "compatible JSONC contains comments/trailing commas and needs changes; preserved to avoid formatting loss")
        return False

    if previous is None and not semantic_change:
        if check:
            reporter.add(component, STATE_OUTDATED,
                         "совместимый RouterAI config уже совпадает по содержимому, но ownership ещё не принят; "
                         "обычный apply примет ownership")
            return False
        managed[component] = {"path": str(destination), "sha256": current_hash,
                              "source": source_label, "mode": "merged-json"}
        reporter.add(component, STATE_CONFIGURED,
                     "содержимое RouterAI config уже было совместимым; ownership принят, user settings сохранены")
        return True

    if not semantic_change:
        metadata_change = bool(previous) and (
            previous.get("sha256") != current_hash
            or previous.get("mode") != "merged-json"
            or previous.get("source") != source_label
        )
        if previous and current_hash != previous.get("sha256") and force:
            if check:
                reporter.add(component, STATE_CONFLICT, "--force would backup and adopt safely merged config")
                return False
            backup = backup_file(destination, state_dir, component)
            managed[component] = {"path": str(destination), "sha256": current_hash,
                                  "source": source_label, "mode": "merged-json"}
            reporter.add(component, STATE_CONFIGURED,
                         f"совместимый config принят после backup без изменения user settings: {backup}")
            return True
        if metadata_change:
            if check:
                reporter.add(component, STATE_OUTDATED,
                             "содержимое RouterAI config актуально, но ownership metadata устарела; обычный apply обновит metadata")
                return False
            managed[component] = {"path": str(destination), "sha256": current_hash,
                                  "source": source_label, "mode": "merged-json"}
            reporter.add(component, STATE_CONFIGURED,
                         "содержимое уже было актуально; ownership metadata обновлена, user settings сохранены")
            return True
        reporter.add(component, STATE_OK, "compatible RouterAI config; user settings preserved")
        return False
    if check:
        reporter.add(component, STATE_OUTDATED,
                     "compatible existing RouterAI config; managed fields can be merged without removing user settings; "
                     "обычный apply выполнит merge автоматически")
        return False
    backup = backup_file(destination, state_dir, component)
    merged_data = _json_bytes(merged)
    atomic_write(destination, merged_data)
    managed[component] = {"path": str(destination), "sha256": sha256_bytes(merged_data),
                          "source": source_label, "mode": "merged-json"}
    reporter.add(component, STATE_CONFIGURED, f"managed fields merged; user settings сохранены; backup: {backup}")
    return True


_AGENT_TOOLCHAIN_AGENTS_BLOCK_START = "<!-- agent-toolchain:managed:start:v1 -->"
_AGENT_TOOLCHAIN_AGENTS_BLOCK_END = "<!-- agent-toolchain:managed:end:v1 -->"
_AGENTS_BOOTSTRAP_MODE = "bootstrap-block-v1"
_AGENTS_BOOTSTRAP_SOURCE = "agent-toolchain:global-AGENTS-bootstrap:v1"
_MANAGED_INSTRUCTIONS_COMPONENT = "OpenCode managed instructions"
_MANAGED_INSTRUCTIONS_SOURCE = "agent-toolchain:templates/AGENTS.md"

_PRE_SPLIT_AGENTS = b"""# Global OpenCode instructions

- Never expose secrets, tokens, passwords, API keys, or credential files.
- Do not scan `.git`, `node_modules`, build output, caches, or logs without a reason.
- When work uses `ssh_relay`, load the `ssh-relay` skill first.
- Before builds, CMake, CTest, integration/load tests, long scripts, or other long-running operations, load `remote-long-running`.
- Before risky state-changing actions or work in an unfamiliar subsystem, load the relevant agent-safe skill: `risk-gate`, `safe-cli`, `unknown-system-safety`, or `recovery-mode`.
- Do not preload specialized skills unless the current task needs them.
"""

_EARLY_LEGACY_AGENTS = b"""# Global OpenCode instructions

- Never expose secrets, tokens, passwords, or API keys.
- Do not scan .git, node_modules, build output, caches, or logs without a reason.
- Prefer concise, structured answers.
"""


@dataclass
class _AgentsPlan:
    state: str
    detail: str
    updated_data: bytes | None = None
    needs_backup: bool = False
    manifest_entry: dict[str, Any] | None = None


def _managed_instructions_path(destination: Path) -> Path:
    return destination.parent / "agent-toolchain" / "managed-instructions.md"


def _managed_agents_block(managed_path: Path) -> bytes:
    block = (
        f"{_AGENT_TOOLCHAIN_AGENTS_BLOCK_START}\n"
        "## agent-toolchain bootstrap\n"
        "- Never expose secrets, tokens, passwords, API keys, or credential files.\n"
        f"- Before using managed tools or changing machine state, read and follow `{managed_path}`.\n"
        "- If that file cannot be read, do not perform state-changing actions that depend on agent-toolchain policy; report the problem.\n"
        "- Do not edit this managed block. Put machine-specific or user-specific persistent instructions outside these markers.\n"
        f"{_AGENT_TOOLCHAIN_AGENTS_BLOCK_END}\n"
    )
    return block.encode("utf-8")


def _canonical_managed_block(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _managed_block_hash(data: bytes) -> str:
    return sha256_bytes(_canonical_managed_block(data))


def _managed_blocks_equal(left: bytes, right: bytes) -> bool:
    return _canonical_managed_block(left) == _canonical_managed_block(right)


def _fresh_agents_document(desired_block: bytes) -> bytes:
    return b"# Global OpenCode instructions\n\n" + desired_block


def _bootstrap_manifest_entry(destination: Path, desired_block: bytes) -> dict[str, Any]:
    return {
        "path": str(destination),
        "source": _AGENTS_BOOTSTRAP_SOURCE,
        "mode": _AGENTS_BOOTSTRAP_MODE,
        "block_sha256": _managed_block_hash(desired_block),
    }


def _known_legacy_agents_payloads() -> dict[str, bytes]:
    payloads: dict[str, bytes] = {}
    for base in (_PRE_SPLIT_AGENTS, _EARLY_LEGACY_AGENTS):
        variants = [base, base.replace(b"\n", b"\r\n")]
        variants.extend([b"\xef\xbb\xbf" + item for item in list(variants)])
        for payload in variants:
            payloads[sha256_bytes(payload)] = payload
    return payloads


def _legacy_block_from_whole(payload: bytes) -> bytes | None:
    try:
        text = payload.decode("utf-8-sig").replace("\r\n", "\n")
    except UnicodeDecodeError:
        return None
    lines = text.strip().splitlines()
    body = "\n".join(lines[2:] if lines and lines[0].startswith("# ") else lines).strip()
    return (
        f"{AGENTS_BLOCK_START}\n## OpenCode managed environment\n{body}\n{AGENTS_BLOCK_END}\n"
    ).encode("utf-8")


def _known_legacy_blocks() -> set[str]:
    hashes: set[str] = set()
    seen: set[bytes] = set()
    for payload in _known_legacy_agents_payloads().values():
        block = _legacy_block_from_whole(payload)
        if block is not None and block not in seen:
            seen.add(block)
            hashes.add(_managed_block_hash(block))
    return hashes


def _locate_block(text: str, start_marker: str, end_marker: str) -> tuple[int, int, bytes] | None:
    start_count = text.count(start_marker)
    end_count = text.count(end_marker)
    if start_count == 0 and end_count == 0:
        return None
    if start_count != 1 or end_count != 1:
        raise ValueError("managed block markers are duplicated or incomplete")
    start = text.find(start_marker)
    end = text.find(end_marker)
    if end < start:
        raise ValueError("managed block markers are malformed")
    end_pos = end + len(end_marker)
    return start, end_pos, (text[start:end_pos] + "\n").encode("utf-8")


def _replace_block(text: str, start: int, end_pos: int, desired_block: bytes) -> bytes:
    desired_text = desired_block.decode("utf-8").rstrip("\n")
    updated = text[:start] + desired_text + text[end_pos:]
    if not updated.endswith("\n"):
        updated += "\n"
    return updated.encode("utf-8")


def _plan_agents_file(*, destination: Path, desired_block: bytes, previous: dict[str, Any] | None,
                      force: bool, legacy_hashes: Iterable[str]) -> _AgentsPlan:
    desired_entry = _bootstrap_manifest_entry(destination, desired_block)
    fresh_document = _fresh_agents_document(desired_block)

    if destination.is_symlink():
        return _AgentsPlan(STATE_CONFLICT, f"глобальный AGENTS.md является symlink; автоматическая migration запрещена: {destination}")
    if not destination.exists():
        return _AgentsPlan(
            STATE_MISSING,
            f"{destination}; `toolchainctl apply` создаст пользовательский AGENTS.md с маленьким managed bootstrap-блоком",
            updated_data=fresh_document,
            manifest_entry=desired_entry,
        )
    if not destination.is_file():
        return _AgentsPlan(STATE_CONFLICT, f"путь global AGENTS.md существует, но это не обычный файл: {destination}")

    current = destination.read_bytes()
    try:
        text = current.decode("utf-8")
    except UnicodeDecodeError:
        return _AgentsPlan(STATE_CONFLICT, "global AGENTS.md не является UTF-8; файл сохранён без изменений")

    try:
        new_block = _locate_block(text, _AGENT_TOOLCHAIN_AGENTS_BLOCK_START, _AGENT_TOOLCHAIN_AGENTS_BLOCK_END)
        old_block = _locate_block(text, AGENTS_BLOCK_START, AGENTS_BLOCK_END)
    except ValueError as exc:
        return _AgentsPlan(STATE_CONFLICT, f"неоднозначные managed markers в global AGENTS.md: {exc}; файл сохранён")

    if new_block is not None and old_block is not None:
        return _AgentsPlan(STATE_CONFLICT, "global AGENTS.md одновременно содержит legacy и current managed markers; файл сохранён")

    if previous is None:
        current_hash = sha256_bytes(current)
        known_hashes = set(_known_legacy_agents_payloads()) | set(legacy_hashes)
        if current_hash in known_hashes:
            return _AgentsPlan(
                STATE_OUTDATED,
                "распознан legacy whole-file AGENTS.md; `toolchainctl apply` мигрирует его в bootstrap + отдельный managed-файл с backup",
                updated_data=fresh_document,
                needs_backup=True,
                manifest_entry=desired_entry,
            )
        if new_block is not None:
            _start, _end_pos, current_block = new_block
            if not _managed_blocks_equal(current_block, desired_block):
                return _AgentsPlan(STATE_CONFLICT, "непринадлежащий manifest bootstrap-блок отличается от целевого; файл сохранён")
            return _AgentsPlan(
                STATE_OUTDATED,
                "bootstrap-блок уже совпадает с целевым, но ownership ещё не принят; обычный apply примет block ownership",
                manifest_entry=desired_entry,
            )
        if old_block is not None:
            start, end_pos, current_block = old_block
            if _managed_block_hash(current_block) not in _known_legacy_blocks():
                return _AgentsPlan(STATE_CONFLICT, "legacy managed block без ownership metadata не совпадает с известным payload; файл сохранён")
            return _AgentsPlan(
                STATE_OUTDATED,
                "распознан legacy managed block; обычный apply заменит только его на bootstrap-блок и сохранит surrounding user text",
                updated_data=_replace_block(text, start, end_pos, desired_block),
                needs_backup=True,
                manifest_entry=desired_entry,
            )
        separator = "" if not text or text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
        return _AgentsPlan(
            STATE_OUTDATED,
            "существующий пользовательский AGENTS.md не принадлежит agent-toolchain; обычный apply добавит только managed bootstrap-блок и сохранит весь текущий текст",
            updated_data=(text + separator + desired_block.decode("utf-8")).encode("utf-8"),
            needs_backup=True,
            manifest_entry=desired_entry,
        )

    if previous.get("path") != str(destination):
        return _AgentsPlan(STATE_CONFLICT, "ownership manifest для global AGENTS.md указывает на другой путь; файл сохранён")

    mode = previous.get("mode")
    if mode == _AGENTS_BOOTSTRAP_MODE:
        if new_block is None:
            return _AgentsPlan(STATE_CONFLICT, "managed bootstrap-блок отсутствует или повреждён; пользовательский файл сохранён")
        start, end_pos, current_block = new_block
        previous_block_hash = previous.get("block_sha256")
        if not isinstance(previous_block_hash, str):
            return _AgentsPlan(STATE_CONFLICT, "manifest bootstrap-блока не содержит block_sha256; автоматическая mutation запрещена")
        current_block_hash = _managed_block_hash(current_block)
        if current_block_hash != previous_block_hash:
            if not force:
                return _AgentsPlan(STATE_CONFLICT, "managed bootstrap-блок изменён локально; пользовательский текст вне блока сохранён")
            return _AgentsPlan(
                STATE_OUTDATED,
                "managed bootstrap-блок изменён локально; явный --force заменит только блок после backup, surrounding user text сохранится",
                updated_data=_replace_block(text, start, end_pos, desired_block),
                needs_backup=True,
                manifest_entry=desired_entry,
            )
        if _managed_blocks_equal(current_block, desired_block):
            if previous != desired_entry:
                return _AgentsPlan(
                    STATE_OUTDATED,
                    "bootstrap-блок актуален, но ownership metadata устарела; обычный apply обновит только metadata",
                    manifest_entry=desired_entry,
                )
            return _AgentsPlan(STATE_OK, "bootstrap-блок актуален; пользовательский текст вне блока не управляется и сохранён")
        return _AgentsPlan(
            STATE_OUTDATED,
            "managed bootstrap-блок устарел; обычный apply обновит только блок и сохранит пользовательский текст вне него",
            updated_data=_replace_block(text, start, end_pos, desired_block),
            needs_backup=True,
            manifest_entry=desired_entry,
        )

    if mode == "block":
        if old_block is None:
            return _AgentsPlan(STATE_CONFLICT, "legacy managed block отсутствует или повреждён; surrounding user text сохранён")
        start, end_pos, current_block = old_block
        previous_block_hash = previous.get("block_sha256")
        if not isinstance(previous_block_hash, str):
            return _AgentsPlan(STATE_CONFLICT, "legacy block ownership не содержит block_sha256; автоматическая migration запрещена")
        if _managed_block_hash(current_block) != previous_block_hash and not force:
            return _AgentsPlan(STATE_CONFLICT, "legacy managed block изменён локально; surrounding user text сохранён")
        return _AgentsPlan(
            STATE_OUTDATED,
            "legacy block ownership будет мигрирован в новый bootstrap-блок; surrounding user text сохранится",
            updated_data=_replace_block(text, start, end_pos, desired_block),
            needs_backup=True,
            manifest_entry=desired_entry,
        )

    previous_hash = previous.get("sha256")
    if not isinstance(previous_hash, str):
        return _AgentsPlan(STATE_CONFLICT, "legacy whole-file ownership не содержит sha256; автоматическая migration запрещена")
    current_hash = sha256_bytes(current)
    if current_hash == previous_hash:
        return _AgentsPlan(
            STATE_OUTDATED,
            "legacy whole-file AGENTS.md не изменён; обычный apply мигрирует его в bootstrap + отдельный managed-файл после backup",
            updated_data=fresh_document,
            needs_backup=True,
            manifest_entry=desired_entry,
        )

    historical = _known_legacy_agents_payloads().get(previous_hash)
    if historical is None:
        return _AgentsPlan(
            STATE_CONFLICT,
            "legacy whole-file AGENTS.md изменён, а предыдущий managed payload неизвестен; automatic migration и --force запрещены, файл сохранён",
        )
    first = current.find(historical)
    if first < 0 or current.find(historical, first + 1) >= 0:
        return _AgentsPlan(
            STATE_CONFLICT,
            "legacy whole-file AGENTS.md изменён внутри прежнего managed payload или содержит неоднозначные совпадения; automatic migration и --force запрещены",
        )
    preserved_prefix = current[:first]
    preserved_suffix = current[first + len(historical):]
    return _AgentsPlan(
        STATE_OUTDATED,
        "legacy whole-file AGENTS.md содержит доказанно неизменный historical managed payload и локальный surrounding text; обычный apply сохранит surrounding text и мигрирует ownership после backup",
        updated_data=preserved_prefix + fresh_document + preserved_suffix,
        needs_backup=True,
        manifest_entry=desired_entry,
    )


def _latest_component_state(reporter: Reporter, component: str) -> str | None:
    for result in reversed(reporter.results):
        if result.component == component:
            return result.state
    return None


def reconcile_agents_file(*, destination: Path, template_data: bytes, source_label: str,
                          manifest: dict[str, Any], reporter: Reporter, check: bool,
                          force: bool, state_dir: Path, legacy_hashes: Iterable[str] = ()) -> bool:
    del source_label  # Legacy call-site label is intentionally not written into new ownership metadata.
    managed = manifest["managed_files"]
    previous = managed.get("global AGENTS.md")
    managed_path = _managed_instructions_path(destination)
    desired_block = _managed_agents_block(managed_path)
    plan = _plan_agents_file(
        destination=destination,
        desired_block=desired_block,
        previous=previous,
        force=force,
        legacy_hashes=legacy_hashes,
    )

    if plan.state == STATE_CONFLICT:
        reporter.add("global AGENTS.md", STATE_CONFLICT, plan.detail)
        return False

    managed_dir = managed_path.parent
    if managed_dir.is_symlink():
        reporter.add(_MANAGED_INSTRUCTIONS_COMPONENT, STATE_CONFLICT,
                     f"каталог managed instructions является symlink; автоматическая запись запрещена: {managed_dir}")
        reporter.add("global AGENTS.md", STATE_CONFLICT,
                     "managed instructions unresolved; bootstrap-блок не изменён")
        return False
    if managed_dir.exists() and not managed_dir.is_dir():
        reporter.add(_MANAGED_INSTRUCTIONS_COMPONENT, STATE_CONFLICT,
                     f"путь managed instructions существует, но это не каталог: {managed_dir}")
        reporter.add("global AGENTS.md", STATE_CONFLICT,
                     "managed instructions unresolved; bootstrap-блок не изменён")
        return False
    if managed_path.is_symlink():
        reporter.add(_MANAGED_INSTRUCTIONS_COMPONENT, STATE_CONFLICT,
                     f"managed instructions path является symlink; автоматическая запись запрещена: {managed_path}")
        reporter.add("global AGENTS.md", STATE_CONFLICT,
                     "managed instructions unresolved; bootstrap-блок не изменён")
        return False

    instructions_changed = reconcile_file(
        component=_MANAGED_INSTRUCTIONS_COMPONENT,
        destination=managed_path,
        source_data=template_data,
        source_label=_MANAGED_INSTRUCTIONS_SOURCE,
        manifest=manifest,
        reporter=reporter,
        check=check,
        force=force,
        state_dir=state_dir,
    )
    instructions_state = _latest_component_state(reporter, _MANAGED_INSTRUCTIONS_COMPONENT)
    if instructions_state in {STATE_CONFLICT, STATE_FAILED}:
        reporter.add("global AGENTS.md", STATE_CONFLICT,
                     "managed instructions не приведены к безопасному target state; bootstrap-блок сохранён без изменений")
        return instructions_changed

    if check:
        reporter.add("global AGENTS.md", plan.state, plan.detail)
        return False

    changed = instructions_changed
    current = destination.read_bytes() if destination.exists() else None
    if plan.updated_data is not None and current != plan.updated_data:
        backup = None
        if destination.exists() and plan.needs_backup:
            backup = backup_file(destination, state_dir, "global AGENTS.md")
        atomic_write(destination, plan.updated_data)
        changed = True
        detail = "global AGENTS.md переведён на bootstrap-block ownership; пользовательский текст вне managed payload сохранён"
        if backup is not None:
            detail += f"; backup: {backup}"
        reporter.add("global AGENTS.md", STATE_CONFIGURED, detail)
    elif plan.manifest_entry is not None and managed.get("global AGENTS.md") != plan.manifest_entry:
        reporter.add("global AGENTS.md", STATE_CONFIGURED,
                     "bootstrap-блок уже был актуален; ownership metadata переведена на block-only model")
    else:
        reporter.add("global AGENTS.md", STATE_OK,
                     "bootstrap-блок актуален; пользовательский текст вне блока не управляется и сохранён")

    if plan.manifest_entry is not None and managed.get("global AGENTS.md") != plan.manifest_entry:
        managed["global AGENTS.md"] = plan.manifest_entry
        changed = True
    return changed


def normalize_repo_url(value: str) -> str:
    value = value.strip()
    if value.startswith("git@github.com:"):
        value = "https://github.com/" + value[len("git@github.com:"):]
    if value.startswith("ssh://git@github.com/"):
        value = "https://github.com/" + value[len("ssh://git@github.com/"):]
    if value.startswith(("https://github.com/", "http://github.com/")):
        value = value.rstrip("/")
        return (value[:-4] if value.endswith(".git") else value).lower()
    candidate = Path(value).expanduser()
    if candidate.exists() or value.startswith(("/", ".")) or re.match(r"^[A-Za-z]:[\\/]", value):
        return str(candidate.resolve())
    return value.rstrip("/")


def _benign_untracked(path: str) -> bool:
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.startswith(".agent-safety/") or normalized.lower().endswith(".md")


def inspect_repo(path: Path, expected_url: str, branch: str) -> tuple[str, str]:
    if not path.exists():
        return STATE_MISSING, str(path)
    if not (path / ".git").exists():
        return STATE_CONFLICT, "path exists but is not a git working copy"
    origin = run(["git", "config", "--get", "remote.origin.url"], cwd=path)
    if origin.returncode != 0:
        return STATE_CONFLICT, "cannot read remote.origin.url"
    if normalize_repo_url(origin.stdout.strip()) != normalize_repo_url(expected_url):
        return STATE_CONFLICT, f"unexpected origin: {origin.stdout.strip()}"
    current = run(["git", "branch", "--show-current"], cwd=path)
    if current.returncode != 0 or current.stdout.strip() != branch:
        return STATE_CONFLICT, f"expected branch {branch!r}"

    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
    dirty = run(["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd=path, env=env)
    if dirty.returncode != 0:
        return STATE_CONFLICT, "git status failed"
    tracked_changes: list[str] = []
    untracked: list[str] = []
    for line in dirty.stdout.splitlines():
        if line.startswith("?? "):
            untracked.append(line[3:])
        elif line.strip():
            tracked_changes.append(line)
    if tracked_changes:
        return STATE_CONFLICT, "working copy has tracked local changes; no reset/clean performed"
    unsafe_untracked = [item for item in untracked if not _benign_untracked(item)]
    if unsafe_untracked:
        sample = ", ".join(unsafe_untracked[:3])
        return STATE_CONFLICT, f"working copy has non-benign untracked files ({sample}); no reset/clean performed"

    head = run(["git", "rev-parse", "HEAD"], cwd=path)
    if head.returncode != 0:
        return STATE_CONFLICT, "cannot resolve local HEAD"
    head_sha = head.stdout.strip()

    tracking = run(["git", "rev-parse", "--verify", f"refs/remotes/origin/{branch}"], cwd=path)
    if tracking.returncode == 0:
        relation = run(["git", "rev-list", "--left-right", "--count",
                        f"HEAD...refs/remotes/origin/{branch}"], cwd=path)
        if relation.returncode != 0:
            return STATE_CONFLICT, "cannot compare local branch with its tracking ref"
        try:
            local_only, tracked_only = (int(x) for x in relation.stdout.split())
        except ValueError:
            return STATE_CONFLICT, "unexpected git rev-list output"
        if local_only:
            return STATE_CONFLICT, "clean tracked working copy contains local commits; no reset/rebase performed"
        if tracked_only and untracked:
            return STATE_CONFLICT, "recorded upstream update exists while benign untracked files are present; pull skipped"
        if tracked_only:
            return STATE_OUTDATED, f"local {head_sha[:12]} is behind recorded origin/{branch}"

    remote = run(["git", "ls-remote", "--exit-code", "origin", f"refs/heads/{branch}"], cwd=path)
    if remote.returncode != 0 or not remote.stdout.strip():
        return STATE_CONFLICT, "tracked working copy is clean, but origin cannot be queried safely"
    remote_sha = remote.stdout.split()[0]
    if head_sha == remote_sha:
        suffix = f"; preserved {len(untracked)} benign untracked file(s)" if untracked else ""
        return STATE_OK, head_sha[:12] + suffix
    if untracked:
        return STATE_CONFLICT, "upstream differs while benign untracked files are present; pull skipped"
    return STATE_OUTDATED, f"local {head_sha[:12]} != origin {remote_sha[:12]}"


def reconcile_repo(*, component: str, path: Path, url: str, branch: str,
                   reporter: Reporter, check: bool) -> tuple[bool, str]:
    state, detail = inspect_repo(path, url, branch)
    if check:
        if state == STATE_MISSING:
            detail += "; обычный apply клонирует управляемый репозиторий автоматически"
        elif state == STATE_OUTDATED:
            detail += "; обычный apply выполнит безопасный ff-only update автоматически"
        reporter.add(component, state, detail)
        return state in {STATE_OK, STATE_OUTDATED}, state

    if state == STATE_CONFLICT:
        reporter.add(component, state, detail)
        return False, state
    if state == STATE_OK:
        reporter.add(component, state, detail)
        return True, state

    actions: list[str] = []
    if state == STATE_MISSING:
        path.parent.mkdir(parents=True, exist_ok=True)
        cp = run(["git", "clone", "--branch", branch, "--single-branch", url, str(path)])
        if cp.returncode != 0:
            reporter.add(component, STATE_FAILED, "clone завершился ошибкой; repository не настроен: " + cp.stderr.strip()[-400:])
            return False, STATE_CONFLICT
        actions.append("репозиторий клонирован")
        state, detail = inspect_repo(path, url, branch)
        if state not in {STATE_OK, STATE_OUTDATED}:
            reporter.add(component, STATE_FAILED,
                         "clone завершён, но итоговое состояние repository не подтверждено: " + detail)
            return False, STATE_CONFLICT
    if state == STATE_OUTDATED:
        cp = run(["git", "pull", "--ff-only", "origin", branch], cwd=path)
        if cp.returncode != 0:
            reporter.add(component, STATE_FAILED, "ff-only update завершился ошибкой; repository сохранён без reset/clean")
            return False, STATE_CONFLICT
        actions.append("безопасный ff-only update применён")
        state, detail = inspect_repo(path, url, branch)
        if state != STATE_OK:
            reporter.add(component, STATE_FAILED,
                         "update завершён, но итоговое состояние repository не подтверждено: " + detail)
            return False, STATE_CONFLICT

    if state == STATE_OK:
        if actions:
            reporter.add(component, STATE_CONFIGURED, "; ".join(actions) + f"; итоговая проверка: {detail}")
        else:
            reporter.add(component, STATE_OK, detail)
        return True, STATE_OK
    reporter.add(component, STATE_FAILED, "reconciliation завершён без подтверждённого целевого состояния")
    return False, STATE_CONFLICT