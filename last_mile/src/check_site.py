"""Validate public data relationships and the generated static artifact (stdlib)."""
import csv
import json
import math
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]

def require(condition, message):
    if not condition:
        raise ValueError(message)

def unique(rows, fields):
    keys = [tuple(row[field] for field in fields) for row in rows]
    require(len(keys) == len(set(keys)), 'Duplicate identifiers: ' + ','.join(fields))
    return set(keys)

def validate_data(data):
    for name in ('samples', 'entrances', 'routes', 'scenarios', 'station_summary'):
        require(isinstance(data.get(name), list) and data[name], 'Missing/non-list ' + name)
    sample_ids = unique(data['samples'], ('sample_id',))
    entrance_ids = unique(data['entrances'], ('entrance_poi_id',))
    unique(data['routes'], ('sample_id', 'entrance_id'))
    unique(data['scenarios'], ('sample_id', 'speed_kmh', 'threshold_min'))
    unique(data['station_summary'], ('station', 'speed_kmh', 'threshold_min'))
    samples = {r['sample_id']: r for r in data['samples']}
    entrances = {r['entrance_poi_id']: r for r in data['entrances']}
    for row in data['samples'] + data['entrances']:
        require(row.get('coordinate_system') == 'GCJ-02', 'Unexpected source coordinate system')
        require(math.isfinite(float(row['lng'])) and -180 <= float(row['lng']) <= 180, 'Invalid longitude')
        require(math.isfinite(float(row['lat'])) and -90 <= float(row['lat']) <= 90, 'Invalid latitude')
    for route in data['routes']:
        require((route['sample_id'],) in sample_ids and (route['entrance_id'],) in entrance_ids, 'Dangling route reference')
        require(route['station'] == samples[route['sample_id']]['station'] == entrances[route['entrance_id']]['station'], 'Route station mismatch')
        if route.get('status') == 'model_route':
            require(len(route.get('polyline', [])) >= 2, 'Model route missing geometry')
            for point in route['polyline']:
                require(len(point) == 2 and all(isinstance(n, (int, float)) and math.isfinite(n) for n in point), 'Invalid route point')
    for row in data['scenarios']:
        require((row['sample_id'],) in sample_ids, 'Dangling scenario reference')
    require(data['expected_routes'] == len(data['routes']), 'Expected route count differs')
    require(data['quality']['route_total'] == len(data['routes']), 'Quality route count differs')
    require(data['quality']['sample_total'] == len(data['samples']), 'Quality sample count differs')
    for row in data['station_summary']:
        require(row['samples'] == sum(s['station'] == row['station'] for s in data['samples']), 'Summary sample count differs')
        require(row['covered'] + row['not_covered'] + row['unknown'] == row['samples'], 'Summary category count differs')

def scan_public(root):
    patterns = [r'gh[pousr]_[A-Za-z0-9]{20,}', r'github_pat_[A-Za-z0-9_]{20,}',
                r'AKIA[0-9A-Z]{16}', r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
                r'(?i)(?:api[_-]?key|access[_-]?token|secret)\s*["\x27]?\s*[:=]\s*["\x27][A-Za-z0-9_-]{24,}',
                r'(?i)[A-Z]:[\\/](?:Users|Downloads|home|projects)[\\/]',
                r'(?i)[A-Z]:[\\/]Downloads[\\/]', r'/Users/[^/]+/', r'/home/[^/]+/']
    for path in root.rglob('*'):
        require(not path.is_symlink(), 'Symlink in artifact')
        if path.is_file() and path.suffix in {'.html', '.js', '.css', '.json', '.csv', '.md', '.txt', '.svg'}:
            content = path.read_text(encoding='utf-8-sig')
            for pattern in patterns:
                require(not re.search(pattern, content), 'Possible credential/local path in ' + str(path.relative_to(root)))

def main():
    site = ROOT / '_site'
    for name in ('index.html', '.nojekyll', 'api/v1/manifest.json', 'api/v1/results.json', 'api/v1/stations.json', 'downloads/analysis-report.md'):
        require((site / name).is_file(), 'Missing artifact ' + name)
    data = json.loads((site / 'api/v1/results.json').read_text(encoding='utf-8'))
    validate_data(data)
    manifest = json.loads((site / 'api/v1/manifest.json').read_text(encoding='utf-8'))
    stations = json.loads((site / 'api/v1/stations.json').read_text(encoding='utf-8'))['stations']
    require(manifest['schema_version'] == '1.0.0' and manifest['coordinate_system'] == 'GCJ-02', 'Manifest contract differs')
    require(manifest['counts'] == {'stations': len(stations), 'samples': len(data['samples']), 'entrances': len(data['entrances']), 'routes': len(data['routes'])}, 'Manifest counts differ')
    require(sum(s['sample_count'] for s in stations) == len(data['samples']), 'Station API counts differ')
    page = (site / 'index.html').read_text(encoding='utf-8')
    require('__DATA__' not in page, 'Unexpanded template')
    embedded = json.dumps(data, ensure_ascii=False).replace('<', '\\u003c').replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')
    require(embedded in page, 'Offline inline dataset differs from public API')
    from build_site import CSV_NAMES
    for name in CSV_NAMES:
        with (site / 'downloads' / (name + '.csv')).open(encoding='utf-8-sig', newline='') as handle:
            rows = list(csv.DictReader(handle))
        require(bool(rows), 'Empty CSV: ' + name)
        expected = {'expanded_samples': len(data['samples']), 'expanded_entrances': len(data['entrances']), 'expanded_accessibility': len(data['scenarios'])}.get(name)
        if name == 'expanded_entrances':
            rows = [row for row in rows if str(row.get('baseline_eligible', '')).lower() == 'true']
            require({r['entrance_poi_id'] for r in rows} == {r['entrance_poi_id'] for r in data['entrances']}, 'Eligible entrance IDs differ')
        if expected is not None:
            require(len(rows) == expected, 'CSV row count differs: ' + name)
    scan_public(site)
    print('PASS: schema, unique IDs, references, counts, inline data, downloads, public-file scan')

if __name__ == '__main__':
    main()
