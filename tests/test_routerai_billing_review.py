import copy
import unittest
from scripts import routerai_prices as prices
from scripts import routerai_billing_review as billing


def candidate(change=None):
    eps=[{'tag':p,'pricing':{'prompt':r,'seconds':r,'completion':'0'},
          'pricing_units':billing.UNITS.copy(),'supported_apis':[],'variable_pricings':[]}
         for p,r in [('deepinfra','0.0001234567890123456789'),('groq','0.0009')]]
    arch={'input_modalities':['audio'],'output_modalities':['transcription']}
    spec={'api':billing.API,'billing_review':billing.PROFILE,'ceilings':{
        k:{'unit':u,'rub':'0.001'} for k,u in billing.UNITS.items()}}
    if change:change(eps,arch,spec)
    return prices.build_candidate({billing.MODEL:{'architecture':arch}},
        {billing.MODEL:{'data':{'id':billing.MODEL,'endpoints':eps}}},
        {'schema':1,'models':{billing.MODEL:spec}},checked_at='2026-11-02T10:00:00Z')


class BillingReviewTests(unittest.TestCase):
    def test_proposed_seconds_and_documented_api_preserve_raw_without_admission(self):
        v=candidate();m=v['content']['models'][billing.MODEL];r=m['billing_review']
        self.assertEqual(m['status'],'unpriced');self.assertEqual(r['state'],'candidate')
        self.assertIs(r['financial_authorization'],False)
        self.assertEqual(r['routes'][0]['proposed_components']['seconds']['rub'],'0.0001234567890123456789')
        self.assertEqual([e['provider'] for e in r['routes']],['deepinfra','groq'])
        for ep in m['endpoints']:
            self.assertEqual(ep['supported_apis'],[])
            self.assertEqual(ep['pricing_units'],billing.UNITS)
            self.assertGreater(prices.amount(ep['pricing']['prompt']),0)
        prices.verify(v,published=False)

    def test_changed_routes_units_components_architecture_rejected(self):
        changes=[lambda e,a,s:e.pop(),lambda e,a,s:e[1].update(tag='new'),
            lambda e,a,s:e[0]['pricing_units'].update(seconds='token'),
            lambda e,a,s:e[0]['pricing'].update(prompt='0.0001'),
            lambda e,a,s:e[0]['pricing'].update(completion='0.0001'),
            lambda e,a,s:e[0].update(variable_pricings=[{'threshold':1}]),
            lambda e,a,s:a['input_modalities'].append('text'),
            lambda e,a,s:e[0]['pricing'].update(extra='0')]
        for i,change in enumerate(changes):
            with self.subTest(i=i):
                v=candidate(change);m=v['content']['models'][billing.MODEL]
                self.assertEqual(m['billing_review']['state'],'rejected')
                self.assertEqual(m['status'],'unpriced');prices.verify(v,published=False)

    def test_rehashed_annotation_cannot_forge_proof_or_authorization(self):
        for field in ('financial_authorization','api_evidence','routes','profile'):
            v=candidate();r=v['content']['models'][billing.MODEL]['billing_review']
            r[field]=True if field=='financial_authorization' else 'forged'
            v['snapshot_id']=prices.digest(v['content'])
            with self.subTest(field=field),self.assertRaises(prices.PriceError):prices.verify(v,published=False)

    def test_policy_typo_and_other_api_are_rejected(self):
        for change in (lambda e,a,s:s.update(billing_review='unknown'),lambda e,a,s:s.update(api='chat')):
            with self.assertRaises(prices.PriceError):candidate(change)

    def test_price_ceiling_cannot_be_bypassed(self):
        with self.assertRaises(prices.PriceError):
            candidate(lambda e,a,s:e[1]['pricing'].update(prompt='1',seconds='1'))
