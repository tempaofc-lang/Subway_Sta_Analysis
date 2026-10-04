"""Add a bounded, cached station round without replacing the six-station baseline."""
import argparse
import json
import re
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from audit_local import ROOT, coordinates, distance, normalize, read_csv, write_csv
from expand_samples import Collector, new_entrances, residential_office, university_samples, string

STATIONS = ['奥体中心', '谢家村', '双港']
BASELINE = ['八一广场', '地铁大厦', '瑶湖西', '秋水广场', '师大南路', '学府大道东']


class RoundCollector(Collector):
    def __init__(self, budget=80):
        super().__init__(budget)
        self.client.directory = ROOT / 'data/raw/round2_sampling'
        self.client.directory.mkdir(parents=True, exist_ok=True)
        self.request_budget = budget
        self.existing_attempts = len(list(self.client.directory.glob('*.json')))
        self.client.budget = max(0, budget - self.existing_attempts)
        self.rejected_pois = []

    def query(self, endpoint, params, station, purpose):
        pois, rid, when = super().query(endpoint, params, station, purpose)
        if purpose not in ['住宅', '写字楼']:
            return pois, rid, when
        accepted = []
        for poi in pois:
            name = string(poi, 'name')
            primary = string(poi, 'typecode').split('|')[0]
            reason = ''
            if purpose == '住宅' and primary not in ['120300', '120301', '120302']:
                reason = '非住宅主类型或宿舍（校园教工/学生宿舍不纳入普通住宅）'
            elif purpose == '写字楼' and not primary.startswith('1202'):
                reason = '非办公楼主类型'
            elif any(w in name for w in ['菜馆', '餐厅', '餐饮', '酒店', '超市', '烟酒', '维修', '有限公司', '建设中', '后门店']):
                reason = '名称明确为商户、旅馆或在建项目，不能视作对应通勤建筑'
            elif purpose == '写字楼' and not any(w in name for w in ['楼', '大厦', '座', '栋', '广场', '财智', '商务', '银座']):
                reason = '名称缺乏明确办公建筑依据'
            if reason:
                self.rejected_pois.append({'station': station, 'category': purpose, 'poi_id': poi['id'], 'name': name, 'reason': reason, 'request_id': rid})
            else:
                accepted.append(poi)
        return accepted, rid, when


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def csv_stage(path, rows):
    fields = list(dict.fromkeys(k for row in rows for k in row))
    write_csv(path, [{k: row.get(k, '') for k in fields} for row in rows], fields)


def snapshot():
    target = ROOT / 'output/history/round2_before'
    manifest = target / 'snapshot_manifest.json'
    if manifest.exists():
        return target
    # Exclusive creation prevents a partial or prior baseline from being overwritten.
    target.mkdir(parents=True, exist_ok=False)
    files = []
    for directory in ['data/processed', 'output']:
        for path in (ROOT / directory).glob('*'):
            if path.is_file() and (path.name.startswith('expanded_') or path.name.startswith('expansion_')
                                   or path.name.startswith('current_expansion') or path.name.startswith('扩展')):
                relative = path.relative_to(ROOT)
                destination = target / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, destination)
                files.append(str(relative))
    dump(manifest, {'created_at_utc': datetime.now(timezone.utc).isoformat(), 'files': files})
    return target


def station_details(c, station):
    name = normalize(station['station'])
    pois, rid, when = c.query('/place/detail', {'id': station['station_id'], 'extensions': 'all'}, name, 'station_parent_verification')
    matches = [p for p in pois if p.get('id') == station['station_id']]
    if len(matches) != 1:
        raise ValueError('Station parent POI not uniquely confirmed: ' + name)
    poi = matches[0]
    point = coordinates(string(poi, 'location'))
    line_text = string(poi, 'address') + ' ' + string(poi, 'name')
    if name not in string(poi, 'name') or '150500' not in string(poi, 'typecode'):
        raise ValueError('Station parent name/type mismatch: ' + name)
    if not re.search(r'1号线', line_text) or '1号线' not in station['lines']:
        raise ValueError('Line 1 not confirmed by official station details: ' + name)
    if not point or distance(point, (float(station['lng']), float(station['lat']))) > 100:
        raise ValueError('Station source/detail coordinate discrepancy: ' + name)
    return {'station': name, 'station_id': station['station_id'], 'official_name': poi['name'],
            'official_address': string(poi, 'address'), 'source_request_id': rid,
            'source_coordinates': [float(station['lng']), float(station['lat'])],
            'official_coordinates': point, 'line_1_confirmed': True, 'collected_at_utc': when}


