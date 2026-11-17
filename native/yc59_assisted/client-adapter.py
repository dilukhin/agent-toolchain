"""Invoke the installed client's unchanged perform(), with a durable consume latch."""
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import uuid



def save_new(path, value):
    with open(path, "x", encoding="utf-8") as f:
        json.dump(value, f, sort_keys=True, ensure_ascii=True)
        f.flush()
        os.fsync(f.fileno())


def publish(path, value):
    """Publish a complete coordination record; Windows rename refuses overwrite."""
    temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    save_new(temp,value)
    try:
        if os.name!='nt' and path.exists():
            raise FileExistsError(path)
        os.rename(temp,path)
    finally:
        if temp.exists():
            temp.unlink()


def run(core, data, shared, latch, owner_sid, agent_sid):
    if latch.exists():
        raise ValueError("prior consume attempt")
    sys.path.insert(0, str(core))
    import setup_yc_owner_component as library
    import yc_owner_client as client
    client.need(library.check(data)["status"] == "staged_verified", "YC59_CLIENT_NOT_VERIFIED")
    runtime = library._root(data) / library.MANIFEST["artifact_id"].replace(":", "-", 1) / "runtime"
    sys.path.insert(0, str(runtime))
    import windows_owner_boundary as boundary
    import windows_owner_pipe as transport
    from approval_owner.ledger import strict_json
    from yc_snapshot_read import canonical
    client.need(boundary.current().sid == agent_sid, "YC59_AGENT_LOGIN_REQUIRED")
    address = rf"\\.\pipe\{client.endpoint_prefix(owner_sid)}-agent"
    owner, pid = client.discover(transport, boundary, address, owner_sid)
    consumed = False

    def call(request, timeout):
        nonlocal consumed
        if request["action"] == "consume":
            consumed = True  # Includes an ambiguous durable-latch failure; fail closed.
            # Exists independently of the untrusted shared reports. Never remove.
            save_new(latch, {"schema": "yc59-consume-latch/v1", "consume_attempted": True,
                            "server_pid": pid, "request_id": request["request_id"],
                            "operation_sha256": request["operation_sha256"]})
        raw = transport.call(address, json.dumps(request, ensure_ascii=True).encode("ascii"),
                             expected_owner=owner, expected_pid=pid, timeout=timeout)
        value = strict_json(raw)
        if request["action"] == "propose" and value.get("ok") and value.get("state") == "pending":
            publish(shared / "request.json", {k: value[k] for k in
                     ("request_id", "operation_sha256", "owner_instance_id")} | {"server_pid": pid})
        return value
    try:
        result = client.perform(call)
        publish(shared / "agent-result.json", {
            "schema": "yc-owner-agent-result/v1", "result": result,
            "result_sha256": hashlib.sha256(canonical(result)).hexdigest(),
            "owner_sid": owner.sid, "owner_session_id": owner.session_id,
            "server_pid": pid, "component_independently_confirmed": False})
        return {"status": "CONSUMED_PENDING_INDEPENDENT_REVIEW", "consume_attempted": True}
    except Exception:
        return {"status": "STOPPED_AFTER_CONSUME_OR_UNKNOWN" if consumed else "STOPPED_BEFORE_CONSUME",
                "consume_attempted": consumed}


if __name__ == "__main__":
    try:
        core, data, shared, latch = map(Path, sys.argv[1:5])
        owner_sid, agent_sid = sys.argv[5:]
        status = run(core, data, shared, latch, owner_sid, agent_sid)
        publish(shared / "agent-done.json", {"schema": "yc59-agent-completion/v1", **status})
        sys.exit(0 if status["status"].startswith("CONSUMED") else 2)
    except Exception:
        # Never print raw exceptions, stdout of YC, or secret-like data.
        print("YC59_AGENT_ADAPTER_STOPPED", file=sys.stderr)
        sys.exit(2)
