"""File ownership, config migration, skill validation, and dependency reconciliation."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

MANIFEST_SCHEMA = 1
STATE_MISSING = "missing"
STATE_OK = "up-to-date"
STATE_OUTDATED = "outdated"
STATE_CONFLICT = "modified/conflict"
VALID_STATES = {STATE_MISSING, STATE_OK, STATE_OUTDATED, STATE_CONFLICT}

AGENTS_BLOCK_START = "<!-- opencode_setup:managed:start -->"
AGENTS_BLOCK_END = "<!-- opencode_setup:managed:end -->"


@dataclass
class Result:
    component: str
    state: str
    detail: str = ""


class Reporter:
    def __init__(self) -> None:
        self.results: list[Result] = []

    def add(self, component: str, state: str, detail: str = "") -> None:
        if state not in VALID_STATES:
            raise ValueError(f"invalid state: {state}")
        self.results.append(Result(component, state, detail))

    @property
    def has_conflict(self) -> bool:
        return any(x.state == STATE_CONFLICT for x in self.results)

    def render(self) -> None:
        print("STATE              COMPONENT                     DETAILS")
        print("-----------------  ----------------------------  ----------------------------------------")
        for x in self.results:
            print(f"{x.state:<17}  {x.component:<28}  {x.detail}")


def run(cmd: list[str], cwd: Path | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=env, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)


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
        reporter.add(component, STATE_MISSING, str(destination))
        if check:
            return False
        atomic_write(destination, source_data)
        managed[component] = {"path": str(destination), "sha256": desired_hash, "source": source_label}
        return True
    if not destination.is_file():
        reporter.add(component, STATE_CONFLICT, f"destination is not a regular file: {destination}")
        return False

    current_hash = sha256_bytes(destination.read_bytes())
    legacy_owned = previous is None and current_hash in legacy
    if previous and previous.get("path") != str(destination):
        reporter.add(component, STATE_CONFLICT, "manifest points to a different destination")
        return False
    if previous is None and not legacy_owned and current_hash == desired_hash:
        reporter.add(component, STATE_OK, f"exact source match; {'would adopt' if check else 'adopted'} ownership")
        if check:
            return False
        managed[component] = {"path": str(destination), "sha256": desired_hash, "source": source_label}
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
        reporter.add(component, STATE_OUTDATED, f"forced replacement after backup: {backup}")
        return True

    if current_hash == desired_hash:
        reporter.add(component, STATE_OK, str(destination))
        if not check and (previous is None or previous.get("source") != source_label):
            managed[component] = {"path": str(destination), "sha256": desired_hash, "source": source_label}
            return True
        return False

    reporter.add(component, STATE_OUTDATED, "managed source changed")
    if check:
        return False
    atomic_write(destination, source_data)
    managed[component] = {"path": str(destination), "sha256": desired_hash, "source": source_label}
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
        reporter.add(component, STATE_OK, f"compatible existing RouterAI config; {'would adopt' if check else 'adopted'} ownership")
        if check:
            return False
        managed[component] = {"path": str(destination), "sha256": current_hash,
                              "source": source_label, "mode": "merged-json"}
        return True

    if not semantic_change:
        detail = "compatible RouterAI config; user settings preserved"
        if previous and current_hash != previous.get("sha256") and force:
            if check:
                reporter.add(component, STATE_CONFLICT, "--force would backup and adopt safely merged config")
                return False
            backup = backup_file(destination, state_dir, component)
            reporter.add(component, STATE_OUTDATED, f"forced safe adoption after backup: {backup}")
        else:
            reporter.add(component, STATE_OK, detail)
        if not check and (previous is None or previous.get("sha256") != current_hash or previous.get("mode") != "merged-json"):
            managed[component] = {"path": str(destination), "sha256": current_hash,
                                  "source": source_label, "mode": "merged-json"}
            return True
        return False

    reporter.add(component, STATE_OUTDATED,
                 "compatible existing RouterAI config; managed fields can be merged without removing user settings")
    if check:
        return False
    backup = backup_file(destination, state_dir, component)
    merged_data = _json_bytes(merged)
    atomic_write(destination, merged_data)
    managed[component] = {"path": str(destination), "sha256": sha256_bytes(merged_data),
                          "source": source_label, "mode": "merged-json"}
    reporter.add(component + " migration", STATE_OK, f"backup: {backup}")
    return True


def _managed_agents_block(template_data: bytes) -> bytes:
    text = template_data.decode("utf-8").strip()
    lines = text.splitlines()
    body = "\n".join(lines[2:] if lines and lines[0].startswith("# ") else lines).strip()
    block = f"{AGENTS_BLOCK_START}\n## OpenCode managed environment\n{body}\n{AGENTS_BLOCK_END}\n"
    return block.encode("utf-8")


def reconcile_agents_file(*, destination: Path, template_data: bytes, source_label: str,
                          manifest: dict[str, Any], reporter: Reporter, check: bool,
                          force: bool, state_dir: Path, legacy_hashes: Iterable[str] = ()) -> bool:
    component = "global AGENTS.md"
    managed = manifest["managed_files"]
    previous = managed.get(component)
    if not destination.exists() or (previous and previous.get("mode") != "block"):
        if previous is None and destination.is_file():
            current_hash = sha256_bytes(destination.read_bytes())
            if current_hash != sha256_bytes(template_data) and current_hash not in set(legacy_hashes):
                pass
            else:
                return reconcile_file(component=component, destination=destination, source_data=template_data,
                                      source_label=source_label, manifest=manifest, reporter=reporter,
                                      check=check, force=force, state_dir=state_dir, legacy_hashes=legacy_hashes)
        elif previous is not None or not destination.exists():
            return reconcile_file(component=component, destination=destination, source_data=template_data,
                                  source_label=source_label, manifest=manifest, reporter=reporter,
                                  check=check, force=force, state_dir=state_dir, legacy_hashes=legacy_hashes)

    if not destination.is_file():
        reporter.add(component, STATE_CONFLICT, f"destination is not a regular file: {destination}")
        return False

    current = destination.read_bytes()
    try:
        text = current.decode("utf-8")
    except UnicodeDecodeError:
        reporter.add(component, STATE_CONFLICT, "existing AGENTS.md is not UTF-8")
        return False
    desired_block = _managed_agents_block(template_data)
    desired_text = desired_block.decode("utf-8")
    start = text.find(AGENTS_BLOCK_START)
    end = text.find(AGENTS_BLOCK_END)

    if previous is None:
        if start >= 0 or end >= 0:
            if start < 0 or end < start:
                reporter.add(component, STATE_CONFLICT, "managed block markers are incomplete; preserved")
                return False
            end_pos = end + len(AGENTS_BLOCK_END)
            block = (text[start:end_pos] + "\n").encode("utf-8")
            if sha256_bytes(block) != sha256_bytes(desired_block):
                reporter.add(component, STATE_CONFLICT, "unowned managed block differs from desired block; preserved")
                return False
            reporter.add(component, STATE_OK, f"existing managed block; {'would adopt' if check else 'adopted'} block ownership")
            if check:
                return False
            managed[component] = {"path": str(destination), "sha256": sha256_bytes(current),
                                  "source": source_label, "mode": "block",
                                  "block_sha256": sha256_bytes(desired_block)}
            return True

        reporter.add(component, STATE_OUTDATED, "existing user AGENTS.md; managed block can be appended safely")
        if check:
            return False
        backup = backup_file(destination, state_dir, component)
        separator = "" if not text or text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
        updated = (text + separator + desired_text).encode("utf-8")
        atomic_write(destination, updated)
        managed[component] = {"path": str(destination), "sha256": sha256_bytes(updated),
                              "source": source_label, "mode": "block",
                              "block_sha256": sha256_bytes(desired_block)}
        reporter.add(component + " migration", STATE_OK, f"user text preserved; backup: {backup}")
        return True

    if previous.get("path") != str(destination):
        reporter.add(component, STATE_CONFLICT, "manifest points to a different destination")
        return False
    if start < 0 or end < start:
        reporter.add(component, STATE_CONFLICT, "managed AGENTS block is missing or malformed; user file preserved")
        return False
    end_pos = end + len(AGENTS_BLOCK_END)
    current_block = (text[start:end_pos] + "\n").encode("utf-8")
    previous_block_hash = previous.get("block_sha256")
    if previous_block_hash and sha256_bytes(current_block) != previous_block_hash:
        if not force:
            reporter.add(component, STATE_CONFLICT, "managed AGENTS block was modified locally; surrounding user text preserved")
            return False
        if check:
            reporter.add(component, STATE_CONFLICT, "managed AGENTS block modified; --force would backup and replace only the block")
            return False

    if current_block == desired_block:
        reporter.add(component, STATE_OK, "managed block current; surrounding user text preserved")
        if not check and previous.get("sha256") != sha256_bytes(current):
            previous["sha256"] = sha256_bytes(current)
            return True
        return False

    reporter.add(component, STATE_OUTDATED, "managed AGENTS block source changed")
    if check:
        return False
    backup = backup_file(destination, state_dir, component)
    updated_text = text[:start] + desired_text.rstrip("\n") + text[end_pos:]
    if not updated_text.endswith("\n"):
        updated_text += "\n"
    updated = updated_text.encode("utf-8")
    atomic_write(destination, updated)
    managed[component] = {"path": str(destination), "sha256": sha256_bytes(updated),
                          "source": source_label, "mode": "block",
                          "block_sha256": sha256_bytes(desired_block)}
    reporter.add(component + " migration", STATE_OK, f"only managed block replaced; backup: {backup}")
    return True


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
    normalized = path.replace("\\", "/").lstrip("./")
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
    reporter.add(component, state, detail)
    if check:
        return state in {STATE_OK, STATE_OUTDATED}, state
    if state == STATE_MISSING:
        path.parent.mkdir(parents=True, exist_ok=True)
        cp = run(["git", "clone", "--branch", branch, "--single-branch", url, str(path)])
        if cp.returncode != 0:
            reporter.add(component + " clone", STATE_CONFLICT, cp.stderr.strip()[-400:])
            return False, STATE_CONFLICT
        state, detail = inspect_repo(path, url, branch)
        if state not in {STATE_OK, STATE_OUTDATED}:
            reporter.add(component + " clone", STATE_CONFLICT, detail)
            return False, STATE_CONFLICT
    if state == STATE_OUTDATED:
        cp = run(["git", "pull", "--ff-only", "origin", branch], cwd=path)
        if cp.returncode != 0:
            reporter.add(component + " update", STATE_CONFLICT, "fast-forward update failed; repository preserved")
            return False, STATE_CONFLICT
        state, detail = inspect_repo(path, url, branch)
        if state != STATE_OK:
            reporter.add(component + " update", STATE_CONFLICT, detail)
            return False, STATE_CONFLICT
    return state == STATE_OK, state