def universities(c, station):
    name = normalize(station['station'])
    # Type search includes accredited colleges, avoiding the previous name-only 大学 filter.
    discovered, rid, when = c.around(station, 'university_discovery', types='141201', radius=2000)
    gates, _, _ = c.around(station, 'university_gate_discovery', keywords='大学 门', radius=1500)
    college_gates, _, _ = c.around(station, 'college_gate_discovery', keywords='学院 门', radius=1500)
    by_id = {p['id']: p for p in discovered if p.get('id')}
    # Resolve potential gate parents; never promote an internal school/department into a campus.
    parent_ids = sorted({string(p, 'parent') for p in discovered + gates + college_gates if string(p, 'parent')})
    for parent in parent_ids[:6]:
        if parent not in by_id:
            results, _, _ = c.query('/place/detail', {'id': parent, 'extensions': 'all'}, name, 'university_gate_parent_verification')
            for p in results:
                if '141201' in string(p, 'typecode') and not string(p, 'parent'):
                    by_id[p['id']] = p
    candidates = [p for p in by_id.values() if string(p, 'typecode').split('|')[0] == '141201'
                  and not string(p, 'parent') and any(w in p.get('name', '') for w in ['大学', '学院'])
                  and not any(w in p.get('name', '') for w in
                      ['医院', '成教', '附属', '继续教育', '培训', '函授', '招生', '选拔', '办公室', '教材', '学务', '-'])]
    for p in by_id.values():
        if p not in candidates:
            c.rejected_pois.append({'station': name, 'category': '大学校门', 'poi_id': p['id'], 'name': p.get('name', ''),
                                    'reason': '非独立高校父POI、主类型非高校或名称为内部/培训机构'})
    candidates.sort(key=lambda p: distance((float(station['lng']), float(station['lat'])),
                                          coordinates(string(p, 'location')) or (0, 0)))
    selected = university_samples(c, station, candidates[:6], radius_override=1500, include_colleges=True)
    for row in c.campus_review:
        if row['station'] == name:
            row['in_primary_1000m_frame'] = any(s['entity_id'] == row['campus_poi_id'] and s['straight_to_station_m'] <= 1000 for s in selected)
            row['selected'] = any(s['entity_id'] == row['campus_poi_id'] for s in selected)
            row['selected_extended_radius'] = any(s['entity_id'] == row['campus_poi_id'] and s['straight_to_station_m'] > 1000 for s in selected)
    return selected, {'discovered_count': len(discovered), 'candidate_parents': len(candidates),
                      'gate_parent_ids_considered': parent_ids[:6], 'selected': len(selected)}


