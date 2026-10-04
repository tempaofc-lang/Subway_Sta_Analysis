"""Deterministic stratified samples and conditional navigation accessibility."""
import argparse
import hashlib
import json
import math
from collections import Counter, defaultdict

from audit_local import ROOT, PILOTS, coordinates, distance, read_csv, write_csv
from collect_entrances import SafeClient


def samples():
    candidates=read_csv(ROOT/'data/processed/destination_entity_review.csv')
    chosen=[]
    for station in PILOTS:
        for category,quota in [('住宅',4),('写字楼',3),('学校',2),('医院',1)]:
            rows=[r for r in candidates if r['station']==station and r['category']==category
                  and r['within_1000m']=='True' and r['candidate_arrival_lng']
                  and '宿舍' not in r['review_action']
                  and (not r['entrance_to_center_m'] or float(r['entrance_to_center_m'])<=250)]
            if category=='住宅':
                rows=[r for r in rows if any(w in r['name'] for w in ['园','苑','城','府','公寓','小区','花园','半岛','住宅'])]
            entities={}
            for r in rows:
                entity=r['proposed_entity_id'] if r['entity_merge_evidence']!='none' else (r['parent_poi_id'] or r['poi_id'])
                entities.setdefault(entity,r)
            rows=list(entities.values())
            rows.sort(key=lambda r:(int(float(r['straight_distance_m'])//333),hashlib.sha256(r['poi_id'].encode()).hexdigest()))
            bins=defaultdict(list)
            for r in rows: bins[min(2,int(float(r['straight_distance_m'])//333))].append(r)
            selected=[]
            while len(selected)<quota and any(bins.values()):
                for band in (0,1,2):
                    if bins[band] and len(selected)<quota: selected.append(bins[band].pop(0))
            for r in selected:
                is_campus=r['candidate_arrival_kind']=='campus_api_entrance_unverified'
                chosen.append({'station':station,'category':category,'sample_id':station+'_'+r['proposed_entity_id'],
                    'name':'江西师范大学(瑶湖校区)' if is_campus else r['name'],
                    'source_poi_id':r['poi_id'],'entity_id':r['proposed_entity_id'],
                    'lng':float(r['candidate_arrival_lng']),'lat':float(r['candidate_arrival_lat']),
                    'point_basis':r['candidate_arrival_kind'],'public_access_status':'unknown',
                    'selection':'category_quota_distance_band_sha256','coordinate_system':'GCJ-02'})
    write_csv(ROOT/'data/processed/access_samples.csv',chosen)
    return chosen


def entrances():
    rows=read_csv(ROOT/'data/processed/entrance_desk_review.csv')
    return [r for r in rows if r['entrance_label'] and
            (r['access_class']=='station_entrance_operator_identified' or
             r['station'].startswith('八一广场') or r['station'].startswith('瑶湖西'))]


def collect():
    selected=samples()
    exits=entrances()
    client=SafeClient(170)
    client.directory=ROOT/'data/raw/access_routes'
    client.directory.mkdir(parents=True,exist_ok=True)
    results=[]
    for s in selected:
        for e in exits:
            if not e['station'].startswith(s['station']): continue
            origin=(s['lng'],s['lat'])
            endpoint=(float(e['lng']),float(e['lat']))
            params={'origin':f'{origin[0]},{origin[1]}','destination':f'{endpoint[0]},{endpoint[1]}'}
            row={'sample_id':s['sample_id'],'station':s['station'],'entrance_id':e['entrance_poi_id'],
                 'entrance_name':e['entrance_name'],'status':'unknown','polyline':[], 'steps':[],
                 'straight_m':distance(origin,endpoint)}
            # Resume only identical successful query snapshots.
            request_hash=hashlib.sha256(json.dumps({'endpoint':'/direction/walking','params':params},sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:20]
            prior=sorted(client.directory.glob(request_hash+'_*.json'))
            cached=json.loads(prior[-1].read_text(encoding='utf-8')) if prior else None
            try:
                if cached and str(cached['response'].get('status'))=='1':
                    data,request_id,when=cached['response'],prior[-1].stem,cached['collected_at_utc']
                else:
                    data,request_id,when=client.get('/direction/walking',params)
                row.update(request_id=request_id,collected_at_utc=when)
                paths=(data.get('route') or {}).get('paths') or []
                if paths:
                    path=min(paths,key=lambda p:float(p['distance']))
                    poly=[]
                    for step in path.get('steps') or []:
                        row['steps'].append({k:step.get(k,'') for k in ['instruction','road','distance','action','assistant_action']})
                        for point in str(step.get('polyline','')).split(';'):
                            p=coordinates(point)
                            if p and (not poly or list(p)!=poly[-1]): poly.append(list(p))
                    d=float(path['distance'])
                    snap_start=distance(origin,poly[0]) if poly else math.inf
                    snap_end=distance(endpoint,poly[-1]) if poly else math.inf
                    geometric=sum(distance(a,b) for a,b in zip(poly,poly[1:]))
                    sane=bool(poly and d>0 and snap_start<=60 and snap_end<=60 and geometric<=max(d*1.25,d+50))
                    row.update(status='model_route' if sane else 'geometry_review',distance_m=d,
                        api_duration_s=float(path.get('duration',0)),polyline=poly,
                        snap_start_m=snap_start,snap_end_m=snap_end,geometry_length_m=geometric,
                        detour_ratio=d/row['straight_m'] if row['straight_m']>=50 else None)
                else: row['status']='no_route'
            except RuntimeError as exc:
                row['error']=str(exc)
                results.append(row)
                (ROOT/'output/access_routes.json').write_text(json.dumps({'samples':selected,'entrances':exits,'routes':results,'new_calls':client.calls},ensure_ascii=False,indent=2),encoding='utf-8')
                print(json.dumps({'error':str(exc),'saved_routes':len(results)}),flush=True)
                return
            results.append(row)
            if len(results)%10==0: print(json.dumps({'completed':len(results),'new_calls':client.calls,'station':s['station']},ensure_ascii=True),flush=True)
        (ROOT/'output/access_routes.json').write_text(json.dumps({'samples':selected,'entrances':exits,'routes':results,'new_calls':client.calls},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'route_statuses':dict(Counter(r['status'] for r in results)),'new_calls':client.calls},ensure_ascii=True),flush=True)


def summarize():
    data=json.loads((ROOT/'output/access_routes.json').read_text(encoding='utf-8'))
    rows=[]
    for s in data['samples']:
        routes=[r for r in data['routes'] if r['sample_id']==s['sample_id']]
        expected=sum(e['station'].startswith(s['station']) for e in data['entrances'])
        valid=[r for r in routes if r['status']=='model_route']
        best=min(valid,key=lambda r:r['distance_m']) if valid else None
        for speed in [4.5,3.5]:
            for threshold in [10,15]:
                covered=bool(best and best['distance_m']/(speed*1000/60)<=threshold)
                complete=len(valid)==expected
                status='model_covered' if covered else 'model_not_covered' if complete else 'unknown'
                rows.append({**s,'speed_kmh':speed,'threshold_min':threshold,'model_status':status,
                    'best_distance_m':best['distance_m'] if best else '',
                    'best_time_min':round(best['distance_m']/(speed*1000/60),3) if best else '',
                    'best_entrance':best['entrance_name'] if best else '',
                    'tested_entrances':len(routes),'valid_entrances':len(valid),'expected_entrances':expected,
                    'minimum_confirmed_within_candidate_set':complete,
                    'best_request_id':best['request_id'] if best else '',
                    'current_public_passability':'unverified'})
    write_csv(ROOT/'data/processed/sample_accessibility.csv',rows)
    data['scenarios']=rows
    checks={'route_total':len(data['routes']),'valid_routes':sum(r['status']=='model_route' for r in data['routes']),
            'sample_total':len(data['samples']),'all_candidate_routes_valid':all(r['status']=='model_route' for r in data['routes']),
            'physical_passability_verified':False}
    data['quality']=checks
    (ROOT/'output/access_results.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(checks),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('mode',choices=['samples','collect','summarize'])
    args=parser.parse_args()
    if args.mode=='samples': print(json.dumps(samples(),ensure_ascii=True))
    elif args.mode=='collect': collect()
    else: summarize()
