"""Bounded refresh of four destination categories at three pilot stations."""
import json
from collections import Counter

from audit_local import ROOT, PILOTS, CATEGORIES, normalize, read_csv, write_csv, coordinates, distance
from collect_entrances import SafeClient


def main():
    client=SafeClient(60)
    client.directory=ROOT/'data/raw/amap_pilot_pois'
    client.directory.mkdir(parents=True,exist_ok=True)
    stations=[r for r in read_csv(ROOT/'data/processed/stations_audit.csv') if normalize(r['station']) in PILOTS]
    old=read_csv(ROOT/'data/processed/poi_cache_coverage.csv')
    old_dest=read_csv(ROOT/'data/processed/pilot_destinations.csv')
    pois_out=[]
    comparison=[]
    for station in stations:
        name=normalize(station['station'])
        origin=(float(station['lng']),float(station['lat']))
        for category in ('住宅','写字楼','学校','医院'):
            page=1
            records=[]
            requests=[]
            counts=[]
            first=None
            error=''
            when=''
            while page<=8:
                try:
                    data,identifier,when=client.get('/place/around',{'location':f'{origin[0]},{origin[1]}',
                        'radius':1000,'types':CATEGORIES[category],'offset':25,'page':page,
                        'extensions':'all','sortrule':'distance'})
                except RuntimeError as exc:
                    error=str(exc)
                    break
                requests.append(identifier)
                count=int(data.get('count') or 0)
                if first is None:
                    first=count
                pois=data.get('pois') or []
                if pois:
                    counts.append(count)
                records.extend(pois)
                print(json.dumps({'station':name,'category':category,'page':page,'records':len(pois),'count':count},ensure_ascii=True),flush=True)
                if not pois or len(records)>=first:
                    break
                page+=1
            by_id={}
            for p in records:
                if p.get('id'):
                    by_id.setdefault(p['id'],p)
            previous=next(r for r in old if r['station']==station['station'] and r['category']==category)
            old_ids={r['poi_id'] for r in old_dest if r['station']==station['station'] and r['category']==category}
            new_ids=set(by_id)
            status=('error_or_partial' if error else 'matches_reported_count'
                    if len(by_id)==first and len(set(counts))<=1 else 'incomplete_or_count_unreliable')
            comparison.append({'station':name,'category':category,'legacy_csv_count':previous['legacy_csv_count'],
                'legacy_first_api_count':previous['api_reported_first_count'],
                'legacy_unique_ids':previous['unique_poi_ids'],'legacy_status':previous['status'],
                'fresh_first_api_count':first if first is not None else '',
                'fresh_records':len(records),'fresh_unique_ids':len(by_id),
                'fresh_duplicate_records':len(records)-len(by_id),
                'fresh_nonempty_count_consistent':len(set(counts))<=1,
                'fresh_status':status,'old_ids_also_in_fresh':len(old_ids & new_ids),
                'old_ids_not_in_fresh':len(old_ids-new_ids),'fresh_ids_not_in_old':len(new_ids-old_ids),
                'pages_requested':len(requests),'request_ids':';'.join(requests),
                'collected_at_utc':when,'error':error})
            for p in by_id.values():
                loc=coordinates(p.get('location'))
                d=distance(origin,loc) if loc else None
                def field(k):
                    return p.get(k) if isinstance(p.get(k),str) else ''
                pois_out.append({'station_id':station['station_id'],'station':name,'category':category,
                    'poi_id':p['id'],'name':p.get('name',''),'typecode':field('typecode'),
                    'lng':loc[0] if loc else '', 'lat':loc[1] if loc else '',
                    'coordinate_system':'GCJ-02','straight_distance_m':round(d,2) if d is not None else '',
                    'within_1000m':d is not None and d<=1000,
                    'parent_poi_id':field('parent'),'entr_location':field('entr_location'),
                    'exit_location':field('exit_location'),'destination_point_status':'poi_center_proxy',
                    'query_completeness':status,'collected_at_utc':when})
            if error:
                break
        if error:
            break
    if pois_out:
        write_csv(ROOT/'data/processed/pilot_pois_refreshed.csv',pois_out)
    write_csv(ROOT/'data/processed/pilot_poi_refresh_comparison.csv',comparison)
    summary={'calls':client.calls,'request_budget':60,'credential_source':client.credential_source,
             'comparison':comparison,'poi_records':len(pois_out),
             'status_counts':dict(Counter(r['fresh_status'] for r in comparison))}
    (ROOT/'output/pilot_poi_refresh.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'calls':client.calls,'pairs':len(comparison),'poi_records':len(pois_out),
                      'status_counts':summary['status_counts']},ensure_ascii=True),flush=True)


if __name__=='__main__':
    main()
