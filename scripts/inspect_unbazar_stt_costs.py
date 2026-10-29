"""Read bounded, non-content STT cost evidence from an existing bot SQLite.

No network, credentials, inference, migrations or catalog publication. Legacy
REAL amounts are approximate observations, never verified prices or invoices.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
import json
import os
from pathlib import Path
import sqlite3
import time

MODELS = ('openai/whisper-large-v3-turbo', 'openai/whisper-large-v3')
REQUIRED = {'provider_model', 'cost', 'duration_seconds', 'created_at', 'status'}


def number(value, *, positive=False):
    if value is None or len(str(value)) > 160:
        raise ValueError('invalid numeric observation')
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise ValueError('invalid numeric observation') from None
    if (not result.is_finite() or result < 0 or result > Decimal('1e12')
            or len(result.as_tuple().digits) > 80 or (result and result.adjusted() < -80)
            or (positive and not result)):
        raise ValueError('invalid numeric observation')
    return result


def inspect(database, *, limit=100):
    if type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError('limit must be from 1 through 200')
    database = Path(database).resolve(strict=True)
    if not database.is_file():
        raise ValueError('existing SQLite file required')
    started = time.monotonic()
    observations = []
    skipped = 0
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=2)) as conn:
        conn.execute('PRAGMA query_only=ON')
        conn.set_progress_handler(lambda: int(time.monotonic() - started > 4), 1000)
        conn.execute('BEGIN')
        columns = {row[1] for row in conn.execute('PRAGMA table_info(transcription_jobs)')}
        if not REQUIRED <= columns:
            raise ValueError('unsupported transcription_jobs schema')
        exact_column = 'cost_rub' if 'cost_rub' in columns else 'NULL'
        # The only selected runtime fields are model, cost, duration and date.
        # Never select transcript, file IDs, user/chat IDs, secrets or raw JSON.
        rows = conn.execute(
            'SELECT provider_model,cost,duration_seconds,created_at,' + exact_column +
            " FROM transcription_jobs WHERE status='done' AND provider_model IN (?,?)"
            ' ORDER BY created_at DESC LIMIT ?', (*MODELS, limit)).fetchall()
        conn.rollback()
    for model, legacy_cost, seconds, created, exact_cost in rows:
        try:
            cost = number(exact_cost if exact_cost is not None else legacy_cost)
            duration = number(seconds, positive=True)
            if duration > 86400:
                raise ValueError('invalid duration')
            # Emit only a calendar date, never an arbitrary stored string.
            parsed = datetime.fromisoformat(created.replace('Z', '+00:00'))
            if parsed.tzinfo is None:
                raise ValueError('observation timezone missing')
            day = parsed.astimezone(timezone.utc).date().isoformat()
            with localcontext() as ctx:
                ctx.prec = 50
                ratio = cost / duration
            observations.append({'model': model, 'day_utc': day,
                'reported_cost_rub': str(cost), 'reported_seconds': str(duration),
                'effective_rub_per_second': str(ratio),
                'cost_precision': 'decimal-text' if exact_cost is not None else 'legacy-approximate',
                'provider_route': 'not-recorded-by-this-query'})
        except (ValueError, TypeError, AttributeError, InvalidOperation):
            skipped += 1
    return {'schema': 1, 'kind': 'unbazar-stt-cost-observations',
        'collected_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'scope': 'historical-reported-metadata-only', 'inference_requests': 0,
        'rows_examined': len(rows), 'rows_skipped': skipped, 'observations': observations,
        'limitations': ['not a verified tariff or provider invoice',
            'legacy REAL is approximate; duration is provider-reported',
            'provider route and historical exchange rate are not established',
            'zero or missing observations do not authorize a new paid request']}


def save_report(report, output):
    output = Path(output)
    # The caller chooses an existing research directory outside runtime data.
    # Exclusive creation prevents overwriting SQLite, configuration or a report.
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
    except BaseException:
        output.unlink(missing_ok=True)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--limit', default=100, type=int)
    args = parser.parse_args(argv)
    try:
        report = inspect(args.database, limit=args.limit)
        save_report(report, args.output)
    except (ValueError, OSError, sqlite3.Error):
        print('Evidence unavailable: check confirmed paths, schema and read access; details suppressed.')
        return 1
    print(json.dumps({'report': 'saved', 'rows_examined': report['rows_examined'],
                      'observations': len(report['observations']), 'inference_requests': 0}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
