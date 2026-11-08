import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'native/client'))
spec=importlib.util.spec_from_file_location('yc59_request',ROOT/'native/client/request.py')
client=importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)


class ManagedPinsTests(unittest.TestCase):
    def test_candidate_and_transport_are_exact_authoritative_bytes(self):
        raw=(ROOT/'native/yc_owner_candidate.json').read_bytes()
        manifest=json.loads(raw)
        seed=(ROOT/'native/yc_owner_managed.ps1').read_text()
        self.assertIn("$CandidateSha256 = '"+hashlib.sha256(raw).hexdigest()+"'",seed)
        self.assertIn("$CandidateId = '"+manifest['artifact_id']+"'",seed)
        core={k:v for k,v in manifest.items() if k!='artifact_id'}
        canonical=json.dumps(core,sort_keys=True,ensure_ascii=True,separators=(',',':')).encode()
        self.assertEqual('sha256:'+hashlib.sha256(b'opencode_permissions.yc_owner_component.v2\n'+canonical).hexdigest(),manifest['artifact_id'])
        self.assertFalse(manifest['installed_acceptance'])
        self.assertEqual(len(manifest['files']),16)
        pins={r['path']:r for r in manifest['files']}
        for name in ('windows_owner_pipe.py','windows_owner_boundary.py'):
            b=(ROOT/'native/client'/name).read_bytes()
            self.assertEqual(hashlib.sha256(b).hexdigest(),pins['runtime/'+name]['sha256'])
        pin=re.search(r"(?m)^\$NativeSha256 = '([0-9a-f]{64})'$",seed).group(1)
        self.assertEqual(pin,hashlib.sha256((ROOT/'native/yc_owner_native.cs').read_bytes()).hexdigest())


class RequestTests(unittest.TestCase):
    def setUp(self):
        self.endpoint=dict(schema='yc-owner-live-endpoint/v1',component_id='sha256:fixture',
            owner_sid='owner',owner_session_id=2,agent_sid='agent',server_pid=123,
            pipe_prefix='yc59-'+'a'*32,approval_required=True)
        self.operation={'schema':'fixture'}
        self.digest=hashlib.sha256(b'{"schema":"fixture"}').hexdigest()
        self.calls=[]

    def invoke(self,request,timeout):
        self.calls.append(request['action'])
        action=request['action']
        if action=='describe': return dict(ok=True,operation=self.operation,approval_required=True)
        if action=='consume':
            self.assertEqual(timeout,35)
            return dict(ok=True,request_id='request',operation_sha256=self.digest,owner_instance_id='instance',projection={'safe':True})
        return dict(ok=True,request_id='request',operation_sha256=self.digest,
                    owner_instance_id='instance',state='pending' if action=='propose' else 'approved')

    def test_only_one_consumption_after_owner_decision(self):
        result=client.execute(self.endpoint,self.invoke,wait=lambda _:None)
        self.assertEqual(self.calls,['describe','propose','inspect','consume'])
        self.assertEqual(result['result']['projection'],{'safe':True})
        self.assertEqual(result['result_sha256'],hashlib.sha256(json.dumps(result['result'],
            ensure_ascii=True,sort_keys=True,separators=(',',':')).encode()).hexdigest())

    def test_unknown_consume_outcome_never_retried(self):
        def fail(request,timeout):
            result=self.invoke(request,timeout)
            if request['action']=='consume': raise TimeoutError('lost reply')
            return result
        with self.assertRaises(TimeoutError): client.execute(self.endpoint,fail,wait=lambda _:None)
        self.assertEqual(self.calls.count('consume'),1)

    def test_rejection_or_changed_instance_cannot_consume(self):
        for change in ({'state':'rejected'},{'owner_instance_id':'changed'}):
            self.calls=[]
            def invoke(request,timeout):
                result=self.invoke(request,timeout)
                return {**result,**change} if request['action']=='inspect' else result
            with self.assertRaises(ValueError): client.execute(self.endpoint,invoke,wait=lambda _:None)
            self.assertNotIn('consume',self.calls)

    def test_endpoint_cannot_supply_approval(self):
        with self.assertRaises(ValueError):
            client.execute({**self.endpoint,'approved':True},lambda *_:self.fail('must not contact host'))


if __name__=='__main__': unittest.main()
