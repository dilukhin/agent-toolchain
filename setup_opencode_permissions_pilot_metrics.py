"""Read-only aggregation of privacy-bounded P0 process snapshots."""
from __future__ import annotations

import json
import re
import stat
from pathlib import Path

COUNTERS = frozenset({
    "native_ask", "classifier_allow", "classifier_deny", "residual_ask",
    "classifier_error/fail_closed", "binding_reject",
})
FIELDS = frozenset({
    "schema", "scope", "opencode_version", "compatibility_profile",
    "native_policy_artifact_id", "pilot_artifact_id", "classifier_profile",
    "counters", "reasons", "families", "updated_at",
})
IDENTITY = (
    "opencode_version", "compatibility_profile", "native_policy_artifact_id",
    "pilot_artifact_id", "classifier_profile",
)
BUCKET = re.compile(r"[A-Za-z0-9_./:-]{1,96}\Z")
PROCESS = re.compile(r"process-[0-9]+\.json\Z")
ARTIFACT = re.compile(r"sha256:[0-9a-f]{64}\Z")


class MetricsConflict(ValueError):
    pass


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise MetricsConflict(code)


def _nonnegative_int(value: object) -> bool:
    return type(value) is int and value >= 0


def _buckets(value: object) -> dict[str, int]:
    _require(isinstance(value, dict) and len(value) <= 64, "METRICS_BUCKETS_INVALID")
    _require(all(isinstance(k, str) and BUCKET.fullmatch(k) and _nonnegative_int(v)
                 for k, v in value.items()), "METRICS_BUCKET_INVALID")
    return value


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        _require(key not in result, "METRICS_DUPLICATE_KEY")
        result[key] = value
    return result


def active_artifact(state_dir: Path) -> tuple[str, str, str] | None:
    """Resolve only our canonical ownership record; no caller-supplied path."""
    state_dir = Path(state_dir)
    _require(not state_dir.is_symlink(), "METRICS_STATE_CONFLICT")
    path = state_dir / "opencode-permissions-pilot.json"
    if not path.exists() and not path.is_symlink():
        return None
    _require(path.is_file() and not path.is_symlink(), "METRICS_STATE_CONFLICT")
    _require(path.stat().st_size <= 1024 * 1024, "METRICS_STATE_INVALID")
    try:
        state = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_pairs)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MetricsConflict("METRICS_STATE_INVALID") from exc
    _require(isinstance(state, dict) and state.get("schema") == 1
             and state.get("owner") == "agent-toolchain"
             and state.get("component") == "opencode-permissions-pilot"
             and state.get("phase") in {"active", "prepared"}, "METRICS_STATE_INVALID")
    artifact = state.get("artifact_id")
    native = state.get("native_artifact_id")
    version = state.get("opencode_version")
    _require(isinstance(artifact, str) and bool(ARTIFACT.fullmatch(artifact))
             and isinstance(native, str) and bool(ARTIFACT.fullmatch(native))
             and isinstance(version, str) and bool(re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version)),
             "METRICS_STATE_INVALID")
    return artifact, version, native


def read_metrics(
    *,
    home: Path,
    artifact_id: str,
    expected_version: str | None = None,
    expected_native_id: str | None = None,
) -> dict[str, object]:
    """Read current snapshots once; never create directories or update state."""
    _require(isinstance(artifact_id, str) and bool(ARTIFACT.fullmatch(artifact_id)),
             "METRICS_ARTIFACT_INVALID")
    root = Path(home).resolve()
    parts = (".local", "state", "opencode_permissions", "p0-metrics",
             "sha256-" + artifact_id.split(":", 1)[1])
    directory = root
    for part in parts:
        directory = directory / part
        if not directory.exists() and not directory.is_symlink():
            return {"status": "no_data", "artifact_id": artifact_id}
        _require(directory.is_dir() and not directory.is_symlink(),
                 "METRICS_DIRECTORY_CONFLICT")
    _require(stat.S_IMODE(directory.stat().st_mode) == 0o700, "METRICS_DIRECTORY_MODE")

    totals = {name: 0 for name in COUNTERS}
    reasons: dict[str, int] = {}
    families: dict[str, int] = {}
    identity: dict[str, str] | None = None
    count = 0
    for path in sorted(directory.iterdir()):
        _require(bool(PROCESS.fullmatch(path.name)), "METRICS_FILE_UNKNOWN")
        _require(path.is_file() and not path.is_symlink(), "METRICS_FILE_CONFLICT")
        _require(stat.S_IMODE(path.stat().st_mode) == 0o600, "METRICS_FILE_MODE")
        _require(path.stat().st_size <= 1024 * 1024, "METRICS_FILE_TOO_LARGE")
        try:
            data = json.loads(path.read_text(encoding="utf-8"),
                              object_pairs_hook=_unique_pairs)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise MetricsConflict("METRICS_FILE_INVALID") from exc
        _require(isinstance(data, dict) and set(data) == FIELDS, "METRICS_SCHEMA_INVALID")
        _require(data["schema"] == "opencode-permissions-p0-metrics/v1"
                 and data["scope"] == "ask_path", "METRICS_SCHEMA_INVALID")
        _require(isinstance(data["updated_at"], str) and
                 len(data["updated_at"]) <= 64, "METRICS_TIMESTAMP_INVALID")
        current = {name: data[name] for name in IDENTITY}
        _require(all(isinstance(value, str) and 0 < len(value) <= 160
                     for value in current.values()), "METRICS_IDENTITY_INVALID")
        _require(current["pilot_artifact_id"] == artifact_id, "METRICS_ARTIFACT_MISMATCH")
        if expected_version is not None:
            _require(current["opencode_version"] == expected_version, "METRICS_VERSION_MISMATCH")
        if expected_native_id is not None:
            _require(current["native_policy_artifact_id"] == expected_native_id,
                     "METRICS_NATIVE_MISMATCH")
        if identity is None:
            identity = current
        _require(identity == current, "METRICS_MIXED_PROFILE")
        counters = data["counters"]
        _require(isinstance(counters, dict) and set(counters) == COUNTERS
                 and all(_nonnegative_int(value) for value in counters.values()),
                 "METRICS_COUNTERS_INVALID")
        _require(counters["residual_ask"] <= counters["native_ask"]
                 and counters["classifier_allow"] <= counters["native_ask"],
                 "METRICS_COUNTERS_INVALID")
        for key in COUNTERS:
            totals[key] += counters[key]
        for key, value in _buckets(data["reasons"]).items():
            reasons[key] = reasons.get(key, 0) + value
        for key, value in _buckets(data["families"]).items():
            families[key] = families.get(key, 0) + value
        _require(len(reasons) <= 64 and len(families) <= 64, "METRICS_MIXED_BUCKETS")
        count += 1
    if count == 0:
        return {"status": "no_data", "artifact_id": artifact_id}
    assert identity is not None
    denominator = totals["native_ask"]
    return {
        "status": "snapshots", "identity": identity, "snapshot_count": count,
        "counters": totals, "reasons": reasons, "families": families,
        "prompt_reduction_ratio": ((denominator - totals["residual_ask"]) / denominator
                                   if denominator else None),
        "note": "Current per-process snapshots only; PID reuse may replace history.",
    }
