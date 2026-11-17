"""Compare both receipts and copy only the validated agent receipt to the report."""
import hashlib
import json
from pathlib import Path
import sys

ARTIFACT='sha256:14f643cb899c6bcec43cb2fa417e952d9c9d1a0ba13c5fb6e23a2d1e9e9ea6df'


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate key')
            result[key] = value
        return result
    def invalid(value):
        raise ValueError('non-finite number')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)


def canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('ascii')


def reconcile(owner, agent, pid, session, owner_sid):
    if set(owner) != {'schema','component_id','result','result_sha256'} or owner['schema'] != 'yc-owner-result/v1' or owner['component_id'] != ARTIFACT:
        raise ValueError('owner receipt')
    if set(agent) != {'schema','result','result_sha256','owner_sid','owner_session_id','server_pid','component_independently_confirmed'}:
        raise ValueError('agent receipt fields')
    if agent['schema'] != 'yc-owner-agent-result/v1' or agent['owner_sid'] != owner_sid or agent['server_pid'] != pid or agent['owner_session_id'] != session or agent['component_independently_confirmed'] is not False:
        raise ValueError('agent identity')
    result=owner['result']
    digest=hashlib.sha256(canonical(result)).hexdigest()
    if digest != owner['result_sha256'] or digest != agent['result_sha256'] or canonical(result) != canonical(agent['result']):
        raise ValueError('receipt mismatch')
    if not (result.get('state') == 'consumed' and result.get('logical_reads_completed') == 3 and result.get('profile') == 'yc-snapshot-read/v1' and result.get('restore_verified') is False):
        raise ValueError('read not confirmed')
    return {**agent, 'result':result, 'result_sha256':digest}


if __name__ == '__main__':
    try:
        op, ap, pid, session, dest, owner_sid=sys.argv[1:]
        result=reconcile(strict_json(Path(op).read_text(encoding='utf-8-sig')),
                         strict_json(Path(ap).read_text(encoding='utf-8-sig')),int(pid),int(session),owner_sid)
        with open(dest,'x',encoding='utf-8') as f:json.dump(result,f,sort_keys=True,ensure_ascii=True)
    except Exception:
        print('YC59_RECEIPTS_DONT_MATCH_NO_RETRY',file=sys.stderr)
        sys.exit(2)
