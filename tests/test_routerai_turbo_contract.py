import copy
import json
from pathlib import Path
import unittest
from scripts import routerai_prices as prices
from scripts import routerai_billing_review as billing


def fixture():
    eps=[{'tag':p,'pricing':{'prompt':r,'seconds':r,'completion':'0'},
          'pricing_units':billing.UNITS.copy(),'supported_apis':[],'variable_pricings':[]}
         for p,r in [('deepinfra','0.0003'),('groq','0.001')]]
    arch={'input_modalities':['audio'],'output_modalities':['transcription']}
    spec={'api':billing.API,'billing_contract':billing.CONTRACT,'ceilings':{
        k:{'unit':u,'rub':'0' if k=='completion' else billing.CEILING_RUB} for k,u in billing.UNITS.items()}}
    return {billing.MODEL:{'architecture':arch}},{billing.MODEL:{'data':{'id':billing.MODEL,'endpoints':eps}}},{'schema':1,'models':{billing.MODEL:spec}}


def candidate(parts=None):
    return prices.build_candidate(*(parts or fixture()),checked_at='2026-11-02T10:00:00Z')


class TurboContractTests(unittest.TestCase):
    def test_documented_duration_does_not_relabel_raw_token_prices(self):
        v=candidate();m=v['content']['models'][billing.MODEL]
        self.assertEqual(m['status'],'available');self.assertNotIn('billing_review',m)
        self.assertEqual(m['endpoints'][0]['pricing']['prompt'],'0.0003')
        self.assertEqual(m['endpoints'][0]['pricing_units']['prompt'],'token')
        self.assertEqual(m['endpoints'][0]['supported_apis'],[])
        self.assertEqual(len(m['billing_contract']['routes']),2)
        self.assertEqual(m['billing_contract']['reservation'],{'floor_seconds':'60','quantum_seconds':'1'})
        prices.verify(v,published=False)

    def test_shape_or_route_changes_remove_financial_admission(self):
        for mutation in ('add','remove','unit','variable','component','prompt','completion','architecture','api','bad-api-shape'):
            parts=fixture();eps=parts[1][billing.MODEL]['data']['endpoints']
            if mutation=='add':eps.append({**copy.deepcopy(eps[0]),'tag':'new'})
            elif mutation=='remove':eps.pop()
            elif mutation=='unit':eps[0]['pricing_units']['seconds']='token'
            elif mutation=='variable':eps[0]['variable_pricings']=[{'threshold':1}]
            elif mutation=='component':eps[0]['pricing']['request']='0'
            elif mutation=='prompt':eps[0]['pricing']['prompt']='0.0002'
            elif mutation=='completion':eps[0]['pricing']['completion']='0.001'
            elif mutation=='architecture':parts[0][billing.MODEL]['architecture']['input_modalities']=['audio','text']
            elif mutation=='api':eps[0]['supported_apis']=['chat']
            else:eps[0]['supported_apis']='audio/transcriptions'
            with self.subTest(mutation=mutation):
                try:v=candidate(parts)
                except prices.PriceError:continue
                self.assertEqual(v['content']['models'][billing.MODEL]['status'],'unpriced')
                prices.verify(v,published=False)

    def test_rehashed_normalization_or_policy_cannot_change_reserve(self):
        for field in ('reservation','ceiling','sources','routes','openapi_sha256','profile'):
            v=candidate();v['content']['models'][billing.MODEL]['billing_contract'][field]='forged'
            v['snapshot_id']=prices.digest(v['content'])
            with self.subTest(field=field),self.assertRaises(prices.PriceError):prices.verify(v,published=False)
        parts=fixture();parts[2]['models'][billing.MODEL]['billing_contract']='unknown'
        with self.assertRaises(prices.PriceError):candidate(parts)

    def test_ceiling_and_loss_of_route_preserve_last_good(self):
        v=candidate();old=prices.approve(v,None,candidate_sha='a'*40,run_id=1,at='2026-11-02T10:01:00Z')
        parts=fixture();parts[1][billing.MODEL]['data']['endpoints'][1]['pricing'].update(prompt='0.00131',seconds='0.00131')
        with self.assertRaises(prices.PriceError):candidate(parts)
        parts=fixture();parts[1][billing.MODEL]['data']['endpoints'].pop()
        changed=candidate(parts);changed['source_checked_at']='2026-11-02T11:00:00Z'
        with self.assertRaises(prices.PriceError):prices.approve(changed,old,candidate_sha='b'*40,run_id=2,at='2026-11-02T11:01:00Z')

    def test_shared_fixture_is_exact_producer_output(self):
        p=Path(__file__).parent/'fixtures/routerai_turbo_contract.json'
        expected=prices.approve(candidate(),None,candidate_sha='a'*40,run_id=1,at='2026-11-02T10:01:00Z')
        self.assertEqual(json.loads(p.read_text()),expected)
