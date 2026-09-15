"""Owned PATH activation for agent-toolchain public commands."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from setup_lib import Reporter, STATE_CONFIGURED, STATE_FAILED, STATE_INFO, STATE_OK, STATE_OUTDATED
from setup_managed_tools import public_bin_dir

_RECORD_KEY = "agent-toolchain-bin"


class PathOwnershipError(RuntimeError):
    """Raised when a targeted PATH mutation cannot prove exact ownership/scope."""



def platform_name() -> str:
    return "windows" if os.name == "nt" else "linux"


def _normalized(value: str) -> str:
    expanded = os.path.expandvars(value.strip().strip('"'))
    return os.path.normcase(os.path.normpath(expanded)).rstrip("\\/")


def _split(value: str) -> list[str]:
    return [item for item in value.split(";") if item.strip()]


def _read_user_path() -> tuple[str, int]:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_QUERY_VALUE) as key:
            value, kind = winreg.QueryValueEx(key, "Path")
            return str(value or ""), int(kind)
    except FileNotFoundError:
        return "", winreg.REG_EXPAND_SZ


def _write_user_path(value: str, kind: int) -> None:
    import winreg

    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, "Path", 0, kind, value)
    # The registry is authoritative for future processes. Best-effort broadcast only.
    try:
        import ctypes

        result = ctypes.c_ulong()
        ctypes.windll.user32.SendMessageTimeoutW(
            0xFFFF, 0x001A, 0, "Environment", 0x0002, 5000, ctypes.byref(result)
        )
    except Exception:
        pass


def _owned_record(manifest: dict[str, Any], desired: Path) -> bool:
    records = manifest.setdefault("managed_path_entries", {})
    record = records.get(_RECORD_KEY)
    return (
        isinstance(record, dict)
        and record.get("owner") == "agent-toolchain"
        and record.get("scope") == "user"
        and isinstance(record.get("path"), str)
        and _normalized(record["path"]) == _normalized(str(desired))
    )


def _record(manifest: dict[str, Any], desired: Path) -> None:
    manifest.setdefault("managed_path_entries", {})[_RECORD_KEY] = {
        "owner": "agent-toolchain",
        "scope": "user",
        "path": str(desired),
    }


def _process_path_has(desired: Path) -> bool:
    current = os.environ.get("PATH", "")
    return any(_normalized(item) == _normalized(str(desired)) for item in _split(current))


def _ensure_process_path(desired: Path) -> None:
    if os.name != "nt":
        return
    current = os.environ.get("PATH", "")
    if not _process_path_has(desired):
        os.environ["PATH"] = (current.rstrip(";") + ";" if current else "") + str(desired)


def _unique_index(entries: list[str], target: Path, *, label: str) -> int:
    positions = [
        index
        for index, item in enumerate(entries)
        if _normalized(item) == _normalized(str(target))
    ]
    if len(positions) != 1:
        raise PathOwnershipError(
            f"{label} must appear exactly once in PATH, found {len(positions)}: {target}"
        )
    return positions[0]


def _move_entry_before(entries: list[str], desired: Path, reference: Path) -> tuple[list[str], bool]:
    desired_index = _unique_index(entries, desired, label="managed agent-toolchain bin")
    reference_index = _unique_index(entries, reference, label="legacy guard bin")
    if desired_index < reference_index:
        return list(entries), False

    reordered = list(entries)
    desired_raw = reordered.pop(desired_index)
    reference_index = _unique_index(reordered, reference, label="legacy guard bin")
    reordered.insert(reference_index, desired_raw)
    return reordered, True


def promote_owned_public_bin_before(manifest: dict[str, Any], reference: Path) -> bool:
    """Promote the owned managed bin before one exact Windows PATH entry.

    This is intentionally narrower than the generic PATH reconciler. It may be
    used by an ownership-aware migration only after the caller has identified
    the exact legacy entry that currently shadows a managed command.
    """
    if platform_name() != "windows":
        raise PathOwnershipError("targeted PATH promotion is supported only on Windows")

    desired = public_bin_dir()
    reference = Path(reference).resolve()
    if _normalized(str(desired)) == _normalized(str(reference)):
        raise PathOwnershipError("managed and reference PATH entries must differ")
    if not _owned_record(manifest, desired):
        raise PathOwnershipError(
            f"managed PATH entry is not proven agent-toolchain-owned: {desired}"
        )

    try:
        user_path, kind = _read_user_path()
    except OSError as exc:
        raise PathOwnershipError(f"cannot read Windows user PATH: {exc}") from exc

    user_entries = _split(user_path)
    reordered_user, user_changed = _move_entry_before(user_entries, desired, reference)
    if user_changed:
        try:
            _write_user_path(";".join(reordered_user), kind)
        except OSError as exc:
            raise PathOwnershipError(f"cannot update Windows user PATH: {exc}") from exc

    process_entries = _split(os.environ.get("PATH", ""))
    reordered_process, process_changed = _move_entry_before(process_entries, desired, reference)
    if process_changed:
        os.environ["PATH"] = ";".join(reordered_process)

    return user_changed or process_changed


def _stale_session_detail(desired: Path) -> str:
    return (
        f"owned user PATH entry is configured in the registry: {desired}; current process PATH does not include it. "
        "MANUAL ACTION REQUIRED: restart the parent terminal application (for example Far Manager/ConEmu) "
        "or sign out/in before using bare managed commands. The running toolchainctl process can activate "
        "the entry only for its own child processes."
    )


def reconcile_public_bin_path(
    manifest: dict[str, Any],
    reporter: Reporter,
    *,
    check: bool,
) -> bool:
    desired = public_bin_dir()
    if platform_name() != "windows":
        entries = [item for item in os.environ.get("PATH", "").split(os.pathsep) if item]
        if any(_normalized(item) == _normalized(str(desired)) for item in entries):
            reporter.add("agent-toolchain PATH", STATE_OK, str(desired))
        else:
            reporter.add(
                "agent-toolchain PATH",
                STATE_OUTDATED,
                f"MANUAL ACTION REQUIRED: add {desired} to PATH using the user's shell/profile policy; agent-toolchain does not edit unknown Linux shell startup files automatically",
            )
        return False

    try:
        user_path, kind = _read_user_path()
    except OSError as exc:
        reporter.add("agent-toolchain PATH", STATE_FAILED, f"cannot read Windows user PATH: {exc}")
        return False
    entries = _split(user_path)
    present = any(_normalized(item) == _normalized(str(desired)) for item in entries)
    owned = _owned_record(manifest, desired)

    if present:
        process_present = _process_path_has(desired)
        if not process_present and owned:
            # Never hide a stale inherited shell during check. Apply may activate its own
            # process so later reconciliation can resolve managed commands, but that cannot
            # modify the already-running parent terminal environment.
            if not check:
                _ensure_process_path(desired)
            reporter.add("agent-toolchain PATH", STATE_OUTDATED, _stale_session_detail(desired))
            return False
        if owned:
            reporter.add("agent-toolchain PATH", STATE_OK, f"owned user PATH entry: {desired}")
        else:
            reporter.add(
                "agent-toolchain PATH",
                STATE_INFO,
                f"pre-existing user PATH entry is usable but not claimed as agent-toolchain-owned: {desired}",
            )
        return False

    if check:
        reporter.add(
            "agent-toolchain PATH",
            STATE_OUTDATED,
            f"toolchainctl apply will append the managed user PATH entry without reordering existing entries: {desired}",
        )
        return False

    new_value = (user_path.rstrip(";") + ";" if user_path else "") + str(desired)
    try:
        _write_user_path(new_value, kind)
    except OSError as exc:
        reporter.add("agent-toolchain PATH", STATE_FAILED, f"cannot update Windows user PATH: {exc}")
        return False
    _ensure_process_path(desired)
    _record(manifest, desired)
    reporter.add(
        "agent-toolchain PATH",
        STATE_CONFIGURED,
        f"appended owned user PATH entry: {desired}; restart the parent terminal application if it keeps an older inherited PATH",
    )
    return True
