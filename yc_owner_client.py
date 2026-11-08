"""Untrusted agent-side client: exact proposals/inspection/one consume, never approval."""
from __future__ import annotations

import hashlib
import json
import sys
import time


class ClientRefusal(RuntimeError):
    pass


def need(value, code):
    if not value:
        raise ClientRefusal(code)


def endpoint_prefix(owner_sid):
    return 'yc59-' + hashlib.sha256(b'yc59.owner.endpoint/v1\n' + owner_sid.encode('ascii')).hexdigest()[:32]


def discover(transport, boundary, address, expected_owner_sid):
    """Observe SID/session/PID from the kernel, never pin a previous SessionId."""
    boundary.validate(boundary.Principal(expected_owner_sid, 0))
    k, deadline = transport.Kernel(), time.monotonic() + 5
    handle = None
    try:
        while True:
            handle = k.open(address, transport.CLIENT_ACCESS, 0, None, 3, 0x40110000, None)
            if handle != transport.INVALID:
                break
            import ctypes as C
            need(C.get_last_error() == 231 and time.monotonic() < deadline, 'YC_OWNER_ENDPOINT_UNAVAILABLE')
            time.sleep(.01)
        import ctypes as C
        from ctypes import wintypes as W
        pid, again, token = W.ULONG(), W.ULONG(), W.HANDLE()
        need(k.server_pid(handle, C.byref(pid)), 'YC_OWNER_SERVER_ID_UNAVAILABLE')
        process = k.open_process(0x1000, False, pid.value)
        need(process, 'YC_OWNER_SERVER_PROCESS_UNAVAILABLE')
        try:
            need(k.open_process_token(process, 8, C.byref(token)), 'YC_OWNER_SERVER_TOKEN_UNAVAILABLE')
            actor = boundary.from_token(token)
            need(actor.sid == expected_owner_sid, 'YC_OWNER_SERVER_SID_MISMATCH')
            agent = boundary.current()
            boundary.admit(actor, agent, agent.sid)
            need(k.server_pid(handle, C.byref(again)) and again.value == pid.value, 'YC_OWNER_SERVER_CHANGED')
            return actor, pid.value
        finally:
            if token:
                k.close(token)
            k.close(process)
    finally:
        if handle not in (None, transport.INVALID):
            k.close(handle)


def perform(call, *, clock=time.monotonic, pause=time.sleep, notify=lambda _: None):
    """call is a client transport adapter, not an executor/approval provider."""
    described = call({'action': 'describe'}, 5)
    need(type(described) is dict and described.get('ok') is True and described.get('approval_required') is True,
         'YC_OWNER_DESCRIPTION_REFUSED')
    operation = described.get('operation')
    need(type(operation) is dict and operation.get('schema') == 'yc-snapshot-read/v1', 'YC_OWNER_PROFILE_INVALID')
    pending = call({'action': 'propose', 'operation': operation}, 5)
    need(pending.get('ok') is True and pending.get('state') == 'pending', 'YC_OWNER_PROPOSAL_REFUSED')
    bound = {key: pending[key] for key in ('request_id', 'operation_sha256')}
    instance = pending['owner_instance_id']
    notify('Ожидается решение в отдельной локальной консоли владельца. Разового API-ответа OpenCode недостаточно.')
    deadline = clock() + 55
    while True:
        need(clock() < deadline, 'YC_OWNER_DECISION_TIMEOUT')
        state = call({'action': 'inspect', **bound}, 5)
        need(state.get('ok') is True and state.get('owner_instance_id') == instance
             and all(state.get(key) == value for key, value in bound.items()), 'YC_OWNER_REQUEST_CHANGED')
        if state.get('state') == 'approved':
            break
        need(state.get('state') == 'pending', 'YC_OWNER_REQUEST_REFUSED')
        pause(.25)
    # Never retry consume, including unknown transport outcomes or malformed reply.
    try:
        result = call({'action': 'consume', **bound}, 35)
    except Exception:
        raise ClientRefusal('YC_OWNER_RESULT_UNAVAILABLE_CONSUME_NOT_RETRIED') from None
    need(result.get('ok') is True and result.get('state') == 'consumed'
         and result.get('owner_instance_id') == instance and result.get('logical_reads_completed') == 3
         and result.get('profile') == 'yc-snapshot-read/v1' and result.get('restore_verified') is False
         and type(result.get('projection')) is dict
         and all(result.get(key) == value for key, value in bound.items()), 'YC_OWNER_READ_NOT_CONFIRMED')
    return result


def run(runtime, owner_sid):
    # Runtime is the checked, inactive library component. It has no agent-side
    # credentials, cloud executable starter or decision endpoint exposed here.
    sys.path.insert(0, str(runtime))
    import windows_owner_boundary as boundary
    import windows_owner_pipe as transport
    from approval_owner.ledger import strict_json
    from yc_snapshot_read import canonical
    address = rf'\\.\pipe\{endpoint_prefix(owner_sid)}-agent'
    owner, pid = discover(transport, boundary, address, owner_sid)
    def call(request, timeout):
        raw = transport.call(address, json.dumps(request, ensure_ascii=True).encode('ascii'),
                             expected_owner=owner, expected_pid=pid, timeout=timeout)
        return strict_json(raw)
    result = perform(call, notify=lambda message: print(message, file=sys.stderr))
    return {'schema': 'yc-owner-agent-result/v1', 'result': result,
            'result_sha256': hashlib.sha256(canonical(result)).hexdigest(),
            'owner_sid': owner.sid, 'owner_session_id': owner.session_id, 'server_pid': pid,
            'component_independently_confirmed': False}
