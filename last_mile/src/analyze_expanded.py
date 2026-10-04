"""Resumable multi-station model expansion with bounded requests and raw evidence."""
import argparse
import hashlib
import json
import math
from collections import Counter
from datetime import datetime, timezone

from audit_local import ROOT, coordinates, distance, read_csv, write_csv
from collect_entrances import SafeClient

ASSUMPTION = '门禁和临时封路不纳入本轮；结果为导航步行模型，公众通行未实地验证'
RAW = ROOT / 'data/raw/expanded_access_routes'


def inputs():
    selected = read_csv(ROOT / 'data/processed/expanded_samples.csv')
    exits = [r for r in read_csv(ROOT / 'data/processed/expanded_entrances.csv')
             if str(r.get('baseline_eligible', '')).lower() == 'true']
    for row in selected + exits:
        row['lng'], row['lat'] = float(row['lng']), float(row['lat'])
    assert len({r['sample_id'] for r in selected}) == len(selected)
    assert len({(e['station'], e['entrance_poi_id']) for e in exits}) == len(exits)
    assert all(any(e['station'] == s['station'] for e in exits) for s in selected), 'A sample station has no eligible entrance'
    return selected, exits


def params_for(sample, entrance):
    return {'origin': f"{sample['lng']},{sample['lat']}",
            'destination': f"{entrance['lng']},{entrance['lat']}"}


