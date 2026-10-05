"""Checkpointed network expansion. No API calls in plan/publish modes."""
import argparse
import hashlib
import json
import re
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from audit_local import ROOT, coordinates, distance, read_csv
from collect_entrances import SafeClient
from expand_samples import Collector, new_entrances, residential_office, string
from extend_round import RoundCollector, universities, csv_stage
from analyze_expanded import ASSUMPTION, params_for, parse_route, summarize

STATE = ROOT / 'data/processed/network_ledger.json'
RAW = ROOT / 'data/raw/network_sampling'
BASE = ROOT / 'output/history/network_before'
CHECKPOINTS = ROOT / 'data/processed/network_checkpoints'
TERMINAL = {'analyzed', 'no_entrances', 'no_samples', 'needs_review', 'failed'}


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def dump(path, data):
    if path == STATE:
        data = {**data, 'stations': {name: {k: entry[k] for k in ['analysis_status', 'error'] if k in entry}
                                  for name, entry in data['stations'].items()}}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


def checkpoint(name, entry, ledger):
    # Large route/evidence payloads are saved only for the current station.
    token = hashlib.sha256(name.encode()).hexdigest()[:20]
    dump(CHECKPOINTS / (token + '.json'), entry)
    dump(STATE, ledger)


def key(endpoint, params):
    return json.dumps({'endpoint': endpoint, 'params': params}, sort_keys=True, ensure_ascii=False)


class StopCollection(RuntimeError):
    pass


class CachedClient:
    def __init__(self, ledger, batch, budget, global_budget):
        self.ledger, self.batch = ledger, batch
        self.budget, self.global_budget = budget, min(global_budget, 6000)
        self.directory, self.calls, self.cache = RAW, 0, {}
        self.inner = None
        RAW.mkdir(parents=True, exist_ok=True)
        # One scan per process, including historical caches; exact full parameters only.
        for path in sorted((ROOT / 'data/raw').rglob('*.json')):
            try:
                raw = load(path)
                if raw.get('endpoint') and isinstance(raw.get('params'), dict) and str(raw.get('response', {}).get('status')) == '1':
                    self.cache[key(raw['endpoint'], raw['params'])] = (raw, path)
            except (ValueError, AttributeError):
                continue

    def get(self, endpoint, params):
        signature = key(endpoint, params)
        if signature in self.cache:
            raw, path = self.cache[signature]
            return raw['response'], path.stem, raw['collected_at_utc']
        for retry in range(2):
            if self.batch['attempts'] >= self.budget or self.ledger['attempts'] >= self.global_budget:
                raise StopCollection('Authorized request budget reached')
            if self.inner is None:
                self.inner = SafeClient(6000)
                self.inner.directory = RAW
            # Reserve before network I/O: even a crash cannot evade the persistent cap.
            self.batch['attempts'] += 1
            self.ledger['attempts'] += 1
            self.calls += 1
            dump(STATE, self.ledger)
            try:
                data, rid, when = self.inner.get(endpoint, params)
            except RuntimeError as exc:
                if str(exc).startswith('Transport failure:') and retry == 0:
                    continue
                raise StopCollection(str(exc)) from None
            raw = {'endpoint': endpoint, 'params': params, 'response': data, 'collected_at_utc': when}
            self.cache[signature] = (raw, RAW / (rid + '.json'))
            return data, rid, when


class NetworkCollector(RoundCollector):
    def __init__(self, client):
        self.client = client
        self.queries, self.pois, self.campus_review, self.rejected_pois = [], [], [], []


def verify_station(c, station):
    pois, rid, when = c.query('/place/detail', {'id': station['station_id'], 'extensions': 'all'}, station['station'], 'station_parent_verification')
    found = [p for p in pois if p.get('id') == station['station_id']]
    if len(found) != 1:
        raise ValueError('Station parent POI not uniquely confirmed')
    poi = found[0]
    point = coordinates(string(poi, 'location'))
    if station['station'] not in string(poi, 'name') or string(poi, 'typecode').split('|')[0] != '150500':
        raise ValueError('Station parent name/main type mismatch')
    if not point or distance(point, (float(station['lng']), float(station['lat']))) > 100:
        raise ValueError('Station detail coordinates differ by more than 100m')
    mentioned = set(re.findall(r'[1-9]\d*号线', string(poi, 'address') + string(poi, 'name')))
    if not mentioned.intersection(station['lines']):
        raise ValueError('No catalog line confirmed by station detail')
    return {'request_id': rid, 'collected_at_utc': when, 'official_name': poi['name'], 'detail_lines': sorted(mentioned), 'catalog_lines': station['lines']}


