"""Check station parent records and the campus parent of Yaohu West colleges."""
import json
from collections import Counter

from audit_local import ROOT, PILOTS, normalize, read_csv
from collect_entrances import SafeClient


def main():
    client=SafeClient(4)
    client.directory=ROOT/'data/raw/amap_poi_details'
    client.directory.mkdir(parents=True,exist_ok=True)
    stations=[r for r in read_csv(ROOT/'data/processed/stations_audit.csv') if normalize(r['station']) in PILOTS]
    pois=read_csv(ROOT/'data/processed/pilot_pois_refreshed.csv')
    parents=Counter(r['parent_poi_id'] for r in pois if r['station']=='瑶湖西' and r['category']=='学校' and r['parent_poi_id'])
    targets=[(normalize(r['station']),r['station_id']) for r in stations]
    if parents:
        targets.append(('瑶湖西学校共同父POI',parents.most_common(1)[0][0]))
    summaries=[]
    for label,poi_id in targets:
        try:
            data,identifier,when=client.get('/place/detail',{'id':poi_id,'extensions':'all'})
            result=data.get('pois') or []
            item=result[0] if result else {}
            summaries.append({'label':label,'poi_id':poi_id,'name':item.get('name',''),
                'location':item.get('location',''),'typecode':item.get('typecode',''),
                'entr_location':item.get('entr_location',''),'exit_location':item.get('exit_location',''),
                'children_count':len(item.get('children') or []),'request_id':identifier,
                'collected_at_utc':when,'error':''})
        except RuntimeError as exc:
            summaries.append({'label':label,'poi_id':poi_id,'error':str(exc)})
            break
    (ROOT/'output/poi_details_probe.json').write_text(json.dumps({'calls':client.calls,'school_parent_counts':parents,'results':summaries},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summaries,ensure_ascii=True))


if __name__=='__main__':
    main()
