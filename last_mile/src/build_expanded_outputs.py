"""Main-agent integration: publish conditional model results with group sizes."""
import json
from collections import Counter
from audit_local import ROOT, write_csv


def main():
    data=json.loads((ROOT/'output/expanded_results.json').read_text(encoding='utf-8'))
    aliases={'resident':'居民通勤','student':'学生通勤','other':'其他目的地'}
    for s in data['samples']+data['scenarios']:
        s['commuter_group']=aliases.get(s.get('commuter_group'),s.get('commuter_group','其他目的地'))
    stations=list(dict.fromkeys(s['station'] for s in data['samples']))
    groups=['全部样本','居民通勤','学生通勤','其他目的地']
    summaries=[]
    for station in stations:
        for group in groups:
            for speed in [4.5,3.5]:
                for threshold in [10,15]:
                    rows=[s for s in data['scenarios'] if s['station']==station and s['speed_kmh']==speed
                          and s['threshold_min']==threshold and (group=='全部样本' or s.get('commuter_group')==group)]
                    statuses=Counter(r['model_status'] for r in rows)
                    n=len(rows)
                    covered=statuses['model_covered']
                    unknown=statuses['unknown']
                    summaries.append({'station':station,'commuter_group':group,'speed_kmh':speed,
                        'threshold_min':threshold,'samples':n,'model_covered':covered,
                        'model_not_covered':statuses['model_not_covered'],'unknown':unknown,
                        'coverage_lower':covered/n if n else '',
                        'coverage_upper':(covered+unknown)/n if n else ''})
    write_csv(ROOT/'data/processed/expanded_group_summary.csv',summaries)
    template=(ROOT/'web/access_template.html').read_text(encoding='utf-8')
    payload=json.dumps(data,ensure_ascii=False).replace('<','\\u003c')
    (ROOT/'output/扩展站点步行接驳地图.html').write_text(template.replace('__DATA__',payload),encoding='utf-8')
    college=[s for s in data['samples'] if s.get('commuter_group')=='学生通勤']
    valid=sum(r['status']=='model_route' for r in data['routes'])
    rate=valid/len(data['routes']) if data['routes'] else 0
    lines=['# 南昌地铁步行接驳：扩展站点与学生通勤情景','',
           '日期：2026-10-04。根据用户最新要求，暂不考虑门禁、临时封路及入口临时关闭，选定地图入口在本情景中视为可使用。其他数据缺失或路线几何异常仍保留未知。','',
           f'范围：{len(stations)}个站点、{len(data["samples"])}个样本、{len(college)}个高校入口学生通勤代表点，共{len(data["routes"])}条路线。站点：'+ '、'.join(stations)+'。','',
           '## 样本与方法','',
           '居民区、学生通勤和其他目的地分别归组。本轮使用大学/校区父POI的入口字段作为大学校门代表点，未通过独立门POI或现场确认其实体校门位置；来源等级保留为接口入口代理。它代表学生从校园进入城市路网的起点，校内宿舍至校门距离不计入；不把学院门口当作独立校区，不用校门或POI个数估计人口。单个校区的不同校门若均保留，按门记录，报告注明它们共享校区。',
           '采用同源GCJ-02的目的地入口 → 地铁各基础入口路线，选通过几何检查的最短距离。4.5 km/h为主情景，3.5 km/h为敏感性情景；10与15分钟阈值。最短路是本候选入口集合中的最小值，不能保证覆盖所有未收录入口。',
           '居民区及办公等主样本候选范围为站点中心1公里；学生校门代表点允许至1.5公里并单独标记。南昌航空大学前湖校区父POI入口距站约1043米，因此纳入学生扩展样本；名为北门的公交POI并未当作校门。学生组与其他组的筛选范围不同，必须分组比较，不能用混合比例推断空间公平性。',
           '样本属于目的性案例选择，各站类别和数量不同；比例只描述所选样本，不能据此判断全站居民、学生覆盖率或功能类型的因果差异。学校类可能含托育、医疗类可能含诊所，以实际对象名称为准。','',
           '## 主情景结果','',
           '|站点|样本数|10分钟模型时限内|15分钟模型时限内|10分钟未知|15分钟未知|',
           '|---|---:|---:|---:|---:|---:|']
    for station in stations:
        a=next(s for s in summaries if s['station']==station and s['commuter_group']=='全部样本' and s['speed_kmh']==4.5 and s['threshold_min']==10)
        b=next(s for s in summaries if s['station']==station and s['commuter_group']=='全部样本' and s['speed_kmh']==4.5 and s['threshold_min']==15)
        lines.append(f'|{station}|{a["samples"]}|{a["model_covered"]}|{b["model_covered"]}|{a["unknown"]}|{b["unknown"]}|')
    lines.extend(['','## 学生通勤起点','',
                  '|站点|校门/校区代表点|样本依据|10分钟模型结果|15分钟模型结果|',
                  '|---|---|---|---|---|'])
    for s in college:
        scenes=[r for r in data['scenarios'] if r['sample_id']==s['sample_id'] and r['speed_kmh']==4.5]
        status=lambda t: next(({'model_covered':'时限内','model_not_covered':'超时限','unknown':'未知'}.get(r['model_status'],'未知') for r in scenes if r['threshold_min']==t),'未知')
        basis=s.get('point_basis',s.get('source_method',''))
        lines.append(f'|{s["station"]}|{s["name"]}|{basis}|{status(10)}|{status(15)}|')
    lines.extend(['','## 分组比较（4.5 km/h，10分钟）','',
                  '|站点|通勤组|样本数|模型时限内|模型超时限|未知|',
                  '|---|---|---:|---:|---:|---:|'])
    for s in summaries:
        if s['commuter_group']!='全部样本' and s['speed_kmh']==4.5 and s['threshold_min']==10 and s['samples']:
            lines.append(f'|{s["station"]}|{s["commuter_group"]}|{s["samples"]}|{s["model_covered"]}|{s["model_not_covered"]}|{s["unknown"]}|')
    lines.extend(['','## 质量与扩展门槛','',
        f'{valid}/{len(data["routes"])}条路线通过几何检查，有效率{rate:.1%}。条件包括正距离、折线存在、请求起终点偏移≤60米、折线长度检查。预设技术门槛为95%；本轮'+('通过。' if rate>=.95 else '未通过，不能将扩展结果作为完成验收的结论。'),
        '未知和已覆盖分别记录：若有有效路线已在阈值内可判模型覆盖；若部分入口异常且其余超时限则为未知。报告CSV给出全部样本覆盖比例上下界，空组不报告比例。',
        '当前门禁和临时封路假设由用户明确授权，不再沿用旧三站报告中“等待现场确认后才能扩展”的门槛。模型结果不能作为路口实际开放、无障碍通行或信号灯等待时间的证明。','',
        '## 可复核成果','',
        '- [扩展交互地图](扩展站点步行接驳地图.html)：站点、通勤组、时间和步速筛选；大学校门标记与路线详情。',
        '- `data/processed/expanded_samples.csv`、`expanded_entrances.csv`：样本与基础入口及来源。',
        '- `data/processed/expanded_accessibility.csv`：逐样本四组情景。',
        '- `data/processed/expanded_group_summary.csv`：逐站分组人数无关的样本计数与未知区间。',
        '- `output/expanded_results.json`：路线、来源ID、几何检查与场景汇总。',
        '- `ANALYSIS_ASSUMPTIONS.md`：本轮分析假设；`PROJECT_STATE.md`：主智能体/子代理分区和接续状态。'])
    (ROOT/'output/扩展站点步行接驳分析报告.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    current_gate={'assumption':'ignore_access_control_temporary_closures','route_pass_rate':rate,
        'model_expansion_gate_pass':rate>=.95,'physical_access_assumed_for_scenario':True,
        'station_count':len(stations),'student_gate_samples':len(college)}
    (ROOT/'output/current_expansion_gate.json').write_text(json.dumps(current_gate,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(current_gate,ensure_ascii=True))


if __name__=='__main__': main()
