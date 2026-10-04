"""Independently verify expanded output against raw API records and model rules."""
import json
import math

from audit_local import ROOT, coordinates, distance, read_csv
from collect_entrances import credential


def main():
    d = json.loads((ROOT / 'output/expanded_results.json').read_text(encoding='utf-8'))
    assert d['collection_completed'], 'Collection is incomplete'
    samples = {s['sample_id']: s for s in d['samples']}
    exits = {(e['station'], e['entrance_poi_id']): e for e in d['entrances']}
    assert len(samples) == len(d['samples'])
    assert len(exits) == len(d['entrances'])
    expected = {(s['sample_id'], e['entrance_poi_id']) for s in samples.values() for e in exits.values() if s['station'] == e['station']}
    assert {(r['sample_id'], r['entrance_id']) for r in d['routes']} == expected
    assert len(d['routes']) == len(expected)
    valid_total = sum(r['status'] == 'model_route' for r in d['routes'])
    expanded = [r for r in d['routes'] if r['source'] != 'legacy_raw_snapshot']
    expanded_valid = sum(r['status'] == 'model_route' for r in expanded)
    assert d['quality']['expected_routes'] == len(expected)
    assert d['quality']['valid_routes'] == valid_total
    assert d['quality']['geometry_pass_rate'] == valid_total / len(expected)
    assert d['quality']['new_geometry_pass_rate'] == expanded_valid / len(expanded)
    assert d['quality']['expansion_model_gate_pass'] == (valid_total / len(expected) >= .95 and expanded_valid / len(expanded) >= .95)
    old = json.loads((ROOT / 'output/access_results.json').read_text(encoding='utf-8'))
    old_pairs = {(r['sample_id'],r['entrance_id']) for r in old['routes']}
    assert old_pairs.issubset(expected)
    assert d['reused_legacy_routes'] == len(old_pairs), 'Legacy queries were recollected'
    for r in d['routes']:
        s, e = samples[r['sample_id']], exits[(r['station'], r['entrance_id'])]
        raw_path = ROOT / r['raw_directory'] / (r['request_id'] + '.json')
        raw = json.loads(raw_path.read_text(encoding='utf-8'))
        assert raw['endpoint'] == '/direction/walking' and str(raw['response']['status']) == '1'
        assert coordinates(raw['params']['origin']) == (s['lng'], s['lat'])
        assert coordinates(raw['params']['destination']) == (e['lng'], e['lat'])
        paths = (raw['response'].get('route') or {}).get('paths') or []
        if not paths:
            assert r['status'] == 'no_route'
            continue
        path = min(paths, key=lambda p: float(p['distance']))
        assert float(path['distance']) == r['distance_m']
        assert float(path.get('duration') or 0) == r['api_duration_s']
        poly = []
        for step in path.get('steps') or []:
            for value in str(step.get('polyline', '')).split(';'):
                point = coordinates(value)
                if point and (not poly or list(point) != poly[-1]): poly.append(list(point))
        assert poly == r['polyline']
        assert all(math.isfinite(v) for point in poly for v in point)
        length = sum(distance(a, b) for a, b in zip(poly, poly[1:]))
        start = distance((s['lng'], s['lat']), poly[0]) if poly else None
        end = distance((e['lng'], e['lat']), poly[-1]) if poly else None
        sane = bool(poly and r['distance_m'] > 0 and start <= 60 and end <= 60 and abs(length-r['distance_m']) <= max(50, .25*r['distance_m']))
        assert r['status'] == ('model_route' if sane else 'geometry_review')
        assert abs(length - r['geometry_length_m']) < 1e-8
        assert start == r['snap_start_m'] and end == r['snap_end_m']
    for s in samples.values():
        scenarios = [r for r in d['scenarios'] if r['sample_id'] == s['sample_id']]
        assert len(scenarios) == 4 and {tuple((r['speed_kmh'],r['threshold_min'])) for r in scenarios} == {(4.5,10),(4.5,15),(3.5,10),(3.5,15)}
        valid = [r for r in d['routes'] if r['sample_id'] == s['sample_id'] and r['status'] == 'model_route']
        best = min(valid, key=lambda r:(r['distance_m'],r['entrance_id'])) if valid else None
        complete = len(valid) == sum(e['station'] == s['station'] for e in exits.values())
        for r in scenarios:
            assert r['commuter_group'] == s['commuter_group']
            assert r['best_distance_m'] == (best['distance_m'] if best else '')
            assert r['best_request_id'] == (best['request_id'] if best else '')
            assert r['minimum_confirmed_within_candidate_set'] == complete
            time = best['distance_m'] / (r['speed_kmh'] * 1000 / 60) if best else None
            status = 'model_covered' if best and time <= r['threshold_min'] else 'model_not_covered' if complete else 'unknown'
            assert r['model_status'] == status
            if best: assert abs(r['best_time_min'] - time) <= 1e-6
        by = {(r['speed_kmh'],r['threshold_min']):r for r in scenarios}
        for speed in [3.5,4.5]:
            assert not (by[speed,10]['model_status'] == 'model_covered' and by[speed,15]['model_status'] != 'model_covered')
        for threshold in [10,15]:
            assert not (by[3.5,threshold]['model_status'] == 'model_covered' and by[4.5,threshold]['model_status'] != 'model_covered')
    key, _ = credential()
    outputs = [ROOT / 'output/expanded_routes.json', ROOT / 'output/expanded_results.json', ROOT / 'data/processed/expanded_accessibility.csv']
    outputs += list((ROOT / 'data/raw/expanded_access_routes').glob('*.json'))
    assert all(key.encode() not in p.read_bytes() for p in outputs), 'Credential leaked'
    csv = read_csv(ROOT / 'data/processed/expanded_accessibility.csv')
    assert len(csv) == len(d['scenarios'])
    for r, row in zip(d['scenarios'], csv):
        assert r['sample_id'] == row['sample_id'] and r['model_status'] == row['model_status']
        assert r['best_request_id'] == row['best_request_id']
    summary = {'raw_routes_match_results': True, 'route_minima_verified': True,
        'geometry_checks_pass': True, 'scenario_monotonicity_pass': True, 'csv_matches': True,
        'no_credentials_in_outputs': True, 'samples': len(samples), 'routes': len(d['routes']),
        'expansion_model_gate_pass': d['quality']['expansion_model_gate_pass'],
        'physical_passability_verified': False, 'physical_passability_is_gate': False}
    d['independent_validation'] = summary
    (ROOT / 'output/expanded_results.json').write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary), flush=True)


if __name__ == '__main__': main()
