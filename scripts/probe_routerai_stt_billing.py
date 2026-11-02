"""Three explicitly authorized synthetic STT probes; plan-only by default.

Research utility, not a catalog updater or a production bot. Spending stops are
client-side: an already sent request cannot be capped or undone by this script.
"""
from __future__ import annotations

import argparse
import ast
import base64
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, localcontext
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import struct
import tempfile
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener
import wave

BASE = 'https://routerai.ru/api/v1'
MODEL = 'openai/whisper-large-v3-turbo'
DURATIONS = ('1.25', '9.25', '11.25')
TOTAL_STOP = Decimal('0.50')
CALL_STOP = Decimal('0.10')
RATE_STOP = Decimal('0.01')
MAX_BYTES = 1024 * 1024
GEN_ID = re.compile(r'[A-Za-z0-9_-]{1,128}\Z')
CATALOG_PATH = '/models/' + MODEL + '/endpoints'
NUMBERS = ('seconds', 'duration', 'duration_seconds', 'cost', 'total_cost',
           'input_tokens', 'output_tokens', 'total_tokens', 'tokens_prompt',
           'tokens_completion', 'native_tokens_prompt', 'native_tokens_completion',
           'generation_time', 'latency')


class ProbeStop(ValueError):
    """A fixed diagnostic string written by this program, never external text."""


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def exact(value):
    if isinstance(value, bool) or isinstance(value, float) or value is None or len(str(value)) > 120:
        raise ValueError('invalid numeric metadata')
    result = Decimal(value)
    if (not result.is_finite() or result < 0 or result > Decimal('1e12')
            or len(result.as_tuple().digits) > 80 or (result and result.adjusted() < -80)):
        raise ValueError('invalid numeric metadata')
    return result


