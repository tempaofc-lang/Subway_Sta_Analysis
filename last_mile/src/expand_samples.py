"""Bounded, resumable six-station expansion; university gates are a separate group."""
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone

from audit_local import ROOT, CATEGORIES, coordinates, distance, normalize, read_csv, write_csv
from collect_entrances import SafeClient

NEW_STATIONS = ['秋水广场', '师大南路', '学府大道东']
ALL_STATIONS = ['八一广场', '地铁大厦', '瑶湖西', *NEW_STATIONS]


class Collector:
    def __init__(self, budget=80):
        self.client = SafeClient(budget)
        self.client.directory = ROOT / 'data/raw/expanded_sampling'
        self.client.directory.mkdir(parents=True, exist_ok=True)
        self.request_budget = budget
        self.existing_attempts = len(list(self.client.directory.glob('*.json')))
        self.client.budget = max(0,budget-self.existing_attempts)
        self.queries = []
        self.pois = []
        self.campus_review = []

    def query(self, endpoint, params, station, purpose):
        h = hashlib.sha256(json.dumps({'endpoint': endpoint, 'params': params}, sort_keys=True,
                                    ensure_ascii=False).encode()).hexdigest()[:20]
        prior = sorted(self.client.directory.glob(h + '_*.json'))
        cached = json.loads(prior[-1].read_text(encoding='utf-8')) if prior else None
        if cached and str(cached['response'].get('status')) == '1':
            data, identifier, when = cached['response'], prior[-1].stem, cached['collected_at_utc']
            source = 'cached_official_response'
        else:
            data, identifier, when = self.client.get(endpoint, params)
            source = 'official_api'
        pois = data.get('pois') or []
        self.queries.append({'station': station, 'purpose': purpose, 'endpoint': endpoint,
                             'params': params, 'request_id': identifier,
                             'reported_count': data.get('count'), 'returned': len(pois), 'source': source})
        self.pois.extend({'station': station, 'purpose': purpose, 'request_id': identifier,
                          'collected_at_utc': when, 'poi': p} for p in pois)
        print(json.dumps({'station': station, 'purpose': purpose, 'returned': len(pois),
                          'new_calls': self.client.calls}, ensure_ascii=True), flush=True)
        return pois, identifier, when

    def around(self, station, purpose, **extra):
        params = {'location': f"{station['lng']},{station['lat']}", 'radius': 1000,
                  'offset': 25, 'page': 1, 'extensions': 'all', 'children': 1, 'sortrule': 'distance'}
        params.update(extra)
        return self.query('/place/around', params, normalize(station['station']), purpose)


def string(poi, key):
    return poi.get(key) if isinstance(poi.get(key), str) else ''


def new_entrances(c, station):
    name = normalize(station['station'])
    origin = (float(station['lng']), float(station['lat']))
    found = {}
    for method in ['around', 'text']:
        if method == 'around':
            pois, rid, when = c.around(station, 'station_entrances', types='150501')
        else:
            pois, rid, when = c.query('/place/text', {'keywords': name + '地铁站', 'city': '360100',
                'citylimit': 'true', 'types': '150501', 'offset': 25, 'page': 1,
                'extensions': 'all', 'children': 1}, name, 'station_entrances')
        for p in pois:
            point = coordinates(p.get('location'))
            if not point or not p.get('id') or (name not in p.get('name','') and p.get('parent') != station['station_id']):
                continue
            if p['id'] in found:
                found[p['id']]['request_ids'] += ';' + rid
                found[p['id']]['both_search_methods_returned'] = True
                continue
            match = re.search(r'(\d+|[A-Z])(?:号)?(?:出入口|口)', p.get('name',''))
            label = match.group(1) if match else ''
            parent_match = string(p, 'parent') == station['station_id']
            poi_name = string(p, 'name')
            exit_only = '出口' in poi_name and '入口' not in poi_name
            named_match = re.search(r'((?:(?:东北|东南|西北|西南|[东西南北])(?:侧)?)?(?:出入口|入口)|(?:东北|东南|西北|西南|[东西南北])(?:侧)?口)(?=[)）\s]*$)', poi_name)
            named_eligible = bool(not label and named_match and parent_match and name in poi_name
                                  and string(p, 'typecode').split('|')[0] == '150501' and not exit_only)
            numbered_eligible = bool(label and parent_match and not exit_only)
            if named_eligible:
                label = named_match.group(1)
            found[p['id']] = {'station_id': station['station_id'], 'station': name,
                'entrance_poi_id': p['id'], 'entrance_name': p.get('name',''),
                'typecode': string(p,'typecode'), 'lng': point[0], 'lat': point[1],
                'coordinate_system': 'GCJ-02', 'parent_poi_id': string(p,'parent'),
                'distance_to_station_m': round(distance(origin,point),2), 'name_matches_station': name in p.get('name',''),
                'verification_status': 'map_parent_named_entry_candidate' if named_eligible else 'map_parent_numbered_candidate', 'open_status': 'excluded_from_scope',
                'field_verified': False, 'request_ids': rid, 'source_url': 'https://ditu.amap.com/place/'+p['id'],
                'collected_at_utc': when, 'entrance_label': label, 'parent_matches_station': parent_match,
                'both_search_methods_returned': False, 'desk_evidence_level': 'official_map_parent_explicit_entry_name' if named_eligible else 'official_map_parent_number',
                'access_class': 'station_entrance_map_parent_named_entry' if named_eligible else 'station_entrance_map_parent_numbered' if numbered_eligible else 'unclassified_access_requires_check',
                'evidence_url': 'https://ditu.amap.com/place/'+p['id'], 'evidence_date': when[:10],
                'eligible_for_verified_open_analysis': False, 'next_check': '门禁及临时封路不纳入本轮；导航几何一致性另检',
                'baseline_eligible': numbered_eligible or named_eligible, 'source_method': 'official_place_around_text_parent_matching'}
    return list(found.values())


