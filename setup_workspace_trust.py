"""Explicit workspace trust storage. No authorization decisions or implicit grants."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import stat
import sys
import tempfile

from setup_manifest import load_manifest
from toolchain_state import canonical_state_dir, default_state_dir
from workspace_trust_contract import (
    SCHEMA, TRUST_CLASS, SUPPORTED_SCOPES, validate_workspace_identity,
    validate_workspace_trust_fact, match_workspace_trust_fact,
)

REGISTRY_SCHEMA = "workspace-trust-registry/v1"
REGISTRY_NAME = "workspace-trust.json"
MAX_REGISTRY_BYTES = 1024 * 1024


class TrustConflict(ValueError):
    pass


def _platform() -> str:
    if os.name == "nt":
        return "windows"
    if sys.platform.startswith("linux"):
        return "linux"
    raise TrustConflict("Workspace trust supports Windows and Linux only")


def _requested_root(value: str) -> str:
    if not isinstance(value, str) or not value or "\0" in value or not Path(value).is_absolute():
        raise TrustConflict("Workspace root must be an explicit absolute path")
    return value  # Preserve spelling, case, Unicode and separators exactly.


def observe_workspace(root: str) -> dict[str, str]:
    requested = _requested_root(root)
    path = Path(requested)
    before = path.stat()
    resolved = path.resolve(strict=True)
    actual = resolved.stat()
    after = path.stat()
    if not all(stat.S_ISDIR(s.st_mode) and s.st_ino != 0 for s in (before, actual, after)):
        raise TrustConflict("Workspace must be a directory with available object identity")
    if len({(s.st_dev, s.st_ino) for s in (before, actual, after)}) != 1 or path.resolve(strict=True) != resolved:
        raise TrustConflict("Workspace changed during observation")
    platform = _platform()
    return validate_workspace_identity(dict(
        platform=platform, requested_root=requested, resolved_root=str(resolved),
        object_identity=f"{platform}:dev={actual.st_dev}:ino={actual.st_ino}",
    ))


def _plain_stat(path: Path, *, directory: bool = False):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise TrustConflict(f"Linked/reparse state object is not supported: {path}")
    if directory:
        valid = stat.S_ISDIR(info.st_mode)
    else:
        valid = stat.S_ISREG(info.st_mode) and info.st_nlink == 1
    if not valid:
        raise TrustConflict(f"Unexpected state object: {path}")
    return info


def _owned_state(state: Path, *, required: bool = False) -> bool:
    try:
        _plain_stat(state, directory=True)
    except FileNotFoundError:
        if required:
            raise TrustConflict("Canonical state is absent; run toolchainctl apply first")
        return False
    try:
        _plain_stat(state / "manifest.json")
    except FileNotFoundError as exc:
        raise TrustConflict("State has no ownership manifest; run toolchainctl apply first") from exc
    _, error, _ = load_manifest(state / "manifest.json")
    if error:
        raise TrustConflict("Invalid state ownership manifest: " + error)
    return True


def _outside_workspace(state: Path, workspace: dict[str, str]) -> None:
    # Never put authoritative state inside the workspace being trusted.
    resolved_state = state.resolve()
    for field in ("requested_root", "resolved_root"):
        if resolved_state.is_relative_to(Path(workspace[field]).resolve()):
            raise TrustConflict("Trust state must be outside the workspace")


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise TrustConflict("Duplicate JSON member in registry")
        result[key] = value
    return result


def _workspace_key(workspace):
    return tuple(workspace[key] for key in ("platform", "requested_root", "resolved_root", "object_identity"))


def validate_registry(data) -> dict:
    if not isinstance(data, dict) or set(data) != {"schema", "entries"} or data["schema"] != REGISTRY_SCHEMA:
        raise TrustConflict("Unknown workspace trust registry schema/shape")
    if not isinstance(data["entries"], list):
        raise TrustConflict("Registry entries must be an array")
    entries, seen = [], set()
    try:
        for raw in data["entries"]:
            entry = validate_workspace_trust_fact(raw)
            key = _workspace_key(entry["workspace"])
            if key in seen:
                raise TrustConflict("Duplicate exact workspace entry")
            seen.add(key)
            entries.append(entry)
    except (ValueError, TypeError) as exc:
        raise TrustConflict(str(exc)) from exc
    return {"schema": REGISTRY_SCHEMA, "entries": sorted(entries, key=lambda e: _workspace_key(e["workspace"]))}


def _read_registry(state: Path) -> tuple[dict, bytes | None]:
    empty = {"schema": REGISTRY_SCHEMA, "entries": []}
    if not _owned_state(state):
        return empty, None
    path = state / REGISTRY_NAME
    try:
        info = _plain_stat(path)
    except FileNotFoundError:
        return empty, None
    if info.st_size > MAX_REGISTRY_BYTES:
        raise TrustConflict("Registry exceeds size limit")
    with path.open("rb") as stream:
        raw = stream.read(MAX_REGISTRY_BYTES + 1)
    if len(raw) > MAX_REGISTRY_BYTES:
        raise TrustConflict("Registry exceeds size limit")
    try:
        return validate_registry(json.loads(raw.decode("utf-8"), object_pairs_hook=_json_object)), raw
    except (ValueError, TypeError, RecursionError) as exc:
        raise TrustConflict("Invalid workspace trust registry: " + str(exc)) from exc


@contextmanager
def _writer_lock(state: Path):
    _owned_state(state, required=True)
    lock = state / (REGISTRY_NAME + ".lock")
    try:
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise TrustConflict("Trust writer lock already exists; no automatic lock repair") from exc
    identity = os.fstat(fd)
    os.close(fd)
    try:
        yield
    finally:
        # Remove only the lock created by this writer, never a replacement object.
        try:
            current = lock.lstat()
            if (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
                lock.unlink()
        except FileNotFoundError:
            pass


def _publish(state: Path, desired: dict, before: bytes | None) -> None:
    desired = validate_registry(desired)
    payload = (json.dumps(desired, ensure_ascii=True, sort_keys=True, indent=2) + "\n").encode("utf-8")
    if len(payload) > MAX_REGISTRY_BYTES:
        raise TrustConflict("Registry exceeds size limit")
    fd, name = tempfile.mkstemp(prefix=".workspace-trust-", suffix=".tmp", dir=state)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if _read_registry(state)[1] != before:
            raise TrustConflict("Registry changed concurrently; refusing replacement")
        os.replace(temporary, state / REGISTRY_NAME)
        actual, raw = _read_registry(state)
        if actual != desired or raw != payload:
            raise TrustConflict("Registry read-back differs; inspect state before retrying")
    finally:
        temporary.unlink(missing_ok=True)


def _change(state: Path, action: str, root: str, scopes: list[str] | None = None) -> bool:
    requested = _requested_root(root)
    platform = _platform()
    workspace = None if action == "remove" else observe_workspace(requested)
    if workspace is not None:
        _outside_workspace(state, workspace)
        desired_entry = validate_workspace_trust_fact(dict(
            schema=SCHEMA, trust_class=TRUST_CLASS, workspace=workspace, scopes=scopes,
        ))
    with _writer_lock(state):
        current, before = _read_registry(state)
        entries = current["entries"]
        if action == "remove":
            # Revoke by exact requested spelling, also for missing/stale directories.
            kept = [e for e in entries if not (e["workspace"]["platform"] == platform and e["workspace"]["requested_root"] == requested)]
            if len(kept) == len(entries):
                return False
        else:
            if observe_workspace(requested) != workspace:
                raise TrustConflict("Workspace changed before matching")
            matching = next((e for e in entries if e["workspace"] == workspace), None)
            if matching is not None and matching["scopes"] == desired_entry["scopes"]:
                return False
            if action == "add" and matching is not None:
                raise TrustConflict("Scopes differ; use explicit workspace-trust update")
            if action == "update" and matching is None:
                raise TrustConflict("No exact current entry; use explicit workspace-trust add")
            if action not in {"add", "update"}:
                raise TrustConflict("Unknown trust mutation")
            kept = [e for e in entries if not (e["workspace"]["platform"] == platform and e["workspace"]["requested_root"] == requested)]
            kept.append(desired_entry)
            if observe_workspace(requested) != workspace:
                raise TrustConflict("Workspace changed before publication")
        _publish(state, {"schema": REGISTRY_SCHEMA, "entries": kept}, before)
        return True


class WorkspaceTrustProvider:
    """Read-only provider. Construct in trusted setup context, never with model env.

    OS user-location environment belongs to setup. The CLI fixture override and
    caller-provided registry paths are not supported. A consumer must still supply
    genuinely observed identity; this class does not authenticate arbitrary dicts.
    """
    def __init__(self):
        self._state = canonical_state_dir()

    def lookup(self, observed_workspace_identity) -> dict | None:
        try:
            observed = validate_workspace_identity(observed_workspace_identity)
            if observed["platform"] != _platform():
                return None
            _outside_workspace(self._state, observed)
            # Re-observe to invalidate replaced/disappeared objects, even for a stale caller.
            if observe_workspace(observed["requested_root"]) != observed:
                return None
            registry, _ = _read_registry(self._state)
            return next((entry for entry in registry["entries"]
                         if match_workspace_trust_fact(entry, observed)["matched"]), None)
        except (OSError, ValueError, TypeError, RuntimeError):
            return None


def add_cli_parser(sub) -> None:
    parser = sub.add_parser("workspace-trust", help="explicit persistent trust for development workspaces")
    commands = parser.add_subparsers(dest="trust_command", required=True)
    commands.add_parser("list", help="read registry as JSON without changing state")
    for action in ("add", "update", "remove"):
        cmd = commands.add_parser(action, help=f"explicitly {action} persistent workspace trust")
        cmd.add_argument("path", help="exact absolute workspace path")
        if action != "remove":
            cmd.add_argument("--scope", action="append", required=True, choices=sorted(SUPPORTED_SCOPES))


def run_cli(args) -> int:
    try:
        state = default_state_dir(resolve_override=False)
        if args.trust_command == "list":
            print(json.dumps(_read_registry(state)[0], ensure_ascii=True, sort_keys=True, indent=2))
        else:
            changed = _change(state, args.trust_command, args.path, getattr(args, "scope", None))
            print("configured" if changed else "up-to-date")
        return 0
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        print(f"modified/conflict  workspace-trust  {exc}", file=sys.stderr)
        return 2
