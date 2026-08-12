"""File ownership, skill validation, and dependency-repository reconciliation."""
from __future__ import annotations

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
        return STATE_CONFLICT, f"expected clean branch {branch!r}"
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
    dirty = run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=path, env=env)
    if dirty.returncode != 0:
        return STATE_CONFLICT, "git status failed"
    if dirty.stdout.strip():
        return STATE_CONFLICT, "working copy has local/untracked changes; no reset/clean performed"
    head = run(["git", "rev-parse", "HEAD"], cwd=path)
    remote = run(["git", "ls-remote", "--exit-code", "origin", f"refs/heads/{branch}"], cwd=path)
    if head.returncode != 0:
        return STATE_CONFLICT, "cannot resolve local HEAD"
    if remote.returncode != 0 or not remote.stdout.strip():
        return STATE_CONFLICT, "clean working copy, but origin cannot be queried safely"
    remote_sha = remote.stdout.split()[0]
    if head.stdout.strip() == remote_sha:
        return STATE_OK, head.stdout.strip()[:12]
    return STATE_OUTDATED, f"local {head.stdout.strip()[:12]} != origin {remote_sha[:12]}"


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
