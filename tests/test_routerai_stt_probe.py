import base64
import copy
from contextlib import redirect_stdout
from decimal import localcontext
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts import probe_routerai_stt_billing as probe


def response(value, status=200, headers=None):
    return probe.Response(status,headers or {},json.dumps(value).encode())


def catalog():
    return {'data':{'id':probe.MODEL,'endpoints':[{'tag':'synthetic-provider',
        'pricing':{'prompt':'0.001','seconds':'0.001','completion':'0'},
        'pricing_units':{'prompt':'token','seconds':'second','completion':'token'},
        'variable_pricings':[]}]}}


class FakeTransport:
    def __init__(self):
        self.calls=[]; self.post_count=0; self.fail=None; self.pending=False

    def request(self,method,path,payload=None):
        self.calls.append((method,path))
        if path==probe.CATALOG_PATH:
            value=catalog()
            if self.fail=='price':value['data']['endpoints'][0]['pricing']['seconds']='1'
            if self.fail=='envelope':
                value['data']['endpoints'][0]['pricing'].update(prompt='0.002',seconds='0.002')
            if self.fail=='boundary':
                value['data']['endpoints'][0]['pricing'].update(prompt='0.00166667',seconds='0.00166667')
            return response(value)
        if method=='POST':
            self.post_count+=1
            if self.fail=='network':raise ConnectionResetError('NEVER_EXPORT_SECRET_EXCEPTION')
            self.payload=payload
            with wave.open(io.BytesIO(base64.b64decode(payload['input_audio']['data'])),'rb') as src:
                self.seconds=str(probe.Decimal(src.getnframes())/16000)
            cost='0.11' if self.fail=='overrun' else '0.003'
            usage={'cost':cost,'seconds':self.seconds,'input_tokens':0,'output_tokens':1}
            if self.fail=='missing-cost':usage.pop('cost')
            if self.fail=='no-seconds':usage.pop('seconds')
            if self.fail=='bad-json':return probe.Response(200,{'x-generation-id':'gen_test'},b'{"usage":{"cost":NaN}}')
            return response({'text':'NEVER_EXPORT_TRANSCRIPT','usage':usage},
                status=500 if self.fail=='http' else 200,
                headers={'x-generation-id':'gen_'+str(self.post_count)})
        if self.pending:
            self.pending=False
            return response({},status=404)
        value={'id':'gen_'+str(self.post_count),'model':probe.MODEL,'provider_name':'Synthetic',
               'total_cost':'0.004' if self.fail=='mismatch' else '0.11' if self.fail=='overrun' else '0.003',
               'tokens_prompt':0,'tokens_completion':1,'untrusted':'NEVER_EXPORT_METADATA'}
        if self.fail=='identity':value['model']='synthetic/wrong-model'
        if self.fail=='generation-missing':return response({},status=404)
        return response({'data':value})