def cache_file(params):
    h = hashlib.sha256(json.dumps({'endpoint': '/direction/walking', 'params': params},
        sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:20]
    candidates = sorted(RAW.glob(h + '_*.json'))
    if candidates:
        path = candidates[-1]
        raw = json.loads(path.read_text(encoding='utf-8'))
        if str(raw['response'].get('status')) == '1':
            return path, raw
    return None, None


def geometry(origin, endpoint, poly, length):
    start = distance(origin, poly[0]) if poly else None
    end = distance(endpoint, poly[-1]) if poly else None
    measured = sum(distance(a, b) for a, b in zip(poly, poly[1:]))
    tolerance = max(50, .25 * length)
    sane = bool(poly and length > 0 and start <= 60 and end <= 60
                and abs(measured - length) <= tolerance)
    return sane, start, end, measured


def parse_route(s, e, data, request_id, when, raw_directory, source):
    origin, endpoint = (s['lng'], s['lat']), (e['lng'], e['lat'])
    row = {'sample_id': s['sample_id'], 'station': s['station'],
        'entrance_id': e['entrance_poi_id'], 'entrance_name': e['entrance_name'],
        'origin': list(origin), 'destination': list(endpoint),
        'request_params': params_for(s, e), 'request_id': request_id,
        'raw_directory': raw_directory, 'collected_at_utc': when, 'source': source,
        'status': 'unknown', 'polyline': [], 'steps': [], 'straight_m': distance(origin, endpoint)}
    paths = (data.get('route') or {}).get('paths') or []
    if not paths:
        row['status'] = 'no_route'
        return row
    path = min(paths, key=lambda p: float(p['distance']))
    poly = []
    for step in path.get('steps') or []:
        row['steps'].append({k: step.get(k, '') for k in ['instruction','road','distance','action','assistant_action']})
        for value in str(step.get('polyline', '')).split(';'):
            point = coordinates(value)
            if point and (not poly or list(point) != poly[-1]):
                poly.append(list(point))
    length = float(path['distance'])
    sane, start, end, measured = geometry(origin, endpoint, poly, length)
    row.update(status='model_route' if sane else 'geometry_review', distance_m=length,
        api_duration_s=float(path.get('duration') or 0), polyline=poly,
        snap_start_m=start, snap_end_m=end, geometry_length_m=measured,
        detour_ratio=length / row['straight_m'] if row['straight_m'] >= 50 else None,
        standard_time_4_5_min=length / 75,
        standard_time_3_5_min=length / (3500 / 60))
    return row


def collect(budget=180):
    selected, exits = inputs()
    old = json.loads((ROOT / 'output/access_results.json').read_text(encoding='utf-8'))
    previous = {(r['sample_id'], r['entrance_id']): r for r in old['routes']}
    RAW.mkdir(parents=True, exist_ok=True)
    jobs = [(s, e) for s in selected for e in exits if e['station'] == s['station']]
    required = sum((s['sample_id'], e['entrance_poi_id']) not in previous
                   and cache_file(params_for(s, e))[0] is None for s, e in jobs)
    if required > budget:
        raise RuntimeError(f'Preflight needs {required} new requests, exceeds authorized budget {budget}; shrink samples first')
    client = SafeClient(budget)
    client.directory = RAW
    routes = []
    report = {'samples': selected, 'entrances': exits, 'routes': routes,
        'scenario_assumption': ASSUMPTION, 'expected_routes': len(jobs),
        'new_calls': 0, 'requests_needed_at_start': required, 'request_budget': budget,
        'collection_completed': False, 'errors': [], 'reused_legacy_routes': 0,
        'reused_expanded_cache_routes': 0}

    def save():
        report['new_calls'] = client.calls
        snapshots = [json.loads(p.read_text(encoding='utf-8')) for p in RAW.glob('*.json')]
        report['expanded_raw_snapshots_total'] = len(snapshots)
        report['expanded_api_attempts_total'] = len(snapshots)
        report['expanded_successful_requests_total'] = sum(str(p['response'].get('status')) == '1' for p in snapshots)
        report['expanded_transport_failures_total'] = sum(bool(p.get('transport_error')) for p in snapshots)
        report['updated_at_utc'] = datetime.now(timezone.utc).isoformat()
        (ROOT / 'output/expanded_routes.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    for s, e in jobs:
        legacy = previous.get((s['sample_id'], e['entrance_poi_id']))
        params = params_for(s, e)
        try:
            if legacy:
                path = ROOT / 'data/raw/access_routes' / (legacy['request_id'] + '.json')
                raw = json.loads(path.read_text(encoding='utf-8'))
                assert raw['params'] == params, 'Legacy coordinates changed; reuse refused'
                data, rid, when = raw['response'], legacy['request_id'], raw['collected_at_utc']
                directory, source = 'data/raw/access_routes', 'legacy_raw_snapshot'
                report['reused_legacy_routes'] += 1
            else:
                path, raw = cache_file(params)
                if raw:
                    data, rid, when = raw['response'], path.stem, raw['collected_at_utc']
                    source = 'expanded_raw_snapshot'
                    report['reused_expanded_cache_routes'] += 1
                else:
                    data, rid, when = client.get('/direction/walking', params)
                    source = 'official_api'
                directory = 'data/raw/expanded_access_routes'
            routes.append(parse_route(s, e, data, rid, when, directory, source))
        except RuntimeError as exc:
            report['errors'].append({'sample_id': s['sample_id'], 'entrance_id': e['entrance_poi_id'], 'error': str(exc)})
            save()
            print(json.dumps({'collection_stopped': True, 'completed': len(routes), 'new_calls': client.calls, 'error': str(exc)}), flush=True)
            return
        if len(routes) % 10 == 0:
            save()
            print(json.dumps({'completed': len(routes), 'expected': len(jobs), 'new_calls': client.calls, 'station': s['station']}, ensure_ascii=True), flush=True)
    report['collection_completed'] = True
    save()
    print(json.dumps({'routes': len(routes), 'statuses': dict(Counter(r['status'] for r in routes)), 'new_calls': client.calls}), flush=True)


def summarize():
    data = json.loads((ROOT / 'output/expanded_routes.json').read_text(encoding='utf-8'))
    if not data.get('collection_completed') or len(data['routes']) != data['expected_routes']:
        raise RuntimeError('Route collection incomplete; preserve the last validated result and resume collection first')
    # Refresh provenance-only edits made while collection was running. A changed
    # coordinate or candidate set must go through collection and cache matching.
    latest_samples, latest_exits = inputs()
    assert {(s['sample_id'],s['lng'],s['lat']) for s in latest_samples} == {(s['sample_id'],s['lng'],s['lat']) for s in data['samples']}, 'Samples changed: rerun collection'
    assert {(e['station'],e['entrance_poi_id'],e['lng'],e['lat']) for e in latest_exits} == {(e['station'],e['entrance_poi_id'],e['lng'],e['lat']) for e in data['entrances']}, 'Entrances changed: rerun collection'
    data['samples'], data['entrances'] = latest_samples, latest_exits
    rows = []
    for s in data['samples']:
        routes = [r for r in data['routes'] if r['sample_id'] == s['sample_id']]
        expected = sum(e['station'] == s['station'] for e in data['entrances'])
        valid = [r for r in routes if r['status'] == 'model_route']
        best = min(valid, key=lambda r: (r['distance_m'], r['entrance_id'])) if valid else None
        complete = len(valid) == expected
        for speed in [4.5, 3.5]:
            for threshold in [10, 15]:
                time = best['distance_m'] / (speed * 1000 / 60) if best else None
                covered = best is not None and time <= threshold
                # Failed/ambiguous routing does not prove inaccessibility.
                status = 'model_covered' if covered else 'model_not_covered' if complete else 'unknown'
                rows.append({**s, 'speed_kmh': speed, 'threshold_min': threshold,
                    'model_status': status, 'best_distance_m': best['distance_m'] if best else '',
                    'best_time_min': round(time, 6) if best else '',
                    'best_api_duration_s': best['api_duration_s'] if best else '',
                    'best_entrance': best['entrance_name'] if best else '',
                    'best_entrance_id': best['entrance_id'] if best else '',
                    'best_detour_ratio': best.get('detour_ratio') if best else '',
                    'best_request_id': best['request_id'] if best else '',
                    'tested_entrances': len(routes), 'valid_entrances': len(valid),
                    'expected_entrances': expected, 'minimum_confirmed_within_candidate_set': complete,
                    'current_public_passability': 'excluded_from_scope', 'scenario_assumption': ASSUMPTION})
    write_csv(ROOT / 'data/processed/expanded_accessibility.csv', rows)
    total = data['expected_routes']
    valid_total = sum(r['status'] == 'model_route' for r in data['routes'])
    new_routes = [r for r in data['routes'] if r['source'] != 'legacy_raw_snapshot']
    new_valid = sum(r['status'] == 'model_route' for r in new_routes)
    overall_rate = valid_total / total if total else 0
    new_rate = new_valid / len(new_routes) if new_routes else 0
    data['scenarios'] = rows
    data['quality'] = {'route_total': len(data['routes']), 'expected_routes': total,
        'valid_routes': valid_total, 'sample_total': len(data['samples']),
        'new_route_total': len(new_routes), 'new_valid_routes': new_valid,
        'geometry_pass_rate': overall_rate, 'new_geometry_pass_rate': new_rate,
        'geometry_threshold': .95, 'expansion_model_gate_pass': bool(data['collection_completed'] and overall_rate >= .95 and new_rate >= .95),
        'physical_passability_verified': False, 'physical_passability_is_gate': False,
        'geometry_rule': 'Endpoint offsets <=60m each; absolute polyline-distance error <=max(50m,25% of API distance)',
        'unknown_scenarios': sum(r['model_status'] == 'unknown' for r in rows)}
    data['station_summary'] = [{
        'station': station, 'speed_kmh': speed, 'threshold_min': threshold,
        'samples': len(ss), 'covered': sum(r['model_status'] == 'model_covered' for r in ss),
        'not_covered': sum(r['model_status'] == 'model_not_covered' for r in ss),
        'unknown': sum(r['model_status'] == 'unknown' for r in ss),
        'coverage_lower': sum(r['model_status'] == 'model_covered' for r in ss) / len(ss),
        'coverage_upper': sum(r['model_status'] != 'model_not_covered' for r in ss) / len(ss)}
        for station in dict.fromkeys(s['station'] for s in data['samples'])
        for speed in [4.5, 3.5] for threshold in [10, 15]
        for ss in [[r for r in rows if r['station'] == station and r['speed_kmh'] == speed and r['threshold_min'] == threshold]]]
    (ROOT / 'output/expanded_results.json').write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'quality': data['quality'], 'station_summary': [s for s in data['station_summary'] if s['speed_kmh'] == 4.5]}, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['collect', 'summarize', 'estimate'])
    parser.add_argument('--budget', type=int, default=180)
    args = parser.parse_args()
    if args.mode == 'collect':
        collect(args.budget)
    elif args.mode == 'summarize':
        summarize()
    else:
        samples, entrances = inputs()
        print(json.dumps({'samples': len(samples), 'entrances': len(entrances),
            'pairs': sum(e['station'] == s['station'] for s in samples for e in entrances)}, ensure_ascii=True))
