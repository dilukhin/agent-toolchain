"""Client can request/inspect/consume, never approve or retry consumption."""
import unittest
from yc_owner_client import ClientRefusal, perform


class ClientProtocolTests(unittest.TestCase):
    def invoke(self, outcome='ok'):
        self.requests = []
        pending = {'ok':True,'request_id':'request','operation_sha256':'digest','owner_instance_id':'owner','state':'pending'}
        def call(request, timeout):
            self.requests.append(request)
            action = request['action']
            if action == 'describe':
                return {'ok':True,'approval_required':True,'operation':{'schema':'yc-snapshot-read/v1'}}
            if action == 'propose':
                return pending
            if action == 'inspect':
                return {**pending,'state':'approved' if outcome != 'reject' else 'rejected'}
            if action == 'consume':
                if outcome == 'lost':
                    raise OSError('private transport diagnostic')
                return {**pending,'state':'consumed','logical_reads_completed':3,'profile':'yc-snapshot-read/v1',
                        'restore_verified':False,'projection':{}}
            raise AssertionError('unexpected action')
        return perform(call)

    def test_exact_once_and_no_decision_request(self):
        self.assertEqual(self.invoke()['state'],'consumed')
        self.assertEqual([r['action'] for r in self.requests], ['describe','propose','inspect','consume'])
        self.assertFalse(any('decision' in r or 'approved' in r for r in self.requests))

    def test_lost_reply_never_retries_or_leaks_diagnostics(self):
        with self.assertRaisesRegex(ClientRefusal,'CONSUME_NOT_RETRIED') as caught:
            self.invoke('lost')
        self.assertNotIn('private',str(caught.exception))
        self.assertEqual(sum(r['action']=='consume' for r in self.requests),1)

    def test_rejection_does_not_consume(self):
        with self.assertRaisesRegex(ClientRefusal,'REQUEST_REFUSED'):
            self.invoke('reject')
        self.assertFalse(any(r['action']=='consume' for r in self.requests))
