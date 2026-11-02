"""Documented API and proposed billing interpretation; never financial admission."""
from __future__ import annotations

from decimal import Decimal

MODEL = 'openai/whisper-large-v3-turbo'
PROFILE = 'whisper-turbo-duration-review-v1'
API = 'audio/transcriptions'
SOURCES = [
    'https://routerai.ru/models/openai/whisper-large-v3-turbo',
    'https://routerai.ru/docs/guides/overview/multimodal/speech-to-text.md',
]
UNITS = {'prompt': 'token', 'seconds': 'second', 'completion': 'token'}


def review(model):
    """Derived data are deliberately separate from untouched endpoint prices.

    Equal numbers and zero tokens do not prove that prompt is an alias. No
    reviewed minimum/rounding rule for every route exists yet. A caller MUST
    keep this model unpriced, even when the proposed interpretation matches.
    """
    result = {
        'profile': PROFILE, 'state': 'rejected', 'financial_authorization': False,
        'api_evidence': {'api': API, 'kind': 'model-documentation', 'sources': SOURCES.copy()},
        'observed_billing_providers': ['groq'],
        'observations_source': 'https://github.com/dilukhin/agent-toolchain/blob/edb8fbd8a47f83ce75ce6ba1509bb5ae7a2555b1/docs/research/routerai_stt_paid_2026-10-30.json',
        'unresolved': ['prompt-seconds-relationship', 'all-route-minimum-and-rounding'],
        'routes': [],
    }
    if model.get('id') != MODEL or model.get('api') != API:
        result['reason'] = 'model-or-api-mismatch'
        return result
    if model.get('architecture') != {'input_modalities': ['audio'], 'output_modalities': ['transcription']}:
        result['reason'] = 'architecture-changed'
        return result
    endpoints = model.get('endpoints', [])
    providers = [ep.get('provider') for ep in endpoints]
    if len(providers) != 2 or set(providers) != {'deepinfra', 'groq'}:
        result['reason'] = 'route-set-changed'
        return result
    for ep in sorted(endpoints, key=lambda item: item['provider']):
        prices = ep.get('pricing', {})
        if (set(prices) != set(UNITS) or ep.get('pricing_units') != UNITS
                or ep.get('variable_pricing') is not False):
            result['reason'] = 'price-shape-changed'
            return result
        # Input has already passed exact-decimal validation in routerai_prices.
        if (Decimal(prices['seconds']) <= 0 or Decimal(prices['prompt']) != Decimal(prices['seconds'])
                or Decimal(prices['completion']) != 0):
            result['reason'] = 'price-relationship-changed'
            return result
        result['routes'].append({
            'provider': ep['provider'],
            'proposed_components': {'seconds': {'rub': prices['seconds'], 'unit': 'second'}},
            'minimum_seconds': None, 'rounding_seconds': None,
        })
    result.update(state='candidate', reason='billing-contract-unconfirmed')
    return result

# A separate contract, never an authorization bit on the research review.
CONTRACT = 'routerai-whisper-turbo-seconds-v1'
CEILING_RUB = '0.0013'
OPENAPI = 'https://routerai.ru/api/openapi.json'
OPENAPI_SHA256 = '7430c049de0f226259a3e135697143df10bebf3cc1f3e8ca67b79a82cd0fd40d'


def contract(model):
    """Reviewed interpretation of RouterAI's documented per-second model.

    The 60-second floor/1-second ceiling are consumer reservation policy, not
    assertions about RouterAI's billing minimum or quantum. Settlement uses
    usage.cost and generation.total_cost, never these estimated quantities.
    """
    checked = review(model)
    if checked['state'] != 'candidate':
        return None
    bounds = model.get('price_ceilings')
    expected_bounds = {key: {'unit': unit, 'rub': CEILING_RUB if key != 'completion' else '0'}
                       for key, unit in UNITS.items()}
    if bounds != expected_bounds:
        return None
    for ep in model['endpoints']:
        if ep.get('supported_apis') and API not in ep['supported_apis']:
            return None
        if Decimal(ep['pricing']['seconds']) > Decimal(CEILING_RUB):
            return None
    return {
        'profile': CONTRACT, 'api': API, 'basis': 'documented-duration',
        'sources': SOURCES + [OPENAPI], 'openapi_sha256': OPENAPI_SHA256,
        'reservation': {'floor_seconds': '60', 'quantum_seconds': '1'},
        'ceiling': {'rub': CEILING_RUB, 'unit': 'second'},
        'routes': [{'provider': ep['provider'], 'components': {
            'seconds': {'rub': ep['pricing']['seconds'], 'unit': 'second'}}}
            for ep in sorted(model['endpoints'], key=lambda ep: ep['provider'])],
    }
