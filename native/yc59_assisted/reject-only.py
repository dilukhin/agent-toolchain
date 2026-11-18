"""Owner-side inspection/cancellation. No approve operation exists in this helper."""
import json
from pathlib import Path
import sys


def cancel(runtime, pid, request, owner_sid):
    sys.path.insert(0, str(runtime))
    import windows_owner_boundary as boundary
    import windows_owner_pipe as transport
    from approval_owner.ledger import strict_json
    import hashlib
    actor = boundary.current()
    if actor.sid != owner_sid:
        raise ValueError("owner")
    prefix = "yc59-" + hashlib.sha256(b"yc59.owner.endpoint/v1\n" + actor.sid.encode("ascii")).hexdigest()[:32]
    address = rf"\\.\pipe\{prefix}-decision"
    bound = {key: request[key] for key in ("request_id", "operation_sha256")}
    def call(action, **extra):
        return strict_json(transport.call(address, json.dumps({"action": action, **bound, **extra}).encode("ascii"),
                                         expected_owner=actor, expected_pid=pid, timeout=5))
    observed = call("inspect")
    # This fixed authenticated code means this exact entry is expired, never consumed.
    if observed == {"ok": False, "code": "REQUEST_EXPIRED"}:
        return {"state": "expired", **bound, "server_pid": pid, "owner_sid": actor.sid}
    if not (observed.get("ok") is True and observed.get("owner_instance_id") == request["owner_instance_id"]
            and all(observed.get(k) == v for k, v in bound.items())):
        raise ValueError("binding")
    if observed["state"] == "pending":
        observed = call("decide", decision="reject")
    if not (observed.get("ok") is True and observed.get("state") == "rejected"
            and observed.get("owner_instance_id") == request["owner_instance_id"]
            and all(observed.get(k) == v for k, v in bound.items())):
        raise ValueError("already approved/consumed or unknown")
    return {"state": "rejected", **bound, "server_pid": pid, "owner_sid": actor.sid}


if __name__ == "__main__":
    try:
        runtime, pid, req, dest, owner_sid = sys.argv[1:]
        request = json.loads(Path(req).read_text(encoding="utf-8-sig"))
        result = cancel(Path(runtime), int(pid), request, owner_sid)
        with open(dest, "x", encoding="utf-8") as f:
            json.dump({"schema": "yc59-trusted-no-consume/v1", **result}, f, sort_keys=True)
    except Exception:
        print("YC59_NO_CONSUME_NOT_PROVEN", file=sys.stderr)
        sys.exit(2)