class ProbeTests(unittest.TestCase):
    def run_probe(self,transport):
        saved=[]
        with patch.object(probe.time,'sleep'):
            report=probe.experiment(transport,lambda value:saved.append(copy.deepcopy(value)))
        return report,saved

    def test_three_exact_wav_lengths_and_no_persisted_audio_or_text(self):
        transport=FakeTransport();report,saved=self.run_probe(transport)
        self.assertEqual(report['state'],'completed-three-probes')
        self.assertEqual(report['paid_attempts'],3);self.assertEqual(transport.post_count,3)
        self.assertEqual(report['known_cost_rub'],'0.009')
        self.assertEqual([x['usage']['seconds'] for x in report['attempts']],list(probe.DURATIONS))
        self.assertEqual([x['input_seconds'] for x in report['attempts']],list(probe.DURATIONS))
        text=json.dumps(report)
        for forbidden in ('NEVER_EXPORT','input_audio','data:audio','Authorization'):
            self.assertNotIn(forbidden,text)
        self.assertTrue(any(x['paid_attempts']==1 and x['attempts'][0]['state']=='sending' for x in saved))

    def test_known_input_duration_survives_missing_reported_seconds(self):
        transport=FakeTransport();transport.fail='no-seconds';report,_=self.run_probe(transport)
        self.assertEqual(report['paid_attempts'],3)
        self.assertEqual(report['attempts'][0]['input_seconds'],'1.25')
        self.assertNotIn('seconds',report['attempts'][0]['usage'])

    def test_uncertain_or_bad_post_stops_without_a_second_paid_attempt(self):
        for failure in ('network','http','missing-cost','bad-json','mismatch','identity','generation-missing','overrun'):
            with self.subTest(failure=failure):
                transport=FakeTransport();transport.fail=failure
                report,_=self.run_probe(transport)
                self.assertEqual(report['state'],'stopped-no-retry')
                self.assertEqual(transport.post_count,1)
                self.assertEqual(report['paid_attempts'],1)
                self.assertNotIn('NEVER_EXPORT',json.dumps(report))

    def test_http_error_keeps_generation_id_for_later_read_only_reconciliation(self):
        transport=FakeTransport();transport.fail='http';report,_=self.run_probe(transport)
        self.assertEqual(report['attempts'][0]['generation_id'],'gen_1')
        self.assertEqual(report['attempts'][0]['http_status'],500)

    def test_preflight_change_sends_no_audio_and_does_not_need_supported_apis(self):
        transport=FakeTransport();transport.fail='price';report,_=self.run_probe(transport)
        self.assertEqual(transport.post_count,0);self.assertEqual(report['paid_attempts'],0)
        self.assertEqual(probe.routes(response(catalog()))[0]['provider'],'synthetic-provider')

    def test_only_generation_get_can_retry_and_it_remains_bounded(self):
        transport=FakeTransport();transport.pending=True;report,_=self.run_probe(transport)
        self.assertEqual(report['paid_attempts'],3)
        self.assertEqual(sum(method=='GET' and path.startswith('/generation?') for method,path in transport.calls),4)

    def test_expensive_but_well_formed_preflight_stops_before_post(self):
        transport=FakeTransport();transport.fail='envelope';report,_=self.run_probe(transport)
        self.assertEqual(transport.post_count,0)
        self.assertEqual(report['attempts'][0]['stop_reason'],'research price envelope too large')

    def test_price_envelope_does_not_round_down_in_an_ambient_decimal_context(self):
        transport=FakeTransport();transport.fail='boundary'
        with localcontext() as ctx:
            ctx.prec=3
            report,_=self.run_probe(transport)
        self.assertEqual(transport.post_count,0)
        self.assertEqual(report['attempts'][0]['client_research_envelope_rub'],'0.10000020')

    def test_dry_run_neither_loads_credentials_nor_constructs_transport(self):
        with patch.object(probe,'key_from_env_file') as key,patch.object(probe,'Transport') as transport,redirect_stdout(io.StringIO()) as out:
            self.assertEqual(probe.main(['--env-file','NONEXISTENT_SECRET']),0)
        key.assert_not_called();transport.assert_not_called()
        self.assertEqual(json.loads(out.getvalue())['state'],'plan-only')

    def test_existing_run_directory_blocks_reexecution_before_secret_or_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(probe,'Transport') as transport,patch.object(probe,'key_from_env_file') as key,redirect_stdout(io.StringIO()):
                self.assertEqual(probe.main(['--execute','--env-file','NEVER_READ','--run-dir',tmp]),1)
            key.assert_not_called();transport.assert_not_called()

    def test_executed_report_is_private_and_key_does_not_enter_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);env=root/'synthetic.env';env.write_text('ROUTERAI_API_KEY="TEST_ONLY_SECRET"\nTELEGRAM_BOT_TOKEN=NEVER_READ_THIS\n')
            run=root/'new'
            with patch.object(probe,'Transport',return_value=FakeTransport()),redirect_stdout(io.StringIO()):
                self.assertEqual(probe.main(['--execute','--env-file',str(env),'--run-dir',str(run)]),0)
            raw=(run/'report.json').read_text()
            self.assertNotIn('TEST_ONLY_SECRET',raw);self.assertNotIn('NEVER_READ',raw)
            if os.name!='nt':
                self.assertEqual(run.stat().st_mode&0o777,0o700)
                self.assertEqual((run/'report.json').stat().st_mode&0o777,0o600)

    def test_credentials_are_parsed_without_environment_mutation_and_duplicates_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'safe.env';path.write_text('export ROUTERAI_API_KEY=TEST_ONLY_SECRET # comment\n')
            with patch.dict(os.environ,{'EXISTING_TEST_VAR':'preserve'},clear=True):
                self.assertEqual(probe.key_from_env_file(path),'TEST_ONLY_SECRET')
                self.assertEqual(dict(os.environ),{'EXISTING_TEST_VAR':'preserve'})
            path.write_text('ROUTERAI_API_KEY=TEST_ONLY_SECRET\nROUTERAI_API_KEY=OTHER_TEST_SECRET\n')
            with self.assertRaises(ValueError):probe.key_from_env_file(path)

    def test_raw_json_duplicates_nonfinite_and_extreme_values_are_refused(self):
        for raw in (b'{"usage":1,"usage":2}',b'{"cost":NaN}',b'[]',b'x'*(probe.MAX_BYTES+1)):
            with self.subTest(raw=raw[:40]),self.assertRaises(ValueError):probe.decode(raw)
        for value in (True,0.1,'-1','Infinity','1e1000000','1e-1000000'):
            with self.subTest(value=value),self.assertRaises((ValueError,ArithmeticError)):probe.exact(value)

    def test_transport_destination_is_fixed_and_redirect_handler_never_follows(self):
        self.assertIsNone(probe.NoRedirect().redirect_request(None,None,302,'',{},'https://example.invalid'))
        transport=probe.Transport('TEST_ONLY_SECRET')
        with patch.object(transport.opener,'open') as network:
            for method,path in (('POST','/chat/completions'),('GET','https://example.invalid'),('GET','/generation?id=gen_x&extra=1')):
                with self.subTest(path=path),self.assertRaises(ValueError):transport.request(method,path)
            network.assert_not_called()


if __name__=='__main__':unittest.main()
