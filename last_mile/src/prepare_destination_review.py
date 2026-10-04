"""Prepare destination candidates without claiming verified public access."""
import json
from collections import Counter

from audit_local import ROOT, read_csv, write_csv, coordinates, distance


def main():
    rows=read_csv(ROOT/'data/processed/pilot_pois_refreshed.csv')
    detail=json.loads((ROOT/'output/poi_details_probe.json').read_text(encoding='utf-8'))
    campus=next(r for r in detail['results'] if r['label']=='瑶湖西学校共同父POI' and not r.get('error'))
    review=[]
    for r in rows:
        center=coordinates(f"{r['lng']},{r['lat']}")
        candidate=coordinates(r['entr_location'])
        is_college=r['station']=='瑶湖西' and r['category']=='学校' and '江西师范大学' in r['name']
        is_dorm=r['station']=='瑶湖西' and '江西师范大学' in r['name'] and r['category']=='住宅'
        item={**r,'proposed_entity_id':r['poi_id'],'entity_merge_evidence':'none',
              'candidate_arrival_lng':candidate[0] if candidate else '',
              'candidate_arrival_lat':candidate[1] if candidate else '',
              'candidate_arrival_kind':'api_entrance_coordinate_unverified' if candidate else 'no_entrance_field',
              'entrance_to_center_m':round(distance(center,candidate),2) if center and candidate else '',
              'public_access_status':'unknown','ready_for_verified_analysis':False,
              'review_action':'核验公众到达点、对象是否重复、道路连接和开放条件'}
        if is_college:
            candidate=coordinates(campus['entr_location'])
            item.update({'proposed_entity_id':campus['poi_id'],
                         'entity_merge_evidence':'parent_id_confirmed' if r['parent_poi_id']==campus['poi_id'] else 'name_inferred_needs_review',
                         'candidate_arrival_lng':candidate[0] if candidate else '',
                         'candidate_arrival_lat':candidate[1] if candidate else '',
                         'candidate_arrival_kind':'campus_api_entrance_unverified',
                         'entrance_to_center_m':'',
                         'review_action':'学院合并为校区候选；核验校区公众大门；不可使用校内学院门口代替校区入口'})
        elif is_dorm:
            item['review_action']='校园宿舍不按普通公开住宅区计入；确认校园通行条件及住宅研究口径'
        review.append(item)
    write_csv(ROOT/'data/processed/destination_entity_review.csv',review)
    summary={'rows':len(review),'valid_api_entrance_fields':sum(bool(coordinates(r['entr_location'])) for r in rows),
             'college_rows_proposed_for_campus_merge':sum(r['proposed_entity_id']==campus['poi_id'] for r in review),
             'merge_evidence_counts':dict(Counter(r['entity_merge_evidence'] for r in review)),
             'all_public_access_unknown':all(r['public_access_status']=='unknown' for r in review)}
    (ROOT/'output/destination_review.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=True))


if __name__=='__main__':
    main()
