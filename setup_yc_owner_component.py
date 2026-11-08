"""Pinned inactive YC59 component staging. No owner activation or YC execution."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import urllib.request

import bootstrap_core
import yc_owner_client

SOURCE_REF = "1f7d89d9a8cd2352c6df865e8915f66171475972"
REPOSITORY = "dilukhin/opencode_permissions"
DOMAIN = b"opencode_permissions.yc_owner_component.v1\n"
MANIFEST = json.loads('{"artifact_id":"sha256:96ab79c0779f4804f63ebcc7e505fff8750657afa08965969bfabbda16db6c29","execution_enabled":false,"files":[{"path":"policy/native/rules.v1.json","sha256":"b7af96ecefd9fce341d128073f1bccae8b66c72e544bf0f47ccbb024232c3490","size":10650},{"path":"policy/yc/yc_policy.v1.json","sha256":"c4f8fba27d4c47118be4da3cd15ae06bd30520de6f95d827783b0d293e52cd2d","size":2063},{"path":"runtime/approval_owner/console.py","sha256":"a07d8aaa8a87f650e4f83e19f1aa7a00bb78f22843603bcd2c7373e884b59bd6","size":3252},{"path":"runtime/approval_owner/entry.py","sha256":"faafa5447211861f8f5ba3c8ca0dbf7fce9b2822e3a9b04630befc8d594d3335","size":5822},{"path":"runtime/approval_owner/ledger.py","sha256":"99b9e7fb61f3b4ebb2fe1c2938cf09fa64a4c3e98b46e1f7a4f3068bf40c70ad","size":7175},{"path":"runtime/approval_owner/read_host.py","sha256":"ab652a2a760ffb7773524006f53793b69438cf2d235011c7b529c8d58a75ab19","size":5720},{"path":"runtime/native_policy_matcher.py","sha256":"e70b27f110ba729d8bbc32c0dba0a62caea1a48cb53712cefbe79b497f965477","size":999},{"path":"runtime/render_native_policy.py","sha256":"f519d74ae76eb8a5a32af97230fcc007fa7955a3df9724e0588abfc046e415dd","size":5189},{"path":"runtime/windows_owner_boundary.py","sha256":"093843f9d9f20dfc8cef0be3d0448f734c867cc8c04e76e47dbb92a2075ca86a","size":7702},{"path":"runtime/windows_owner_pipe.py","sha256":"02e0ecf8f442b3207b054bdb47943e6dc7ff88e03596fd293f7c26770703a859","size":17687},{"path":"runtime/yc_owner_native_veto.py","sha256":"dd8187e00fc60ea54800e45f6c2c73edcdc70c598cff2c207d3142246f4bf706","size":4704},{"path":"runtime/yc_owner_windows_context.py","sha256":"a9a98ea2db26ab0d091ed36d9b1ac213b7b92851d9d08c1577272564c7f2349a","size":9314},{"path":"runtime/yc_policy_veto.py","sha256":"6fa913ce712a54fe0f02d7e5cb1314906936ed4f66ca698af5017080d6cda1f5","size":1545},{"path":"runtime/yc_read_capture.py","sha256":"692f8ac80220b2c68cc753e0d61556dbd04d9a89db0a6fa94c3e779f6d51487f","size":3919},{"path":"runtime/yc_read_projection.py","sha256":"a756d5906674b569a6b26c62187ebd4261890eacb3c6543f58cfe3207c9be983","size":8481},{"path":"runtime/yc_snapshot_read.py","sha256":"fcba2888ec9fd40b588945fdbfba33481c00b9b2b89a0acba72c3d3ae17c9def","size":8965}],"owner":"dilukhin/opencode_permissions","platform":"windows","profile":"yc-snapshot-read/v1","required_before_activation":["native_bootstrap_and_file_protection","live_native_veto_authority","trusted_read_context_and_spawn","managed_activation_and_installed_acceptance"],"schema":"yc-owner-component/v1","status":"library_only"}')
SOURCES = {
    "policy/native/rules.v1.json": "policy/native/rules.v1.json",
    "policy/yc/yc_policy.v1.json": "policy/yc/yc_policy.v1.json",
    "runtime/approval_owner/console.py": "runtime/approval_owner/console.py",
    "runtime/approval_owner/entry.py": "runtime/approval_owner/entry.py",
    "runtime/approval_owner/ledger.py": "runtime/approval_owner/ledger.py",
    "runtime/approval_owner/read_host.py": "runtime/approval_owner/read_host.py",
    "runtime/native_policy_matcher.py": "tools/native_policy_matcher.py",
    "runtime/render_native_policy.py": "tools/render_native_policy.py",
    "runtime/windows_owner_boundary.py": "tools/windows_owner_boundary.py",
    "runtime/windows_owner_pipe.py": "tools/windows_owner_pipe.py",
    "runtime/yc_owner_native_veto.py": "tools/yc_owner_native_veto.py",
    "runtime/yc_owner_windows_context.py": "tools/yc_owner_windows_context.py",
    "runtime/yc_policy_veto.py": "tools/yc_policy_veto.py",
    "runtime/yc_read_capture.py": "tools/yc_read_capture.py",
    "runtime/yc_read_projection.py": "tools/yc_read_projection.py",
    "runtime/yc_snapshot_read.py": "tools/yc_snapshot_read.py"
}
ROOT_MARKER = b'agent-toolchain:yc-owner-inactive-staging:v1\n'
LOCK_MARKER = b'agent-toolchain:yc-owner-staging-lock:v1\n'


class ComponentConflict(RuntimeError):
    pass


def require(ok, code):
    if not ok:
        raise ComponentConflict(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode("ascii")


def plan():
    return {"artifact_id": MANIFEST["artifact_id"], "source_ref": SOURCE_REF,
            "status": "library_only", "execution_enabled": False,
            "trusted_placement_proven": False, "native_sources_proven": False,
            "required_before_activation": list(MANIFEST["required_before_activation"])}


def _ordinary(path, *, directory=False):
    require(not path.is_symlink() and (path.is_dir() if directory else path.is_file()), "YC_OWNER_PATH_CONFLICT")
    stat = path.stat()
    require(not (getattr(stat, "st_file_attributes", 0) & 0x400), "YC_OWNER_REPARSE_POINT")


def _ancestors(path):
    for parent in (path, *path.parents):
        if parent.exists() or parent.is_symlink():
            _ordinary(parent, directory=True)


def _root(data):
    # Versioned staging keeps previous verified bytes/journals untouched.
    path = Path(data).absolute() / "tools" / "yc-owner-inactive" / "v2" / MANIFEST["artifact_id"][7:23]
    require(".." not in path.parts, "YC_OWNER_PATH_CONFLICT")
    _ancestors(path)
    return path


def validate(package):
    package = Path(package)
    _ordinary(package, directory=True)
    _ancestors(package)
    expected = {row["path"]: row for row in MANIFEST["files"]}
    core = {k: v for k, v in MANIFEST.items() if k != "artifact_id"}
    require("sha256:" + hashlib.sha256(DOMAIN + canonical(core)).hexdigest() == MANIFEST["artifact_id"], "YC_OWNER_REVIEWED_PIN_INVALID")
    found = set()
    for path in package.rglob("*"):
        if path.is_dir():
            _ordinary(path, directory=True)
            continue
        _ordinary(path)
        rel = path.relative_to(package).as_posix()
        require(rel in expected or rel == "manifest.json", "YC_OWNER_UNREVIEWED_FILE")
        if rel == "manifest.json":
            require(path.stat().st_size == len(canonical(MANIFEST)) + 1, "YC_OWNER_MANIFEST_MISMATCH")
            require(path.read_bytes() == canonical(MANIFEST) + b"\n", "YC_OWNER_MANIFEST_MISMATCH")
        else:
            require(path.stat().st_size == expected[rel]["size"], "YC_OWNER_FILE_SIZE_MISMATCH")
            require(hashlib.sha256(path.read_bytes()).hexdigest() == expected[rel]["sha256"], "YC_OWNER_FILE_DIGEST_MISMATCH")
        found.add(rel)
    require(found == set(expected) | {"manifest.json"}, "YC_OWNER_FILESET_MISMATCH")
    return plan()


def _journal(root):
    return root / "stage-journal.json"


def _read_root(root):
    _ordinary(root, directory=True)
    marker = root / ".owner"
    _ordinary(marker)
    require(marker.read_bytes() == ROOT_MARKER, "YC_OWNER_STAGING_UNOWNED")


def check(data):
    root = _root(data)
    if not root.exists():
        return {**plan(), "status": "not_staged"}
    _read_root(root)
    journal = _journal(root)
    _ordinary(journal)
    require(journal.stat().st_size <= 4096, "YC_OWNER_STAGE_JOURNAL_INVALID")
    observed = journal.read_bytes()
    expected = {"schema": 1, "component": "yc-owner-inactive", "source_ref": SOURCE_REF,
                "artifact_id": MANIFEST["artifact_id"], "phase": "complete"}
    require(observed == canonical(expected) + b"\n", "YC_OWNER_STAGING_PENDING_OR_CONFLICT")
    package = root / MANIFEST["artifact_id"].replace(":", "-", 1)
    validate(package)
    return {**plan(), "status": "staged_verified"}


@contextmanager
def _lock(root):
    path = root / "stage.lock"
    flags = os.O_RDWR | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        _ordinary(path)
        fd = os.open(path, flags)
    else:
        os.write(fd, LOCK_MARKER)
        os.fsync(fd)
    acquired = False
    try:
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        acquired = True
        os.lseek(fd, 0, os.SEEK_SET)
        require(os.read(fd, len(LOCK_MARKER) + 1) == LOCK_MARKER, "YC_OWNER_LOCK_UNOWNED")
        yield
    except OSError:
        raise ComponentConflict("YC_OWNER_STAGE_LOCKED_OR_IO_FAILED") from None
    finally:
        if acquired:
            if os.name == "nt":
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _download(source, limit, deadline):
    require(time.monotonic() < deadline, "YC_OWNER_DOWNLOAD_TIMEOUT")
    request = urllib.request.Request(f"https://raw.githubusercontent.com/{REPOSITORY}/{SOURCE_REF}/{source}",
                                     headers={"User-Agent": "agent-toolchain-yc-owner/1"})
    with urllib.request.urlopen(request, timeout=min(10, deadline - time.monotonic())) as response:
        raw = bytearray()
        while len(raw) <= limit:
            require(time.monotonic() < deadline, "YC_OWNER_DOWNLOAD_TIMEOUT")
            chunk = response.read1(min(4096, limit + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
    require(len(raw) <= limit and time.monotonic() <= deadline, "YC_OWNER_DOWNLOAD_LIMIT")
    return bytes(raw)


def stage(data, *, fetch=_download):
    root = _root(data)
    if not root.exists():
        # Only this fresh staging subdirectory is owned. Never adopt an existing one.
        root.parent.mkdir(parents=True, exist_ok=True)
        root.mkdir()
        with (root / ".owner").open("xb") as stream:
            stream.write(ROOT_MARKER)
    _read_root(root)
    with _lock(root):
        if _journal(root).exists():
            return check(data)  # incomplete prior operation refuses; never silently repair
        require(set(p.name for p in root.iterdir()) == {".owner", "stage.lock"}, "YC_OWNER_STAGING_CONTENT_CONFLICT")
        journal = {"schema": 1, "component": "yc-owner-inactive", "source_ref": SOURCE_REF,
                   "artifact_id": MANIFEST["artifact_id"], "phase": "prepared"}
        with _journal(root).open("xb") as stream:
            stream.write(canonical(journal) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        package = root / MANIFEST["artifact_id"].replace(":", "-", 1)
        package.mkdir()
        deadline = time.monotonic() + 60
        for row in MANIFEST["files"]:
            raw = fetch(SOURCES[row["path"]], row["size"], deadline)
            require(type(raw) is bytes and len(raw) == row["size"]
                    and hashlib.sha256(raw).hexdigest() == row["sha256"], "YC_OWNER_DOWNLOAD_DIGEST_MISMATCH")
            destination = package / row["path"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("xb") as stream:
                stream.write(raw)
        with (package / "manifest.json").open("xb") as stream:
            stream.write(canonical(MANIFEST) + b"\n")
        validate(package)
        journal["phase"] = "complete"
        pending = root / "stage-journal.new"
        with pending.open("xb") as stream:
            stream.write(canonical(journal) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(pending, _journal(root))
        return check(data)


def add_cli_parser(sub):
    command = sub.add_parser("yc-owner", help="проверка и подготовка неактивного компонента YC59")
    actions = command.add_subparsers(dest="yc_owner_command", required=True)
    for name in ("plan", "check", "stage", "apply"):
        actions.add_parser(name)
    reader = actions.add_parser("read", help="один запрос чтения с подтверждением в отдельной консоли владельца")
    reader.add_argument("--owner-sid", required=True)


def run_cli(args):
    try:
        action = args.yc_owner_command
        if action == "apply":
            import setup_yc_owner_release as release
            # Stage client libraries and exact bootstrap data, never start under
            # the agent SID or elevate/switch an account from this Python.
            stage(bootstrap_core.data_root())
            result = release.prepare(bootstrap_core.data_root())
            print(json.dumps(result, sort_keys=True))
            return 0
        if action == "read":
            import setup_yc_owner_release as release
            result = release.read(bootstrap_core.data_root(), args.owner_sid)
            print(json.dumps(result, sort_keys=True))
            return 0
        result = plan() if action == "plan" else check(bootstrap_core.data_root()) if action == "check" else stage(bootstrap_core.data_root())
        print(json.dumps(result, sort_keys=True))
        return 0
    except yc_owner_client.ClientRefusal as error:
        print(str(error), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print('YC_OWNER_INTERRUPTED_NO_AUTOMATIC_RETRY', file=sys.stderr)
        return 130
    except Exception:
        print("YC_OWNER_COMPONENT_REFUSED", file=sys.stderr)
        return 2