def run(budget, refresh=False):
    baseline = ROOT / 'output/history/round2_before' if refresh else ROOT
    old_samples = read_csv(baseline / 'data/processed/expanded_samples.csv')
    old_entrances = read_csv(baseline / 'data/processed/expanded_entrances.csv')
    old_review = read_csv(baseline / 'data/processed/expanded_campus_gate_review.csv')
    if any(r['station'] in STATIONS for r in old_samples):
        names = {r['station'] for r in old_samples}
        if set(STATIONS) <= names:
            print(json.dumps({'status': 'already_published_locally', 'samples': len(old_samples)}, ensure_ascii=True))
            return
        raise ValueError('Partially merged round detected; inspect the baseline snapshot before resuming')
    if len(old_samples) != 45 or set(r['station'] for r in old_samples) != set(BASELINE):
        raise ValueError('Expected the existing six-station, 45-sample baseline')
    history = snapshot()
    c = RoundCollector(budget)
    stations = {normalize(r['station']): r for r in read_csv(ROOT / 'data/processed/stations_audit.csv')}
    details, additions, entrances, discovery = [], [], [], {}
    for name in STATIONS:
        station = stations[name]
        details.append(station_details(c, station))
        new_entries = new_entrances(c, station)
        if not any(e['baseline_eligible'] for e in new_entries):
            raise ValueError('No parent-confirmed numbered station entrance: ' + name)
        entrances.extend(new_entries)
        additions.extend(residential_office(c, station))
        students, discovery[name] = universities(c, station)
        additions.extend(students)
    # Mixed-category map POIs may be returned in both residential and office searches.
    # Preserve the first category (residential), never count one entity twice.
    deduplicated = {}
    duplicate_samples = []
    for row in additions:
        if row['sample_id'] in deduplicated:
            duplicate_samples.append({'sample_id': row['sample_id'], 'name': row['name'], 'excluded_category': row['category']})
        else:
            deduplicated[row['sample_id']] = row
    additions = list(deduplicated.values())
    for row in additions:
        row.setdefault('selection_radius_m', 1000)
        row.setdefault('selection_scope', 'primary_1000m')
        evidence = [p for p in c.pois if p['request_id'] == row['source_request_id'] and p['poi'].get('id') == row['source_poi_id']]
        if not evidence:
            raise ValueError('Sample evidence request does not contain selected POI: ' + row['sample_id'])
        row['source_typecode'] = string(evidence[0]['poi'], 'typecode')
        row['source_type'] = string(evidence[0]['poi'], 'type')
    samples = old_samples + additions
    all_entrances = old_entrances + entrances
    if len({s['sample_id'] for s in samples}) != len(samples):
        raise ValueError('Duplicate sample identity')
    if any(not any(s['station'] == name for s in additions) for name in STATIONS):
        raise ValueError('New station has no usable sample')
    total = list(c.client.directory.glob('*.json'))
    summary = {'round': 2, 'rejected_pois': c.rejected_pois, 'excluded_duplicate_samples': duplicate_samples, 'stations': BASELINE + STATIONS, 'new_stations': STATIONS,
               'sample_count': len(samples), 'added_sample_count': len(additions),
               'samples_by_station': dict(Counter(r['station'] for r in samples)),
               'samples_by_group': dict(Counter(r['commuter_group'] for r in samples)),
               'new_api_calls': c.client.calls, 'total_api_attempts': len(total),
               'total_api_calls': len(total), 'request_budget': budget,
               'cache_reused_queries': sum(q['source'] == 'cached_official_response' for q in c.queries),
               'entrance_count': len(all_entrances), 'station_verification': details,
               'added_eligible_entrances': dict(Counter(e['station'] for e in entrances if e['baseline_eligible'])),
               'university_discovery': discovery, 'campus_review': old_review + c.campus_review,
               'queries': c.queries, 'coordinate_system': 'GCJ-02',
               'baseline_snapshot': str(history.relative_to(ROOT)),
               'assumptions': ['住宅最多4、办公最多2；主样本入口距站中心1000米内',
                               '新增站大学校门最多2，学生扩展半径1500米并单独标注',
                               '合格校门缺失不强行补样；父POI入口字段仅为未经现场核实的代理',
                               '门禁及临时封路不纳入本轮；样本不代表人口；保留原六站45样本'],
               'updated_at_utc': datetime.now(timezone.utc).isoformat()}
    # Complete every collection and validation before staging any replacement of active inputs.
    stage = ROOT / 'output/round2_staging'
    stage.mkdir(parents=True, exist_ok=True)
    staged = [('expanded_samples.csv', samples), ('expanded_entrances.csv', all_entrances),
              ('expanded_campus_gate_review.csv', old_review + c.campus_review)]
    for filename, rows in staged:
        csv_stage(stage / filename, rows)
    dump(stage / 'round2_sampling.json', summary)
    dump(ROOT / 'data/processed/round2_poi_evidence.json', c.pois)
    for filename, _ in staged:
        (stage / filename).replace(ROOT / 'data/processed' / filename)
    dump(ROOT / 'output/round2_sampling.json', summary)
    dump(ROOT / 'output/expansion_sampling.json', summary)
    print(json.dumps({k: summary[k] for k in ['sample_count', 'added_sample_count', 'samples_by_station',
                     'new_api_calls', 'total_api_attempts', 'added_eligible_entrances', 'university_discovery']}, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--budget', type=int, default=80)
    parser.add_argument('--refresh', action='store_true', help='Rebuild this round from the preserved baseline using cached evidence')
    args = parser.parse_args()
    if not 1 <= args.budget <= 80:
        parser.error('budget must be between 1 and 80')
    run(args.budget, args.refresh)
