"""Combine the local audit, API snapshots and explicitly graded desk evidence."""
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone

from audit_local import ROOT, SOURCE, PILOTS, normalize, read_csv, write_csv, coordinates, distance

OPERATOR_REPLY='https://wenz.jxnews.com.cn/ms/viewpage/202509/view_481718.html'
BAYI_NOTICE='https://www.ncmtr.com/topic_detail_18/4507027.html'
UNIVERSITY_NOTICE='https://tianyuan.xmu.edu.cn/cn/news/3818.html'


def main():
    out=ROOT/'output'
    audit=json.loads((out/'local_audit.json').read_text(encoding='utf-8'))
    rows=read_csv(ROOT/'data/processed/entrance_candidates.csv')
    verified=[]
    for r in rows:
        if r['name_matches_station']!='True':
            continue
        name=normalize(r['station'])
        match=re.search(r'([0-9]+(?:-[0-9]+)?)号口$',r['entrance_name'])
        label=match.group(1) if match else ''
        parent_match=r['parent_poi_id']==r['station_id']
        r.update({'entrance_label':label,'parent_matches_station':parent_match,
                  'both_search_methods_returned':len(r['request_ids'].split(';'))>=2,
                  'desk_evidence_level':'map_record_only','access_class':'unresolved',
                  'evidence_url':'','evidence_date':'','eligible_for_verified_open_analysis':False,
                  'next_check':'核对标牌、地面入口位置、通往站厅及当前开放时间'})
        if name=='地铁大厦':
            r.update({'desk_evidence_level':'operator_reply_historical','evidence_url':OPERATOR_REPLY,
                      'evidence_date':'2025-09-18'})
            if label in ('1','2','4','5'):
                r.update({'access_class':'station_entrance_operator_identified',
                          'next_check':'确认当前开放、入口位置与道路连接；运营方历史回复支持入口类别'})
            else:
                r.update({'access_class':'commercial_access_requires_check',
                          'next_check':'核实商业区开放时间、能否公众通行到站厅、是否与车站入口重复'})
        elif name=='八一广场' and label in ('8','9','14'):
            r.update({'desk_evidence_level':'operator_notice_search_title_only',
                      'access_class':'historical_station_entrance_notice','evidence_url':BAYI_NOTICE,
                      'evidence_date':'2021-12 (search metadata)',
                      'next_check':'历史官方公告可检索，正文访问受限；核对当前编号、位置及开放状态'})
        elif name=='瑶湖西' and label=='3':
            r.update({'desk_evidence_level':'institution_travel_notice_historical',
                      'access_class':'historical_public_access_evidence','evidence_url':UNIVERSITY_NOTICE,
                      'evidence_date':'2025-07-10',
                      'next_check':'2025年机构出行指引提及3口；确认当前开放与步行连接'})
        elif not label:
            r['next_check']='缺少编号，确认是否实体地面口、站内/商业口或重复地图点；不直接纳入计算'
        verified.append(r)
    write_csv(ROOT/'data/processed/entrance_desk_review.csv',verified)
    walk=json.loads((out/'walking_api_probe.json').read_text(encoding='utf-8'))
    collection=json.loads((out/'entrance_collection.json').read_text(encoding='utf-8'))
    refresh_path=out/'pilot_poi_refresh.json'
    refresh=json.loads(refresh_path.read_text(encoding='utf-8')) if refresh_path.exists() else None
    details_path=out/'poi_details_probe.json'
    poi_details=json.loads(details_path.read_text(encoding='utf-8')) if details_path.exists() else None
    coverage=read_csv(ROOT/'data/processed/poi_cache_coverage.csv')
    docs=[
        {'url':OPERATOR_REPLY,'publisher':'南昌轨道交通集团客户服务中心，经问政江西刊载',
         'published_at':'2025-09-18','access':'body_read','supported_fact':'地铁大厦站1、2、4、5号为车站出入口，其余为商业出入口；不证明当前开放状态。'},
        {'url':BAYI_NOTICE,'publisher':'南昌轨道交通集团','published_at':'2021-12 (search metadata)',
         'access':'search_title_only_body_403','supported_fact':'历史公告标题提及八一广场8、9、14号口对外开放；正文未核验，不证明当前状态。'},
        {'url':UNIVERSITY_NOTICE,'publisher':'国家天元数学东南中心/厦门大学','published_at':'2025-07-10',
         'access':'body_read','supported_fact':'活动交通指引明确使用瑶湖西3号口，支持历史公众使用，不证明当前开放。'},
        {'url':'https://lbs.amap.com/faq/webservice/webservice-api/poi-search/43253',
         'publisher':'高德开放平台','access':'search_body_available',
         'supported_fact':'150501为出入口搜索类别；与150500合并查询时存在父子POI聚合。'},
        {'url':'https://ditu.amap.com/place/BT10020306','publisher':'高德地图',
         'access':'search_body_only','supported_fact':'公开页面检索到瑶湖西3号口坐标，与新API同名入口坐标相同；BT网页ID与BX接口ID不同，不能只按跨入口ID合并。'}]
    (out/'desk_sources.json').write_text(json.dumps({'checked_on':'2026-10-04','sources':docs},ensure_ascii=False,indent=2),encoding='utf-8')
    total=collection['calls']+walk['calls']+1+(refresh['calls'] if refresh else 0)+(poi_details['calls'] if poi_details else 0)
    matched=audit['coverage_status_counts'].get('matches_reported_count',0)
    lines=['# 本地数据审计与三站入口桌面核验', '', '核验日期：2026-10-04（北京时间）。', '',
           '## 结论', '',
           '已有站点表可复用，旧POI缓存可作为候选池；入口和步行接口已经实测可用。当前尚未完成入口现场核验、目的地入口整理或任何站点可达率计算。', '',
           '旧项目保留原状，新结果单独写入 last_mile。使用旧项目本机已有密钥，无需用户重新提供；日志、请求缓存键、报告均不写密钥。', '',
           '## 本地资料', '',
           f'- 113个站点均成功关联数量表和分类表，均解析到唯一高德站点ID；坐标非空且通过宽范围检查。数量列、总量和功能熵的舍入值一致。运营状态与坐标实地精度不在该检查结论内。',
           f'- 缓存 {audit["cache_requests"]:,} 个响应，含 {audit["poi_records"]:,} 条POI记录、{audit["unique_poi_ids"]:,} 个唯一POI ID。跨站点重叠造成重复很正常；同ID有名称/位置/类别差异的有 {audit["ids_with_conflicting_name_location_or_type"]} 个，需保留版本。',
           f'- 904个“站点×类别”组合中，{matched}个明细与第一页报告数量相符，其余 {904-matched} 个存在数量差异或重复，无法据此确认完整性；这些差异可能来自API枚举/聚合或分页重复，不等于旧采集程序遗漏了全部差额。“相符”不等于现实世界完整。',
           f'- {sum(int(r["duplicate_records"])>0 for r in coverage)}个组合存在组内重复ID；{audit["csv_vs_first_count_mismatches"]}个组合的旧CSV数量与第一页count不同。这通常与旧代码累计分页返回量有关，不能全部解释为漏采或数据错误。',
           f'- {sum(int(r["api_reported_first_count"])>200 for r in coverage)}个组合第一页count大于200；其中64个恰为600。末页空响应会返回count=0，本次未将它误判为非空页数量变化。count=600集中出现提示上限/截断的可能，尚未证实具体机制。',
           '- 本地请求缓存没有可靠的采集时间；POI自身timestamp不能当作采集时间。所有旧候选标注年份未知。',
           '- 原缓存未找到150501出入口POI，也没有可用的children/entr_location。', '',
           '## 三站目的地候选', '',
           '以下仅为原1公里查询缓存中有坐标、按站点/类别/POI ID去重后的候选数，未合并建筑或校区，也未核验目的地公众入口。', '',
           '|站点|住宅|写字楼|学校|医院|', '|---|---:|---:|---:|---:|']
    for p in audit['pilots']:
        c=p['destination_counts']
        lines.append('|'+normalize(p['station'])+'|'+'|'.join(str(c.get(k,0)) for k in ('住宅','写字楼','学校','医院'))+'|')
    lines.extend(['', '重要口径问题：瑶湖西的13个学校候选名称全部指向江西师范大学的不同学院。它们不是13所独立学校；住宅候选也包含校园宿舍。地铁大厦的写字楼缓存133条记录去重后为131个ID。抽样前须统一“校区/建筑/小区”为分析对象，并查明真实入口。', '',
                  '## 新采入口及桌面核验', '',
                  f'成功完成6次入口查询：分别按150501做周边搜索和关键词搜索，三个站点各两次。匹配到本站的 {len(verified)} 个不同POI ID均同时出现在两种搜索中，且parent与站点ID一致；这是同一提供商内部一致性检查，不是独立来源核验。', '',
                  '|站点|地图候选点|入口内容|桌面判断|', '|---|---:|---|---|'])
    for name in PILOTS:
        members=[r for r in verified if normalize(r['station'])==name]
        details={'八一广场':'1、2、3、4、5、7、8、9、14号口及1个无编号点',
                 '地铁大厦':'1—5号口、3个无编号点、更新天地南/北口',
                 '瑶湖西':'2-1、2-2、3号口'}[name]
        notes={'八一广场':'8、9、14有历史官方公告线索；其他入口及当前开放待核验',
               '地铁大厦':'运营方历史回复确认1、2、4、5为车站口；其余按商业通道另查',
               '瑶湖西':'3号口有2025年机构出行指引；当前状态及其余入口待核验'}[name]
        lines.append(f'|{name}|{len(members)}|{details}|{notes}|')
    lines.extend(['',
        f'地铁大厦的入口类别依据为运营方客户服务中心2025-09-18的[公开回复]({OPERATOR_REPLY})。商业口可能通往站厅，但开放时间、公众通行及连接条件需要另查，不能简单计为全天开放地铁口。',
        f'八一广场8、9、14号入口见[历史官方公告]({BAYI_NOTICE})。本次仅能检索标题，正文返回403；保留线索等级，未标为正文确认。',
        f'瑶湖西3号口见2025-07-10的[机构交通指引]({UNIVERSITY_NOTICE})。公开地图网页ID与新API ID不同，匹配时同时比较名称和坐标。',
        '', '当前23个候选点的开放状态均记为unknown，现场核验均为false。“地图候选点数”不能当作“确认开放入口数”。无编号点需要查明是否地面实体入口、站内点或重复点。', '',
        '## 步行接口实测', '',
        '每站选一个旧住宅POI中心代理点，向一个候选地铁口请求路线；以下只验证接口能力，不代表最近入口、不代表典型居民路线，也不用于站点排名。', '',
        '|站点|所选入口|API距离（米）|API时间（秒）|折线|', '|---|---|---:|---:|---|'])
    for r in walk['samples']:
        lines.append(f'|{r["station"]}|{r["entrance_name"]}|{r["distance_m"]}|{r["api_duration_seconds"]}|{"有" if r["has_polyline"] else "无"}|')
    lines.extend(['', f'截至本报告，成功API调用共 {total} 次：入口探测1次、入口查询6次、步行探测3次'+(f'、目的地复采{refresh["calls"]}次' if refresh else '')+(f'、父POI详情{poi_details["calls"]}次' if poi_details else '')+'。另有1次沙箱网络尝试被代理阻断，未获得API响应；未批量采集全市或进行付费开通。API时间与规划中的固定步速时间需分列。', '',
        '## 下一步', '',
        '1. 优先核验地铁大厦1、2、4、5号口；对3号口、商业南北口和无编号点建立补查清单。',
        '2. 核对八一广场当前编号、过街通道与车站口关系；核对瑶湖西2-1、2-2、3口开放情况。',
        '3. 将同校区学院、同一建筑重复POI合并，住宅区优先标定公众大门；校内宿舍单独处理。',
        '4. 校区入口可能在1公里圆之外，不能只沿用圆内POI作为入口；若校园门口缺失，应补充采集并标记空间筛选口径。',
        '5. 形成入口集合和目的地样本后，再计算每站多个入口的最短步行路线并制作可达性地图。第一批可先每站10个目的地；瑶湖西按实际独立对象数采样，不强凑40个。',
        '', '## 输出文件', '',
        '- `data/processed/stations_audit.csv`：全站关联与ID核查。',
        '- `data/processed/poi_cache_coverage.csv`：904个组合的分页、数量与去重审计。',
        '- `data/processed/pilot_destinations.csv`：三站主要类别候选明细。',
        '- `data/processed/entrance_candidates.csv`：新采入口及邻站干扰记录。',
        '- `data/processed/entrance_desk_review.csv`：23个本站候选的逐点证据与待查项。',
        '- `data/processed/walking_api_probe.csv`：三条接口验证路线摘要。',
        '- `data/processed/pilot_pois_refreshed.csv`、`pilot_poi_refresh_comparison.csv`：带时间戳的新候选与新旧逐类比较。',
        '- `data/processed/destination_entity_review.csv`：目的地入口字段与实体合并待查清单。',
        '- `output/local_audit.json`、`desk_sources.json`及原始快照：结果与来源记录。',
        '', '复现方式见本目录README。原始快照限本地保存；派生POI资料继续保留高德来源，公开分发前按平台条款核对。'])
    additions=[]
    if refresh:
        fresh=read_csv(ROOT/'data/processed/pilot_pois_refreshed.csv')
        valid_entrances=sum(bool(r['entr_location']) for r in fresh)
        additions.extend(['## 本次复采与审计结论校正','',
            f'在2026-10-04另用 {refresh["calls"]} 次请求重新采集三站住宅、写字楼、学校、医院，共12组查询、{refresh["poi_records"]}条按站点/类别/ID去重的候选。新结果单独保存，不覆盖旧缓存；查询中心和半径保持不变，并明确设置距离排序。',
            '', '|站点|类别|旧缓存唯一ID|新API count|新返回记录|新唯一ID|',
            '|---|---|---:|---:|---:|---:|'])
        for r in refresh['comparison']:
            additions.append(f'|{r["station"]}|{r["category"]}|{r["legacy_unique_ids"]}|{r["fresh_first_api_count"]}|{r["fresh_records"]}|{r["fresh_unique_ids"]}|')
        additions.extend(['',
            '12组新查询全部取得正常响应；6组去重明细与count相符（其中2组count=0），6组不符。例如地铁大厦写字楼返回137条但只有135个不同ID，学校count=16但明细14条，医院count=60但明细56条。后一页空响应仍无新增数据。差异在复采中重现，因此不能把旧审计中的全部差额解释为缓存损坏或未翻页。',
            '新旧之间数量和ID变化只能描述为快照差异：旧采集日期未知，不能据此计算增长率，亦不能把每个变化判为旧数据错误。当前633条候选是查询可获取明细，不宣称完整目的地清单。',
            f'使用extensions=all取得 {valid_entrances} 条非空入口坐标字段，作为目的地到达点候选；仍需验证其是否公众可通行的建筑/小区/校区入口。没有入口字段时保留中心代理状态，不自动混用。',''])
    if poi_details:
        campus=next((r for r in poi_details['results'] if r['label']=='瑶湖西学校共同父POI' and not r.get('error')),None)
        additions.extend(['## 父POI与校区口径核查','',
            '三个站点详情接口均返回与旧站点相同的ID、名称和坐标，支持本次入口parent匹配；详情中的children和入口字段为空，不能从站点中心推定地面出入口。'])
        if campus:
            yaohu=next(p for p in audit['pilots'] if normalize(p['station'])=='瑶湖西')
            station_point=(float(yaohu['lng']),float(yaohu['lat']))
            center_distance=distance(station_point,coordinates(campus['location']))
            entrance_distance=distance(station_point,coordinates(campus['entr_location']))
            additions.extend([f'瑶湖西13个学院候选中12个明确指向父ID `{campus["poi_id"]}`，其详情名称为“{campus["name"]}”；剩余一个学院父字段缺失，名称指向同一大学，拟合并但需人工确认。',
                f'父校区中心坐标 `{campus["location"]}`，入口字段 `{campus["entr_location"]}`。其中心距站点约{center_distance:.0f}米，接口入口距站点约{entrance_distance:.0f}米（同源坐标的近似直线距离）。校园内部学院的入口字段不能替代校园公众大门。按“中心在1公里内”的筛选会遗漏部分跨越研究范围的校园，需先确定校区范围和目的地定义。',''])
    if additions:
        index=lines.index('## 新采入口及桌面核验')
        lines[index:index]=additions
    (out/'本地审计与入口核验报告.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    # Assert unchanged original inputs using their recorded hashes.
    for item in audit['input_manifest']:
        assert hashlib.sha256((SOURCE/item['path']).read_bytes()).hexdigest()==item['sha256'], item['path']
    assert len(verified)==23 and all(r['parent_matches_station'] and r['both_search_methods_returned'] for r in verified)
    assert all(r['open_status']=='unknown' for r in verified)
    assert all(r['status']=='route_returned' and r['has_polyline'] for r in walk['samples']) and len(walk['samples'])==3
    print(json.dumps({'original_input_hashes_unchanged':True,'desk_review_rows':len(verified),
                      'access_class_counts':dict(Counter(r['access_class'] for r in verified)),
                      'report_written':True},ensure_ascii=True))


if __name__=='__main__':
    main()
