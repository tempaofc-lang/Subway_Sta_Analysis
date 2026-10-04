"""Bounded official API collection. Credentials never enter logs or cache keys."""
import argparse
import ast
import hashlib
import json
import os
import time
from datetime import datetime, timezone

import requests

from audit_local import ROOT, SOURCE, PILOTS, coordinates, distance, normalize, read_csv, write_csv


def credential():
    key = os.environ.get('AMAP_WEB_SERVICE_KEY')
    if key:
        return key, 'environment'
    env_path = ROOT / '.env'
    if env_path.exists():
        for line in env_path.read_text(encoding='utf-8-sig').splitlines():
            if line.strip().startswith('AMAP_WEB_SERVICE_KEY='):
                value = line.split('=',1)[1].strip().strip('\"').strip("'")
                if value:
                    return value, 'local_env_file'
    # Read the literal only; do not import the old config or execute legacy code.
    tree = ast.parse((SOURCE/'code/config.py').read_text(encoding='utf-8-sig'))
    for node in tree.body:
        if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='AMAP_KEY' for t in node.targets):
            if isinstance(node.value,ast.Constant) and isinstance(node.value.value,str) and node.value.value:
                return node.value.value, 'legacy_local_config'
    raise RuntimeError('No local credential available')


class SafeClient:
    def __init__(self, budget):
        self.key, self.credential_source = credential()
        self.budget, self.calls = budget, 0
        self.last = 0
        self.session = requests.Session()
        self.directory = ROOT/'data/raw/amap_entrances'
        self.directory.mkdir(parents=True,exist_ok=True)

    def get(self, endpoint, params):
        if self.calls>=self.budget:
            raise RuntimeError('Request budget reached')
        time.sleep(max(0,1.1-(time.monotonic()-self.last)))
        request = {'endpoint': endpoint, 'params': params}
        now = datetime.now(timezone.utc).isoformat()
        identifier = (hashlib.sha256(json.dumps(request,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:20]
                      + '_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
        self.calls += 1
        try:
            response = self.session.get('https://restapi.amap.com/v3'+endpoint,
                                       params={**params,'key':self.key},timeout=(10,25))
            response.raise_for_status()
            data = response.json()
            error = None
        except Exception as exc:
            # Exception strings may contain credential-bearing URLs; never serialize them.
            data, error = {}, type(exc).__name__
        finally:
            self.last = time.monotonic()
        record = {**request,'collected_at_utc':now,'response':data,'transport_error':error}
        (self.directory/f'{identifier}.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
        if error:
            raise RuntimeError('Transport failure: '+error)
        if str(data.get('status'))!='1':
            raise RuntimeError('API rejected request; infocode='+str(data.get('infocode','unknown')))
        return data, identifier, now


def collect(budget=20, probe=False):
    client = SafeClient(budget)
    stations = [r for r in read_csv(ROOT/'data/processed/stations_audit.csv') if normalize(r['station']) in PILOTS]
    collected = {}
    queries=[]
    errors=[]
    for station in stations:
        name = normalize(station['station'])
        origin = (float(station['lng']),float(station['lat']))
        for method in ('around','text'):
            if probe and queries:
                break
            page=1
            query_ids=[]
            reported=0
            unique_ids=set()
            returned=0
            while True:
                params={'types':'150501','offset':25,'page':page,'extensions':'all','children':1}
                if method=='around':
                    params.update({'location':f"{origin[0]},{origin[1]}",'radius':1000,'sortrule':'distance'})
                else:
                    params.update({'keywords':name+'地铁站','city':'360100','citylimit':'true'})
                try:
                    data,identifier,when=client.get('/place/'+method,params)
                except RuntimeError as exc:
                    errors.append({'station':name,'method':method,'error':str(exc)})
                    break
                query_ids.append(identifier)
                pois=data.get('pois') or []
                reported=int(data.get('count') or 0)
                returned+=len(pois)
                unique_ids.update(p.get('id') for p in pois if p.get('id'))
                for poi in pois:
                    loc=coordinates(poi.get('location'))
                    if not loc or not poi.get('id'):
                        continue
                    matched=name in poi.get('name','')
                    item_key=(station['station_id'],poi['id'])
                    if item_key not in collected:
                        collected[item_key]={'station_id':station['station_id'],'station':station['station'],
                            'entrance_poi_id':poi['id'],'entrance_name':poi.get('name',''),'typecode':poi.get('typecode',''),
                            'lng':loc[0],'lat':loc[1],'coordinate_system':'GCJ-02',
                            'parent_poi_id':poi.get('parent') if isinstance(poi.get('parent'),str) else '',
                            'distance_to_station_m':round(distance(origin,loc),2),
                            'name_matches_station':matched,'verification_status':'map_candidate' if matched else 'other_station_or_unmatched',
                            'open_status':'unknown','field_verified':False,'request_ids':identifier,
                            'source_url':'https://ditu.amap.com/place/'+poi['id'],'collected_at_utc':when}
                    elif identifier not in collected[item_key]['request_ids'].split(';'):
                        collected[item_key]['request_ids']+=';'+identifier
                if not pois or returned>=reported or page>=4 or probe:
                    break
                page+=1
            queries.append({'station':name,'method':method,'request_ids':query_ids,'api_reported_last_count':reported,
                            'returned_records':returned,'unique_ids':len(unique_ids),'matches_reported_count':len(unique_ids)==reported and bool(query_ids)})
            if errors:
                break
        if errors or probe:
            break
    if collected:
        write_csv(ROOT/'data/processed/entrance_candidates.csv',list(collected.values()))
    summary={'calls':client.calls,'budget':budget,'credential_source':client.credential_source,
             'candidate_count':sum(r['name_matches_station'] for r in collected.values()),
             'queries':queries,'errors':errors,'collected_at_utc':datetime.now(timezone.utc).isoformat()}
    target='api_probe.json' if probe else 'entrance_collection.json'
    (ROOT/'output'/target).write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=True))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--budget',type=int,default=20)
    parser.add_argument('--probe',action='store_true')
    args=parser.parse_args()
    collect(args.budget,args.probe)