def decode(raw):
    def unique(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ValueError('duplicate JSON key')
            out[key] = value
        return out
    def reject(value):
        raise ValueError('non-finite JSON')
    if len(raw) > MAX_BYTES:
        raise ValueError('response too large')
    value = json.loads(raw, parse_float=Decimal, parse_constant=reject, object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise ValueError('object required')
    return value


def audio(seconds):
    frames = int(Decimal(seconds) * 16000)
    # Deterministic non-speech signal; no user recordings or local TTS required.
    pcm = b''.join(struct.pack('<h', int(1800 * math.sin(2 * math.pi * 440 * i / 16000)
                                      + 500 * math.sin(2 * math.pi * 660 * i / 16000)))
                   for i in range(frames))
    stream = io.BytesIO()
    with wave.open(stream, 'wb') as target:
        target.setnchannels(1); target.setsampwidth(2); target.setframerate(16000)
        target.writeframes(pcm)
    return stream.getvalue()


def plan():
    return {'schema': 1, 'kind': 'routerai-stt-billing-probe', 'state': 'plan-only',
        'authorization': 'owner instruction 2026-10-30; three short synthetic requests',
        'model': MODEL, 'durations_seconds': list(DURATIONS), 'max_paid_attempts': 3,
        'total_client_stop_rub': str(TOTAL_STOP), 'per_response_client_stop_rub': str(CALL_STOP),
        'client_research_envelope_seconds': 60, 'server_charge_cap_guaranteed': False,
        'preflight_max_seconds_rate_rub': str(RATE_STOP), 'automatic_post_retries': 0,
        'limitations': ['client stop does not cap an already sent provider request',
                        'tones test billing, not recognition quality',
                        'no publication, financial admission or production changes']}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


@dataclass
class Response:
    status: int
    headers: dict
    body: bytes


class Transport:
    def __init__(self, key):
        self.key = key
        self.opener = build_opener(NoRedirect())

    def request(self, method, path, payload=None):
        public = method == 'GET' and path == CATALOG_PATH
        allowed = public or (method == 'POST' and path == '/audio/transcriptions')
        allowed = allowed or (method == 'GET' and path.startswith('/generation?id=')
                              and GEN_ID.fullmatch(path.removeprefix('/generation?id=')))
        if not allowed:
            raise ValueError('endpoint not allowed')
        headers = {'Accept': 'application/json', 'Accept-Encoding': 'identity'}
        if not public:
            headers['Authorization'] = 'Bearer ' + self.key
        body = None
        if payload is not None:
            headers['Content-Type'] = 'application/json'
            body = json.dumps(payload, separators=(',', ':'), allow_nan=False).encode('utf-8')
        request = Request(BASE + path, data=body, headers=headers, method=method)
        try:
            response = self.opener.open(request, timeout=60 if method == 'POST' else 15)
        except HTTPError as exc:
            response = exc
        with response:
            return Response(response.code, {k.lower(): v for k,v in response.headers.items()},
                            response.read(MAX_BYTES + 1))


def routes(response):
    if response.status != 200:
        raise ValueError('preflight HTTP failure')
    data = decode(response.body)['data']
    if data.get('id') != MODEL or not isinstance(data.get('endpoints'), list) or not 1 <= len(data['endpoints']) <= 16:
        raise ValueError('unexpected routes')
    out = []
    for endpoint in data['endpoints']:
        prices, units = endpoint['pricing'], endpoint['pricing_units']
        tag = endpoint['tag']
        if (not isinstance(tag,str) or not re.fullmatch(r'[a-z0-9_-]{1,64}',tag)
                or set(prices) != {'prompt','seconds','completion'}
                or units != {'prompt':'token','seconds':'second','completion':'token'}
                or endpoint.get('variable_pricings') != []):
            raise ProbeStop('pricing pattern changed')
        second = exact(prices['seconds'])
        if not 0 < second <= RATE_STOP or exact(prices['prompt']) != second or exact(prices['completion']) != 0:
            raise ProbeStop('unexpected price')
        out.append({'provider':tag, 'seconds_rub':str(second),
                    'raw_prompt_rub':str(exact(prices['prompt'])), 'raw_prompt_unit':'token'})
    if len({r['provider'] for r in out}) != len(out):
        raise ValueError('duplicate provider')
    return out


def numeric_fields(value):
    out = {}
    if not isinstance(value, dict):
        return out
    for key in NUMBERS:
        if value.get(key) is not None:
            out[key] = str(exact(value[key]))
    return out


def inspect_response(response):
    if response.status != 200:
        raise ProbeStop('STT HTTP failure; outcome may be paid')
    data = decode(response.body)
    identity = response.headers.get('x-generation-id')
    if not isinstance(identity,str) or not GEN_ID.fullmatch(identity):
        raise ProbeStop('generation identity missing')
    usage = numeric_fields(data.get('usage'))
    if 'cost' not in usage:
        raise ProbeStop('usage cost missing')
    return {'generation_id':identity, 'usage':usage,
            'text_nonempty':bool(isinstance(data.get('text'),str) and data['text'].strip())}


def generation(transport, identity):
    for attempt in range(2):
        response = transport.request('GET', '/generation?' + urlencode({'id':identity}))
        if response.status in {404,429,500,502,503,504} and attempt == 0:
            time.sleep(0.3)
            continue
        if response.status != 200:
            raise ProbeStop('generation unavailable')
        data = decode(response.body)['data']
        if data.get('id') != identity or data.get('model') != MODEL:
            raise ProbeStop('generation identity mismatch')
        result = {'id':identity,'model':MODEL,**numeric_fields(data)}
        if 'total_cost' not in result:
            raise ProbeStop('generation cost missing')
        for name in ('provider_name','provider'):
            val = data.get(name)
            if isinstance(val,str) and re.fullmatch(r'[A-Za-z0-9 _.-]{1,80}',val):
                result[name] = val
        if isinstance(data.get('usage'),dict):
            result['usage'] = numeric_fields(data['usage'])
        return result
    raise ProbeStop('generation unavailable')


def experiment(transport, checkpoint):
    report = {**plan(), 'state':'running', 'started_at':now(), 'attempts':[],
              'known_cost_rub':'0', 'paid_attempts':0}
    checkpoint(report)
    total = Decimal(0)
    for duration in DURATIONS:
        entry = {'input_seconds':duration, 'state':'preflight'}
        report['attempts'].append(entry)
        try:
            entry['routes'] = routes(transport.request('GET', CATALOG_PATH))
            entry['prices_checked_at'] = now()
            with localcontext() as ctx:
                ctx.prec = 100
                envelope = max(exact(row['seconds_rub']) for row in entry['routes']) * 60
            entry['client_research_envelope_rub'] = str(envelope)
            if envelope > CALL_STOP:
                raise ProbeStop('research price envelope too large')
            if total + CALL_STOP > TOTAL_STOP:
                raise ProbeStop('remaining client budget too small')
            raw = audio(duration)
            entry.update(input_bytes=len(raw),input_sha256=hashlib.sha256(raw).hexdigest())
            payload = {'model':MODEL, 'input_audio':{'data':base64.b64encode(raw).decode('ascii'),'format':'wav'},
                       'language':'en','temperature':0}
            entry['state'] = 'sending'
            report['paid_attempts'] += 1
            checkpoint(report)  # Durable before POST: reusing this run directory is forbidden.
            response = transport.request('POST','/audio/transcriptions',payload)
            entry['http_status'] = response.status
            identity = response.headers.get('x-generation-id')
            if isinstance(identity,str) and GEN_ID.fullmatch(identity):
                entry['generation_id'] = identity
            entry.update(inspect_response(response))
            entry['state'] = 'reported'
            observed = exact(entry['usage']['cost'])
            with localcontext() as ctx:
                ctx.prec = 100
                total += observed
            report['known_cost_rub'] = str(total)
            checkpoint(report)
            entry['generation'] = generation(transport, entry['generation_id'])
            if exact(entry['generation']['total_cost']) != observed:
                raise ProbeStop('usage and generation costs differ')
            if observed > CALL_STOP or total > TOTAL_STOP:
                raise ProbeStop('observed client spending stop exceeded')
            entry['state'] = 'verified-response'
            checkpoint(report)
        except (Exception, KeyboardInterrupt) as exc:
            entry['state_before_stop'] = entry['state']
            entry['state'] = 'stopped-no-retry'
            # Do not serialize exception text: it can contain secrets or response bodies.
            entry['error_class'] = type(exc).__name__
            if isinstance(exc,ProbeStop):
                entry['stop_reason'] = str(exc)
            report['state'] = 'stopped-no-retry'
            report['finished_at'] = now()
            checkpoint(report)
            return report
    report.update(state='completed-three-probes', finished_at=now())
    checkpoint(report)
    return report


def key_from_env_file(path):
    values = []
    for line in Path(path).read_text(encoding='utf-8-sig').splitlines():
        match = re.fullmatch(r'\s*(?:export\s+)?ROUTERAI_API_KEY\s*=\s*(.*?)\s*', line)
        if match:
            val = match[1]
            if val.startswith(('"',"'")):
                val = ast.literal_eval(val)
            else:
                val = val.split(' #',1)[0].rstrip()
            values.append(val)
    if (len(values) != 1 or not isinstance(values[0],str)
            or not re.fullmatch(r'[!-~]{8,512}', values[0]) or '${' in values[0]):
        raise ValueError('key unavailable or unsupported env format')
    return values[0]


def save(run_dir, report):
    fd, name = tempfile.mkstemp(prefix='.report-', dir=run_dir)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as out:
            json.dump(report,out,ensure_ascii=False,indent=2,allow_nan=False)
            out.write('\n'); out.flush(); os.fsync(out.fileno())
        os.replace(name,run_dir/'report.json')
    finally:
        if os.path.exists(name):
            os.unlink(name)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--env-file', type=Path)
    parser.add_argument('--run-dir', type=Path)
    args = parser.parse_args(argv)
    if not args.execute:
        print(json.dumps(plan(),ensure_ascii=False,indent=2))
        return 0
    if args.run_dir is None:
        parser.error('--execute requires a new --run-dir')
    try:
        # Atomic, exclusive directory is also the restart/no-repeat marker.
        args.run_dir.mkdir(mode=0o700)
        save(args.run_dir,{**plan(),'state':'initializing-no-resume'})
        key = key_from_env_file(args.env_file) if args.env_file else os.environ.get('ROUTERAI_API_KEY','')
        if not re.fullmatch(r'[!-~]{8,512}',key):
            raise ValueError('key unavailable')
        report = experiment(Transport(key),lambda value:save(args.run_dir,value))
    except (Exception, KeyboardInterrupt):
        print('Probe not completed; details suppressed. Do not retry with another directory.')
        return 1
    print(json.dumps({'state':report['state'],'paid_attempts':report['paid_attempts'],
                      'known_cost_rub':report['known_cost_rub']}))
    return 0 if report['state']=='completed-three-probes' else 1


if __name__ == '__main__':
    raise SystemExit(main())
