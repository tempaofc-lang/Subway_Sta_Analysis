"""Three route samples to check access to the official walking API, not coverage."""
import json

from audit_local import ROOT, PILOTS, normalize, read_csv, distance, write_csv
from collect_entrances import SafeClient


def main():
    client=SafeClient(3)
    client.directory=ROOT/'data/raw/amap_walking_probe'
    client.directory.mkdir(parents=True,exist_ok=True)
    destinations=read_csv(ROOT/'data/processed/pilot_destinations.csv')
    entrances=read_csv(ROOT/'data/processed/entrance_candidates.csv')
    rows=[]
    for station in PILOTS:
        candidates=[r for r in entrances if normalize(r['station'])==station and r['name_matches_station']=='True']
        # Metro Central 1 is an operator-confirmed numbered station entrance;
        # the other two are map candidates and still require opening checks.
        preferred={'八一广场':'2号口','地铁大厦':'1号口','瑶湖西':'3号口'}[station]
        entrance=next(r for r in candidates if r['entrance_name'].endswith(preferred))
        choices=[r for r in destinations if normalize(r['station'])==station and r['category']=='住宅'
                 and 200<=float(r['straight_distance_m'])<=800]
        if not choices:
            choices=[r for r in destinations if normalize(r['station'])==station]
        destination=sorted(choices,key=lambda r:r['poi_id'])[0]
        origin=(float(destination['lng']),float(destination['lat']))
        end=(float(entrance['lng']),float(entrance['lat']))
        row={'station':station,'origin_poi_id':destination['poi_id'],'origin_name':destination['name'],
             'origin_category':destination['category'],'origin_point_status':'poi_center_proxy',
             'entrance_poi_id':entrance['entrance_poi_id'],'entrance_name':entrance['entrance_name'],
             'entrance_open_status':'unknown','straight_distance_m':round(distance(origin,end),2)}
        try:
            data,identifier,when=client.get('/direction/walking',{
                'origin':f'{origin[0]},{origin[1]}','destination':f'{end[0]},{end[1]}'})
            paths=(data.get('route') or {}).get('paths') or []
            route=min(paths,key=lambda p:float(p['distance'])) if paths else None
            row.update({'request_id':identifier,'collected_at_utc':when,'status':'route_returned' if route else 'no_route',
                        'distance_m':route.get('distance','') if route else '',
                        'api_duration_seconds':route.get('duration','') if route else '',
                        'steps':len(route.get('steps') or []) if route else 0,
                        'has_polyline':bool(route and any(s.get('polyline') for s in route.get('steps') or [])),
                        'error':''})
        except RuntimeError as exc:
            row.update({'request_id':'','collected_at_utc':'','status':'error','distance_m':'',
                        'api_duration_seconds':'','steps':0,'has_polyline':False,'error':str(exc)})
        rows.append(row)
        if row['status']=='error':
            break
    write_csv(ROOT/'data/processed/walking_api_probe.csv',rows)
    (ROOT/'output/walking_api_probe.json').write_text(json.dumps({'calls':client.calls,'samples':rows},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'calls':client.calls,'results':[{k:r[k] for k in ['station','status','distance_m','api_duration_seconds','has_polyline','error']} for r in rows]},ensure_ascii=True))


if __name__=='__main__':
    main()