def residential_office(c, station):
    name = normalize(station['station'])
    origin = (float(station['lng']), float(station['lat']))
    result = []
    for category, quota in [('住宅',4),('写字楼',2)]:
        all_pois = {}
        # Search is deliberately capped. It is a sampling frame, not a population census.
        for page in (1,2,3):
            pois,rid,when = c.around(station, category, types=CATEGORIES[category], page=page)
            for p in pois:
                if p.get('id'):
                    all_pois.setdefault(p['id'],(p,rid,when))
            good = [p for p,_,_ in all_pois.values() if coordinates(string(p,'entr_location'))]
            raw_returned = c.queries[-1]['returned'] if getattr(c, 'queries', None) else len(pois)
            if len(good) >= quota*3 or raw_returned < 25:
                break
        candidates = []
        entities = set()
        for p,rid,when in all_pois.values():
            point = coordinates(string(p,'entr_location'))
            entity = string(p,'parent') or p['id']
            if not point or entity in entities or distance(origin,point)>1000:
                continue
            if category == '住宅' and (any(t in p['name'] for t in ['宿舍','学生公寓']) or
                not any(t in p['name'] for t in ['园','苑','城','府','公寓','小区','花园','半岛','住宅'])):
                continue
            entities.add(entity)
            candidates.append({'station':name, 'category':category, 'sample_id':name+'_'+entity,
                'name':p['name'], 'source_poi_id':p['id'], 'entity_id':entity, 'lng':point[0], 'lat':point[1],
                'point_basis':'api_entrance_coordinate_unverified', 'public_access_status':'excluded_from_scope',
                'selection':'category_quota_distance_band_sha256', 'coordinate_system':'GCJ-02',
                'commuter_group':'resident' if category=='住宅' else 'other',
                'source_method':'official_poi_entr_location', 'source_request_id':rid,
                'parent_poi_id':string(p,'parent'), 'straight_to_station_m':round(distance(origin,point),2),
                'point_name':p['name']+'入口坐标', 'source_url':'https://ditu.amap.com/place/'+p['id']})
        bins=defaultdict(list)
        for p in sorted(candidates,key=lambda p:hashlib.sha256(p['source_poi_id'].encode()).hexdigest()):
            bins[min(2,int(p['straight_to_station_m']//333))].append(p)
        chosen=[]
        while len(chosen)<quota and any(bins.values()):
            for band in (0,1,2):
                if bins[band] and len(chosen)<quota:
                    chosen.append(bins[band].pop(0))
        result.extend(chosen)
    return result


def university_candidates(c, station):
    name = normalize(station['station'])
    pois,rid,when = c.around(station, 'university_discovery', keywords='大学', radius=2000)
    # Independent gates are discovered separately. A university centroid need not fall inside 1 km.
    gates,gid,gwhen = c.around(station, 'university_gate_discovery', keywords='大学 门', radius=1500)
    return pois + gates


def university_samples(c, station, discovered, *, radius_override=None, include_colleges=False):
    name = normalize(station['station'])
    origin = (float(station['lng']),float(station['lat']))
    parents = {p['id']:p for p in discovered if '141201' in string(p,'typecode')
               and not string(p,'parent') and not any(w in p.get('name','') for w in
                   (['医院','成教'] if include_colleges else ['学院','医院','成教']))}
    selected=[]
    for p in parents.values():
        radius = radius_override if radius_override is not None else (1500 if name=='学府大道东' else 1000)
        # Examine the named university's actual gate objects, never bus stops or department doors.
        keyword = p['name'].replace('(',' ').replace(')',' ') + ' 门'
        gates,rid,when = c.query('/place/text', {'keywords':keyword,'city':'360100',
            'citylimit':'true','offset':25,'page':1,'extensions':'all','children':1}, name,'named_university_gates')
        if name=='学府大道东':
            # Compound gate names avoid treating a similarly named bus stop as the university gate.
            for gate_keyword in ['南昌航空大学北门','南昌航空大学(前湖校区北门)']:
                extra,erid,ewhen=c.query('/place/text', {'keywords':gate_keyword,'city':'360100',
                    'citylimit':'true','offset':25,'page':1,'extensions':'all','children':1}, name,'exact_university_gate_name')
                gates.extend(extra)
        candidates=[]
        for gate in gates:
            point=coordinates(string(gate,'location'))
            exact_parent=string(gate,'parent')==p['id']
            gate_type='出入口' in string(gate,'type') or '大门' in string(gate,'type')
            gate_name=bool(re.search(r'(东|南|西|北|[一二三四五六七八九]|[0-9]+)门',gate.get('name','')))
            if (point and exact_parent and gate_type and gate_name and
                not any(w in gate['name'] for w in (['公交','停车','宿舍'] if include_colleges else ['公交','停车','学院','宿舍'])) and distance(origin,point)<=radius):
                candidates.append((gate,point,'university_gate_poi_parent_matched',rid))
        # The campus parent entrance field is usable as a declared map proxy when there is no independent gate.
        point=coordinates(string(p,'entr_location'))
        center=coordinates(string(p,'location'))
        c.campus_review.append({'station':name,'campus_poi_id':p['id'],'campus_name':p['name'],
            'campus_center_distance_m':round(distance(origin,center),2) if center else '',
            'campus_entrance_field':string(p,'entr_location'),
            'campus_entrance_distance_m':round(distance(origin,point),2) if point else '',
            'independent_parent_matched_gate_candidates':len(candidates),
            'gate_search_returned':len(gates),
            'bus_stop_not_gate_count':sum('公交' in x.get('name','') for x in gates),
            'in_primary_1000m_frame':bool(candidates or (point and distance(origin,point)<=1000)),
            'review_basis':'大学父POI入口字段是校区入口代理；不使用公交站或内部学院点；尚未现场确认校门'})
        if not candidates and point and distance(origin,point)<=radius:
            original=next(x['request_id'] for x in c.pois if x['poi'].get('id')==p['id'])
            candidates=[(p,point,'campus_api_entrance_unverified',original)]
        if not candidates:
            continue
        gate,point,basis,source_rid=min(candidates,key=lambda x:distance(origin,x[1]))
        selected.append({'station':name,'category':'大学校门','sample_id':name+'_'+p['id'],
            'name':p['name'],'source_poi_id':gate['id'],'entity_id':p['id'],
            'lng':point[0],'lat':point[1],'point_basis':basis,'public_access_status':'excluded_from_scope',
            'selection':'nearest_parent_confirmed_campus_gate_within_selected_radius', 'coordinate_system':'GCJ-02',
            'selection_radius_m':radius,
            'selection_scope':'student_gate_extended_radius' if distance(origin,point)>1000 else 'primary_1000m',
            'commuter_group':'student','source_method':basis,'source_request_id':source_rid,
            'parent_poi_id':p['id'],'straight_to_station_m':round(distance(origin,point),2),
            'point_name':gate['name'] if gate['id']!=p['id'] else p['name']+'高德入口字段',
            'source_url':'https://ditu.amap.com/place/'+gate['id']})
    return sorted(selected,key=lambda s:s['straight_to_station_m'])[:2]


def output(c, samples, entrances, universities):
    for s in samples:
        s.setdefault('selection_radius_m',1000)
        s.setdefault('selection_scope','primary_1000m')
    fields = list(dict.fromkeys(k for r in samples for k in r))
    samples = [{k:r.get(k,'') for k in fields} for r in samples]
    efields = list(dict.fromkeys(k for r in entrances for k in r))
    entrances = [{k:r.get(k,'') for k in efields} for r in entrances]
    write_csv(ROOT/'data/processed/expanded_samples.csv',samples)
    write_csv(ROOT/'data/processed/expanded_entrances.csv',entrances)
    if c.campus_review:
        for r in c.campus_review:
            r['selected']=any(s['station']==r['station'] and s['entity_id']==r['campus_poi_id'] for s in samples)
            r['selected_extended_radius']=any(s['station']==r['station'] and s['entity_id']==r['campus_poi_id']
                and s.get('selection_scope')=='student_gate_extended_radius' for s in samples)
        write_csv(ROOT/'data/processed/expanded_campus_gate_review.csv',c.campus_review)
    (ROOT/'data/processed/expanded_poi_evidence.json').write_text(json.dumps(c.pois,ensure_ascii=False,indent=2),encoding='utf-8')
    total_records=[json.loads(f.read_text(encoding='utf-8')) for f in c.client.directory.glob('*.json')]
    summary={'new_api_calls':c.client.calls,'total_api_attempts':len(total_records),
        'total_api_calls':len(total_records),
        'total_successful_api_calls':sum(str(x.get('response',{}).get('status'))=='1' for x in total_records),
        'request_budget':c.request_budget,'cache_reused_queries':sum(q['source']=='cached_official_response' for q in c.queries),
        'stations':ALL_STATIONS,'sample_count':len(samples),
        'samples_by_station':dict(Counter(r['station'] for r in samples)),
        'samples_by_group':dict(Counter(r['commuter_group'] for r in samples)),
        'entrance_count':len(entrances),'university_discovery':universities,
        'campus_review':c.campus_review,'queries':c.queries,'assumptions':['门禁、临时封路与临时入口开放状态不纳入本轮',
           '大学校门单列学生组；校园宿舍不纳入普通住宅；POI样本不解释为人口',
           '抽样框不是全量POI清查；主样本按入口距站中心1000米筛选，学府大道东学生扩展情景采用1500米',
           '学生起点使用高校父POI高德entr_location字段代理校门，独立校门未确认，非学院入口'],
        'coordinate_system':'GCJ-02','updated_at_utc':datetime.now(timezone.utc).isoformat()}
    (ROOT/'output/expansion_sampling.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:summary[k] for k in ['new_api_calls','sample_count','samples_by_station','samples_by_group','entrance_count']},ensure_ascii=True),flush=True)


def main():
    c=Collector()
    stations={normalize(r['station']):r for r in read_csv(ROOT/'data/processed/stations_audit.csv')}
    samples=read_csv(ROOT/'data/processed/access_samples.csv')
    for s in samples:
        s['commuter_group']='student' if s['point_basis']=='campus_api_entrance_unverified' else 'resident' if s['category']=='住宅' else 'other'
        s['source_method']='legacy_pilot_api_entrance'
        s['source_request_id']=''
        s['parent_poi_id']=s['entity_id'] if s['source_poi_id']!=s['entity_id'] else ''
        s['point_name']=s['name']+'入口坐标'
        s['source_url']='https://ditu.amap.com/place/'+s['entity_id']
        s['selection_scope']='primary_1000m'
        s['selection_radius_m']=1000
        if s['commuter_group']=='student':
            campus=next(r for r in json.loads((ROOT/'output/poi_details_probe.json').read_text(encoding='utf-8'))['results']
                        if r['poi_id']==s['entity_id'])
            s['source_method']='legacy_parent_detail_entr_location'
            s['source_request_id']=campus['request_id']
            s['point_name']=s['name']+'高德入口字段'
        st=stations[s['station']]
        s['straight_to_station_m']=round(distance((float(st['lng']),float(st['lat'])),(float(s['lng']),float(s['lat']))),2)
    entrances=read_csv(ROOT/'data/processed/entrance_desk_review.csv')
    for e in entrances:
        e['station']=normalize(e['station'])
        e['baseline_eligible']=bool(e['entrance_label'] and (e['access_class']=='station_entrance_operator_identified' or e['station'] in ['八一广场','瑶湖西']))
        e['source_method']='legacy_pilot_desk_review'
    universities={}
    for name in NEW_STATIONS:
        station=stations[name]
        entrances.extend(new_entrances(c,station))
        samples.extend(residential_office(c,station))
        discovered=university_candidates(c,station)
        samples.extend(university_samples(c,station,discovered))
        universities[name]=[{'id':p.get('id'),'name':p.get('name'),'typecode':p.get('typecode'),
                            'parent':p.get('parent'),'location':p.get('location'),
                            'entr_location':p.get('entr_location')} for p in discovered]
        output(c,samples,entrances,universities)


if __name__=='__main__':
    main()