def snapshot():
    manifest = BASE / 'snapshot_manifest.json'
    if manifest.exists():
        return
    paths = list((ROOT / 'data/processed').glob('expanded_*')) + list((ROOT / 'output').glob('expanded_*'))
    for path in paths:
        target = BASE / path.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copy2(path, target)
    dump(manifest, {'files': [str(p.relative_to(ROOT)) for p in paths]})


def station_collect(c, station, entry):
    if 'samples' in entry and not entry['samples']:
        entry['analysis_status'] = 'no_samples' if any(e['baseline_eligible'] for e in entry.get('entrances', [])) else 'no_entrances'
        return
    if 'samples' not in entry:
        entry['verification'] = verify_station(c, station)
        entrances = new_entrances(c, station)
        entry['entrances'] = entrances
        if not any(e['baseline_eligible'] for e in entrances):
            entry.update(analysis_status='no_entrances', samples=[], routes=[])
            return
        samples = residential_office(c, station, strict_primary=True)
        students, discovery = universities(c, station)
        samples.extend(students)
        samples = list({s['sample_id']: s for s in samples}.values())
        for sample in samples:
            sample.setdefault('selection_radius_m', 1000)
            sample.setdefault('selection_scope', 'primary_1000m')
            evidence = next((p['poi'] for p in c.pois if p['request_id'] == sample['source_request_id'] and p['poi'].get('id') == sample['source_poi_id']), None)
            if evidence is None:
                raise ValueError('Missing selected sample POI evidence')
            sample['source_typecode'], sample['source_type'] = string(evidence, 'typecode'), string(evidence, 'type')
        entry.update(samples=samples, routes=[], campus_review=c.campus_review[:], university_discovery=discovery,
                     queries=c.queries[:], rejected_pois=c.rejected_pois[:], poi_evidence=c.pois[:])
        if not samples:
            entry['analysis_status'] = 'no_samples'
            return
        checkpoint(station['station'], entry, c.client.ledger)
    previous = {(r['sample_id'], r['entrance_id']): r for r in entry['routes']}
    for sample in entry['samples']:
        for entrance in entry['entrances']:
            if not entrance['baseline_eligible']:
                continue
            pair = (sample['sample_id'], entrance['entrance_poi_id'])
            params = params_for(sample, entrance)
            if pair in previous and previous[pair]['request_params'] == params:
                continue
            data, rid, when = c.client.get('/direction/walking', params)
            _, path = c.client.cache[key('/direction/walking', params)]
            row = parse_route(sample, entrance, data, rid, when, str(path.parent.relative_to(ROOT)), 'network_official_cache')
            if pair in previous:
                entry['routes'].remove(previous[pair])
            entry['routes'].append(row)
            checkpoint(station['station'], entry, c.client.ledger)
    entry['analysis_status'] = 'analyzed' if all(r['status'] == 'model_route' for r in entry['routes']) else 'needs_review'


def publish(catalog, ledger):
    baseline = load(BASE / 'output/expanded_results.json')
    samples = baseline['samples'][:]
    entrances = read_csv(BASE / 'data/processed/expanded_entrances.csv')
    routes = baseline['routes'][:]
    reviews = read_csv(BASE / 'data/processed/expanded_campus_gate_review.csv')
    baseline_names = {s['station'] for s in samples}
    for name, entry in ledger['stations'].items():
        if name in baseline_names or entry['analysis_status'] not in TERMINAL or entry['analysis_status'] == 'failed':
            continue
        samples.extend(entry.get('samples', []))
        entrances.extend(entry.get('entrances', []))
        routes.extend(entry.get('routes', []))
        reviews.extend(entry.get('campus_review', []))
    statuses = []
    for station in catalog:
        name = station['station']
        statuses.append({**station, 'analysis_status': 'analyzed' if name in baseline_names else ledger['stations'].get(name, {}).get('analysis_status', 'pending'),
                         'sample_count': sum(s['station'] == name for s in samples), 'route_count': sum(r['station'] == name for r in routes)})
    csv_stage(ROOT / 'data/processed/expanded_samples.csv', samples)
    csv_stage(ROOT / 'data/processed/expanded_entrances.csv', entrances)
    if reviews:
        csv_stage(ROOT / 'data/processed/expanded_campus_gate_review.csv', reviews)
    eligible = [e for e in entrances if str(e['baseline_eligible']).lower() == 'true']
    for row in samples + eligible:
        row['lng'], row['lat'] = float(row['lng']), float(row['lat'])
    report = {'samples': samples, 'entrances': eligible, 'routes': routes, 'scenario_assumption': ASSUMPTION,
              'station_catalog': statuses, 'collection_completed': True,
              'updated_at_utc': datetime.now(timezone.utc).isoformat(),
              'expected_routes': sum(e['station'] == s['station'] for s in samples for e in eligible),
              'network_requests': ledger['attempts']}
    dump(ROOT / 'output/expanded_routes.json', report)
    summarize()


