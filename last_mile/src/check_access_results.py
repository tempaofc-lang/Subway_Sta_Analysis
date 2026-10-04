"""Independent checks against raw snapshots and parameter monotonicity."""
import json
import math
from collections import Counter
from audit_local import ROOT, distance, read_csv
from collect_entrances import credential


def main():
    d=json.loads((ROOT/'output/access_results.json').read_text(encoding='utf-8'))
    assert len(d['samples'])==24 and len({r['sample_id'] for r in d['samples']})==24
    sample_by_id={s['sample_id']:s for s in d['samples']}
    for r in d['routes']:
        raw=json.loads((ROOT/'data/raw/access_routes'/f"{r['request_id']}.json").read_text(encoding='utf-8'))
        assert raw['response']['status']=='1'
        assert min(float(p['distance']) for p in raw['response']['route']['paths'])==r['distance_m']
        assert all(math.isfinite(v) for p in r['polyline'] for v in p)
    for s in d['samples']:
        scenarios=[r for r in d['scenarios'] if r['sample_id']==s['sample_id']]
        assert len(scenarios)==4
        for speed in [3.5,4.5]:
            a=next(r for r in scenarios if r['speed_kmh']==speed and r['threshold_min']==10)
            b=next(r for r in scenarios if r['speed_kmh']==speed and r['threshold_min']==15)
            assert not(a['model_status']=='model_covered' and b['model_status']!='model_covered')
        for t in [10,15]:
            a=next(r for r in scenarios if r['speed_kmh']==3.5 and r['threshold_min']==t)
            b=next(r for r in scenarios if r['speed_kmh']==4.5 and r['threshold_min']==t)
            assert not(a['model_status']=='model_covered' and b['model_status']!='model_covered')
        route=min([r for r in d['routes'] if r['sample_id']==s['sample_id']],key=lambda r:r['distance_m'])
        assert all(r['best_distance_m']==route['distance_m'] for r in scenarios)
    key,_=credential()
    assert not any(key.encode() in p.read_bytes() for p in ROOT.rglob('*') if p.is_file() and '__pycache__' not in p.parts)
    result={'raw_routes_match_results':True,'route_minima_verified':True,'scenario_monotonicity_pass':True,
            'no_credentials_in_outputs':True,'samples':24,'routes':142,'physical_passability_verified':False}
    (ROOT/'output/access_validation.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result))


if __name__=='__main__': main()
