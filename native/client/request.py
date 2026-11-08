"""Untrusted request client. Never submits an owner decision or retries consume."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import windows_owner_boundary as boundary
from windows_owner_pipe import call


def execute(endpoint, invoke, *, clock=time.monotonic, wait=time.sleep):
    if set(endpoint) != {"schema", "component_id", "owner_sid", "owner_session_id", "agent_sid",
                         "server_pid", "pipe_prefix", "approval_required"}:
        raise ValueError("YC_OWNER_ENDPOINT_INVALID")
    if (endpoint["schema"] != "yc-owner-live-endpoint/v1" or endpoint["approval_required"] is not True
            or re.fullmatch(r"yc59-[0-9a-f]{32}", endpoint["pipe_prefix"]) is None):
        raise ValueError("YC_OWNER_ENDPOINT_INVALID")
    described = invoke({"action": "describe"}, 5)
    if described.get("ok") is not True or described.get("approval_required") is not True:
        raise ValueError("YC_OWNER_DESCRIPTION_REFUSED")
    operation = described["operation"]
    digest = hashlib.sha256(json.dumps(operation,sort_keys=True,ensure_ascii=True,
                          separators=(",", ":"),allow_nan=False).encode("ascii")).hexdigest()
    proposed = invoke({"action": "propose", "operation": operation}, 5)
    if proposed.get("ok") is not True or proposed.get("operation_sha256") != digest:
        raise ValueError("YC_OWNER_PROPOSAL_REFUSED")
    bound = {key:proposed[key] for key in ("request_id","operation_sha256")}
    instance = proposed["owner_instance_id"]
    deadline = clock() + 40
    status = proposed
    while status.get("state") == "pending" and clock() < deadline:
        wait(.25)
        status = invoke({"action":"inspect", **bound}, 5)
        if status.get("ok") is not True or status.get("owner_instance_id") != instance:
            raise ValueError("YC_OWNER_REQUEST_LOST")
    if status.get("state") != "approved":
        raise ValueError("YC_OWNER_APPROVAL_UNAVAILABLE")
    # Exactly one call. A timeout or lost reply is an unknown outcome, never a retry.
    result = invoke({"action":"consume", **bound}, 35)
    if (result.get("ok") is not True or result.get("request_id") != bound["request_id"]
            or result.get("operation_sha256") != digest or result.get("owner_instance_id") != instance):
        raise ValueError("YC_OWNER_READ_REFUSED")
    return {"schema":"yc-owner-client-result/v1", "component_id":endpoint["component_id"],
            "result":result,"result_sha256":hashlib.sha256(json.dumps(result,sort_keys=True,
                ensure_ascii=True,separators=(",", ":"),allow_nan=False).encode("ascii")).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint",type=Path,required=True)
    args = parser.parse_args()
    raw = args.endpoint.read_bytes()
    if len(raw) > 4096:
        raise ValueError("YC_OWNER_ENDPOINT_LIMIT")
    endpoint = json.loads(raw)
    owner = boundary.Principal(endpoint["owner_sid"],endpoint["owner_session_id"])
    boundary.admit(owner,boundary.current(),endpoint["agent_sid"])
    address = rf"\\.\pipe\{endpoint['pipe_prefix']}-agent"
    def invoke(request, timeout):
        return json.loads(call(address,json.dumps(request,ensure_ascii=True).encode("ascii"),
            expected_owner=owner,expected_pid=endpoint["server_pid"],timeout=timeout))
    print(json.dumps(execute(endpoint,invoke),ensure_ascii=True,sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (Exception,KeyboardInterrupt):
        print("YC_OWNER_CLIENT_REFUSED_NO_AUTOMATIC_RETRY",file=sys.stderr)
        raise SystemExit(2) from None
