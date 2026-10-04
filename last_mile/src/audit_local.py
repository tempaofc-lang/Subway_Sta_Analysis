"""Read-only audit of the internship inputs; write independent derived outputs."""
import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT.parent / 'python practice project' / '建模做样'
CATEGORIES = {'餐饮': '050000', '购物': '060000', '写字楼': '120200',
              '住宅': '120300', '学校': '141200', '医院': '090100|090200',
              '酒店': '100000', '景点': '110000'}
PILOTS = ['八一广场', '地铁大厦', '瑶湖西']


def read_csv(path):
    with path.open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def write_csv(path, rows, fields=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields or list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def coordinates(value):
    try:
        lon, lat = map(float, value.split(','))
        if math.isfinite(lon) and math.isfinite(lat) and -180 <= lon <= 180 and -90 <= lat <= 90:
            return lon, lat
    except (ValueError, TypeError, AttributeError):
        pass
    return None


def distance(a, b):
    lon1, lat1, lon2, lat2 = map(math.radians, (*a, *b))
    v = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 6371008.8 * 2 * math.asin(math.sqrt(min(1, v)))


def normalize(name):
    return name.replace('(地铁站)', '').replace('（地铁站）', '')


def audit():
    out = ROOT / 'data' / 'processed'
    output = ROOT / 'output'
    out.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    stations = read_csv(SOURCE / 'data_clean/stations.csv')
    counts = read_csv(SOURCE / 'data_clean/station_poi_counts.csv')
    types = read_csv(SOURCE / 'data_clean/station_types.csv')
    count_by_name = {r['station']: r for r in counts}
    type_by_name = {r['station']: r for r in types}
    cache = json.loads((SOURCE / 'data_raw/poi_counts_cache.json').read_text(encoding='utf-8'))
    groups = defaultdict(list)
    all_pois = []
    cache_station_pois = []
    unsafe_key_count = 0
    for key, response in cache.items():
        endpoint, query = key.split('|', 1)
        params = dict(p.split('=', 1) for p in query.split('&') if '=' in p)
        unsafe_key_count += int(any(k.lower() in ('key', 'token', 'secret') for k in params))
        pois = response.get('pois') or []
        all_pois.extend(pois)
        if endpoint == '/place/text':
            cache_station_pois.extend(pois)
        if endpoint == '/place/around':
            loc = coordinates(params.get('location'))
            if loc:
                groups[(loc, params.get('types'), params.get('radius'))].append((params, response))
    variants = defaultdict(set)
    for poi in all_pois:
        if poi.get('id'):
            variants[poi['id']].add((str(poi.get('name')), str(poi.get('location')), str(poi.get('typecode'))))
    station_audit, coverage, pilot_pois = [], [], []
    for station in stations:
        name = station['station']
        origin = coordinates(f"{station['lng']},{station['lat']}")
        matches = [p for p in cache_station_pois if p.get('name') == name and coordinates(p.get('location')) == origin]
        ids = sorted({p['id'] for p in matches if p.get('id')})
        old = type_by_name.get(name, {})
        values = [int(count_by_name.get(name, {}).get(c, 0)) for c in CATEGORIES]
        total = sum(values)
        entropy = -sum((v/total)*math.log2(v/total) for v in values if v) if total else 0
        numeric_match = all(old.get(c) == count_by_name.get(name, {}).get(c) for c in CATEGORIES)
        station_audit.append({'station_id': ids[0] if len(ids) == 1 else '', 'station': name,
            'lng': station['lng'], 'lat': station['lat'], 'coordinate_system': 'GCJ-02 (source inferred)',
            'lines': station['lines'], 'legacy_type': old.get('type', ''),
            'station_id_candidates': len(ids), 'coordinate_valid': bool(origin),
            'within_nanchang_sanity_box': bool(origin and 115 <= origin[0] <= 117 and 28 <= origin[1] <= 30),
            'table_join_complete': name in count_by_name and name in type_by_name,
            'poi_count_columns_match': numeric_match,
            'total_matches': total == int(old.get('total', -1)),
            'entropy_matches_rounding': abs(entropy-float(old.get('entropy', -100))) <= .0011,
            'operating_status': 'not_verified', 'collection_date': 'unknown'})
        for category, typecode in CATEGORIES.items():
            entries = sorted(groups.get((origin, typecode, '1000'), []), key=lambda e: int(e[0].get('page', 1)))
            seen = {}
            raw_count = 0
            invalid, anonymous = 0, 0
            for params, response in entries:
                for poi in response.get('pois') or []:
                    raw_count += 1
                    invalid += int(not coordinates(poi.get('location')))
                    if not poi.get('id'):
                        anonymous += 1
                    else:
                        seen.setdefault(poi['id'], poi)
            # A trailing empty page commonly reports count=0; it is not evidence
            # that the nonempty pages disagree about the query's total.
            reported_counts = sorted({int(r.get('count') or 0) for _, r in entries if r.get('pois')})
            first_count = int(entries[0][1].get('count') or 0) if entries else None
            pages = [int(p.get('page', 1)) for p, _ in entries]
            max_offset = max((int(p.get('offset', 25)) for p, _ in entries), default=25)
            expected_pages = math.ceil((first_count or 0)/max_offset)
            pages_complete = bool(entries) and (not first_count or set(range(1, expected_pages+1)).issubset(pages))
            matches_count = first_count is not None and len(seen) == first_count
            status = ('missing_cache' if not entries else 'reported_count_inconsistent' if len(reported_counts)>1
                      else 'matches_reported_count' if matches_count and pages_complete and not anonymous
                      else 'incomplete_or_count_unreliable')
            within_count = 0
            for poi_id, poi in seen.items():
                loc = coordinates(poi.get('location'))
                d = distance(origin, loc) if origin and loc else None
                within_count += int(d is not None and d <= 1000)
                if normalize(name) in PILOTS:
                    pilot_pois.append({'station_id': ids[0] if len(ids)==1 else '', 'station': name,
                        'category': category, 'poi_id': poi_id, 'name': poi.get('name', ''),
                        'typecode': poi.get('typecode', ''), 'lng': loc[0] if loc else '', 'lat': loc[1] if loc else '',
                        'coordinate_system': 'GCJ-02 (source inferred)',
                        'straight_distance_m': round(d, 2) if d is not None else '',
                        'within_1000m': d is not None and d<=1000,
                        'destination_point_status': 'poi_center_proxy',
                        'candidate_source': 'legacy_station_category_cache', 'collection_date': 'unknown'})
            coverage.append({'station': name, 'category': category, 'typecode': typecode,
                'legacy_csv_count': count_by_name.get(name, {}).get(category, ''),
                'api_reported_first_count': first_count if first_count is not None else '',
                'api_counts_consistent': len(reported_counts)<=1, 'cached_pages': len(entries),
                'empty_cached_pages': sum(not r.get('pois') for _,r in entries),
                'cached_poi_records': raw_count, 'unique_poi_ids': len(seen),
                'duplicate_records': raw_count-len(seen)-anonymous, 'missing_poi_ids': anonymous,
                'invalid_coordinate_records': invalid, 'within_1000m_unique_pois': within_count,
                'outside_or_invalid_unique_pois': len(seen)-within_count,
                'matches_reported_count': matches_count, 'status': status,
                'csv_matches_first_reported_count': first_count is not None and int(count_by_name[name][category]) == first_count})
    write_csv(out/'stations_audit.csv', station_audit)
    write_csv(out/'poi_cache_coverage.csv', coverage)
    write_csv(out/'pilot_poi_candidates.csv', pilot_pois)
    relevant = [p for p in pilot_pois if p['category'] in ('住宅','写字楼','学校','医院') and p['within_1000m']]
    write_csv(out/'pilot_destinations.csv', relevant, list(pilot_pois[0]))
    pilots = []
    for n in PILOTS:
        station = next(r for r in station_audit if normalize(r['station'])==n)
        pilots.append({**station, 'destination_counts': dict(Counter(p['category'] for p in relevant if normalize(p['station'])==n)),
                       'categories': [r for r in coverage if normalize(r['station'])==n]})
    summary = {'audited_at_utc': datetime.now(timezone.utc).isoformat(),
        'station_rows': len(stations), 'unique_station_names': len({r['station'] for r in stations}),
        'table_checks_failed': {c: sum(not r[c] for r in station_audit) for c in ['coordinate_valid', 'within_nanchang_sanity_box','table_join_complete','poi_count_columns_match','total_matches','entropy_matches_rounding']},
        'station_ids_resolved': sum(bool(r['station_id']) for r in station_audit),
        'cache_requests': len(cache), 'poi_records': len(all_pois), 'unique_poi_ids': len(variants),
        'ids_with_conflicting_name_location_or_type': sum(len(v)>1 for v in variants.values()),
        'cache_keys_containing_credential_parameter': unsafe_key_count,
        'station_category_pairs': len(coverage), 'coverage_status_counts': dict(Counter(r['status'] for r in coverage)),
        'first_count_distribution': dict(Counter(str(r['api_reported_first_count']) for r in coverage)),
        'csv_vs_first_count_mismatches': sum(not r['csv_matches_first_reported_count'] for r in coverage),
        'all_cached_entrance_pois': sum(str(p.get('typecode'))=='150501' for p in all_pois),
        'station_text_nonempty_entr_location': sum(bool(p.get('entr_location')) for p in cache_station_pois),
        'station_text_nonempty_children': sum(bool(p.get('children')) for p in cache_station_pois),
        'pilots': pilots,
        'input_manifest': [{'path': str(p.relative_to(SOURCE)), 'bytes': p.stat().st_size,
                            'sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'collection_date': 'unknown'}
                           for p in [SOURCE/'data_clean/stations.csv',SOURCE/'data_clean/station_poi_counts.csv',SOURCE/'data_clean/station_types.csv',SOURCE/'data_raw/poi_counts_cache.json']]}
    (output/'local_audit.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k:v for k,v in summary.items() if k not in ('pilots','input_manifest','first_count_distribution')},ensure_ascii=True))
    print('pilot_destination_counts='+json.dumps({normalize(p['station']):p['destination_counts'] for p in pilots},ensure_ascii=True))
    return summary


if __name__ == '__main__':
    audit()