def run(args):
    catalog = load(args.catalog)['stations']
    assert len({s['station'] for s in catalog}) == len(catalog), 'Duplicate station names'
    ledger = load(STATE) if STATE.exists() else {'attempts': 0, 'stations': {}, 'batches': {}}
    for name, entry in list(ledger['stations'].items()):
        token = hashlib.sha256(name.encode()).hexdigest()[:20]
        path = CHECKPOINTS / (token + '.json')
        if path.exists():
            ledger['stations'][name] = {**load(path), **entry}
    baseline_path = BASE / 'output/expanded_results.json'
    baseline = load(baseline_path if baseline_path.exists() else ROOT / 'output/expanded_results.json')
    existing = {s['station'] for s in baseline['samples']}
    retry_statuses = (({'failed'} if args.retry_failed else set())
                      | ({'no_entrances'} if args.retry_missing else set())
                      | ({'no_samples'} if args.retry_empty else set()))
    candidates = [s for s in catalog if s['station'] not in existing and (ledger['stations'].get(s['station'], {}).get('analysis_status') not in TERMINAL or ledger['stations'].get(s['station'], {}).get('analysis_status') in retry_statuses)
                  and (not args.line or args.line in s['lines']) and s.get('operating_status') not in ['planned', 'under_construction', 'closed']]
    batch_id = str(args.batch)
    selected = candidates[:args.size]
    if batch_id in ledger['batches']:
        selected = [s for s in catalog if s['station'] in ledger['batches'][batch_id]['stations']]
    if args.mode == 'plan':
        print(json.dumps({'batch': batch_id, 'stations': [s['station'] for s in selected], 'remaining': len(candidates), 'prior_attempts': ledger['attempts']}, ensure_ascii=False))
        return
    snapshot()
    if args.mode == 'publish':
        publish(catalog, ledger)
        return
    if args.budget <= 0:
        raise ValueError('collect requires an explicitly positive --budget')
    batch = ledger['batches'].setdefault(batch_id, {'stations': [s['station'] for s in selected], 'attempts': 0})
    client = CachedClient(ledger, batch, args.budget, args.global_budget)
    dump(STATE, ledger)
    for station in selected:
        name = station['station']
        entry = ledger['stations'].setdefault(name, {'analysis_status': 'pending'})
        if entry['analysis_status'] in retry_statuses:
            entry.clear()
            entry['analysis_status'] = 'pending'
            checkpoint(name, entry, ledger)
        if entry['analysis_status'] in TERMINAL:
            continue
        print(json.dumps({'station': name, 'event': 'start', 'batch_attempts': batch['attempts']}, ensure_ascii=True), flush=True)
        try:
            station_collect(NetworkCollector(client), station, entry)
            entry.pop('error', None)
        except StopCollection as exc:
            entry.update(analysis_status='pending', error=str(exc))
            checkpoint(name, entry, ledger)
            print(json.dumps({'stopped': str(exc), 'station': name}), flush=True)
            raise SystemExit(2)
        except ValueError as exc:
            entry.update(analysis_status='failed', error=str(exc))
        checkpoint(name, entry, ledger)
        print(json.dumps({'station': name, 'status': entry['analysis_status'], 'samples': len(entry.get('samples', [])), 'routes': len(entry.get('routes', [])), 'batch_attempts': batch['attempts']}, ensure_ascii=True), flush=True)
    publish(catalog, ledger)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['plan', 'collect', 'publish'])
    parser.add_argument('--catalog', type=Path, default=ROOT / 'data/processed/network_stations.json')
    parser.add_argument('--batch', type=int, default=1)
    parser.add_argument('--size', type=int, default=26)
    parser.add_argument('--line')
    parser.add_argument('--budget', type=int, default=0)
    parser.add_argument('--global-budget', type=int, default=6000)
    parser.add_argument('--retry-failed', action='store_true', help='Explicitly retry failed station verification or sampling')
    parser.add_argument('--retry-missing', action='store_true', help='Explicitly resample no_entrances stations; preserve request cache and budgets')
    parser.add_argument('--retry-empty', action='store_true', help='Explicitly resample no_samples stations; preserve request cache and budgets')
    run(parser.parse_args())
