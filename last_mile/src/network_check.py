"""Independent network structure, geometry, scenario, and baseline checks; no API."""
import json
import math
from collections import Counter

from audit_local import ROOT
from network_expand import BASE, STATE, load, dump


def meters(a, b):
    x1, y1, x2, y2 = map(math.radians, (*a, *b))
    return 12742017.6 * math.asin(min(1, math.sqrt(math.sin((y2-y1)/2)**2 + math.cos(y1)*math.cos(y2)*math.sin((x2-x1)/2)**2)))


def check():
    data = load(ROOT / 'output/expanded_results.json')
    old = load(BASE / 'output/expanded_results.json')
    errors = []
    def require(test, message):
        if not test:
            errors.append(message)
    samples = {s['sample_id']: s for s in data['samples']}
    entrances = {(e['station'], e['entrance_poi_id']): e for e in data['entrances']}
    routes = {(r['sample_id'], r['entrance_id']): r for r in data['routes']}
    require(len(samples) == len(data['samples']), 'duplicate samples')
    require(len(routes) == len(data['routes']), 'duplicate route pairs')
    require(len(entrances) == len(data['entrances']), 'duplicate entrance pairs')
    for row in old['routes']:
        require(routes.get((row['sample_id'], row['entrance_id'])) == row, 'baseline route changed: '+row['sample_id'])
    for row in old['samples']:
        require(samples.get(row['sample_id']) == row, 'baseline sample changed: '+row['sample_id'])
    old_scenarios = {(r['sample_id'], float(r['speed_kmh']), int(r['threshold_min'])): r for r in old['scenarios']}
    old_names = {s['station'] for s in old['samples']}
    for station in {s['station'] for s in data['samples']} - old_names:
        ss = [s for s in data['samples'] if s['station'] == station]
        counts = Counter(s['category'] for s in ss)
        require(counts['住宅'] <= 4 and counts['写字楼'] <= 2 and counts['大学校门'] <= 2, 'sample quota: '+station)
        require(len({s['entity_id'] for s in ss}) == len(ss), 'duplicate entity: '+station)
        for s in ss:
            cap = 1500 if s['commuter_group'] == 'student' else 1000
            require(float(s['straight_to_station_m']) <= cap, 'sample radius: '+s['sample_id'])
            primary = s.get('source_typecode', '').split('|')[0]
            if s['category'] == '住宅':
                require(primary in ['120300','120301','120302'], 'residential primary type: '+s['sample_id'])
            if s['category'] == '写字楼':
                require(primary.startswith('1202'), 'office primary type: '+s['sample_id'])
    for r in data['routes']:
        sample = samples.get(r['sample_id'])
        entrance = entrances.get((r['station'], r['entrance_id']))
        require(bool(sample and entrance), 'orphan route')
        if not sample or not entrance:
            continue
        require(r['origin'] == [sample['lng'], sample['lat']] and r['destination'] == [entrance['lng'], entrance['lat']], 'route endpoints mismatch: '+r['sample_id'])
        if r['status'] == 'model_route':
            poly = r['polyline']
            length = sum(meters(a,b) for a,b in zip(poly,poly[1:]))
            require(bool(poly) and meters(r['origin'],poly[0]) <= 60 and meters(r['destination'],poly[-1]) <= 60 and abs(length-r['distance_m']) <= max(50, .25*r['distance_m']), 'invalid model geometry: '+r['sample_id'])
    for row in data['scenarios']:
        rr = [r for r in data['routes'] if r['sample_id'] == row['sample_id']]
        valid = [r for r in rr if r['status'] == 'model_route']
        expected = sum(e['station'] == row['station'] for e in data['entrances'])
        best = min((r['distance_m'] for r in valid), default=None)
        covered = best is not None and best/(float(row['speed_kmh'])*1000/60) <= int(row['threshold_min'])
        status = 'model_covered' if covered else 'model_not_covered' if len(valid) == expected else 'unknown'
        require(row['model_status'] == status, 'scenario mismatch: '+row['sample_id'])
        original = old_scenarios.get((row['sample_id'],float(row['speed_kmh']),int(row['threshold_min'])))
        if original:
            for field in ['model_status','best_distance_m','best_time_min','best_entrance_id','tested_entrances','valid_entrances','expected_entrances']:
                require(row[field] == original[field], 'baseline scenario changed: '+row['sample_id']+' '+field)
    require(len(data['routes']) == data['expected_routes'], 'route count incomplete')
    names = [s['station'] for s in data['station_catalog']]
    require(len(names) == len(set(names)), 'duplicate catalog stations')
    ledger = load(STATE)
    require(ledger['attempts'] <= 6000, 'global budget exceeded')
    require(sum(b['attempts'] for b in ledger['batches'].values()) == ledger['attempts'], 'budget ledger mismatch')
    report = {'passed': not errors, 'errors': errors, 'samples': len(samples), 'routes': len(routes), 'catalog_stations': len(names),
              'baseline_samples_preserved': len(old['samples']), 'baseline_routes_preserved': len(old['routes']),
              'route_statuses': dict(Counter(r['status'] for r in data['routes'])), 'network_attempts': ledger['attempts']}
    dump(ROOT / 'output/network_validation.json', report)
    data['independent_validation'] = report
    dump(ROOT / 'output/expanded_results.json', data)
    print(json.dumps(report, ensure_ascii=True))
    if errors:
        raise SystemExit(1)


if __name__ == '__main__':
    check()
