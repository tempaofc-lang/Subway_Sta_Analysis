import json
from collections import Counter
from audit_local import ROOT, read_csv, write_csv


def main():
    data=json.loads((ROOT/'output/access_results.json').read_text(encoding='utf-8'))
    # JSON is embedded as data; prevent provider text from ending a script tag.
    payload=json.dumps(data,ensure_ascii=False).replace('<','\\u003c')
    template=(ROOT/'web/access_template.html').read_text(encoding='utf-8')
    (ROOT/'output/步行接驳地图.html').write_text(template.replace('__DATA__',payload),encoding='utf-8')
    reviews=[]
    for r in data['routes']:
        hints=[s['instruction'] for s in r['steps'] if any(w in s['instruction'] for w in ['人行横道','天桥','地下','隧道','过街','台阶'])]
        reviews.append({'station':r['station'],'sample_id':r['sample_id'],'entrance_name':r['entrance_name'],
                        'route_status':r['status'],'distance_m':r.get('distance_m',''),
                        'start_snap_m':r.get('snap_start_m',''),'end_snap_m':r.get('snap_end_m',''),
                        'detour_ratio':r.get('detour_ratio',''), 'crossing_hints':'；'.join(hints),
                        'request_id':r.get('request_id',''),'physical_crossing_verified':False,
                        'review_priority':'high' if r['status']!='model_route' or (r.get('detour_ratio') or 0)>2 else 'normal'})
    write_csv(ROOT/'data/processed/route_passability_review.csv',reviews)
    lines=['# 三站步行接驳：样本、路线与核验结果','',
           '计算日期：2026-10-04。结果为接口入口和导航路线成立条件下的模型估算，公众通行、信号灯等待、临时封路与地铁口当前开放尚未获得现场确认。','',
           f'目的地样本共{len(data["samples"])}个，按住宅4、写字楼3、学校2、医院1的每站上限和近/中/远距离带轮选。按父POI合并同类别实体，校园宿舍剔除，同大学学院合并校区。仅选择有入口字段且中心到入口偏移不超过250米的候选（校区按父记录入口）。不足的类别不补齐。属于有目的的案例样本，不能用作全站人口或总体覆盖率。','',
           '地铁大厦只用运营方历史确认的1、2、4、5号口；八一广场用9个编号地图候选；瑶湖西用2-1、2-2、3号口。商业口和无编号点未纳入基础情景。这些入口集合仍不保证当前完整开放。','',
           '教育与医疗沿用旧POI检索类别，样本中包含托育中心、医美诊所，不能把它们解读为独立学校或综合医院。本轮作为接驳目的地案例展示，后续需按用途再细分。','',
           '路线核查：每个样本请求所有选定入口，选最短通过几何检查的路线。几何检查要求起终点与请求点偏移≤60米，正距离、非空折线，折线长度≤报告距离的1.25倍或多50米。无法通过者保留待核验，不能当作不可达；部分入口失败时，只有已有时限内路线可判模型覆盖。','',
           f'完成{len(data["routes"])}条路线；状态：{json.dumps(Counter(r["status"] for r in data["routes"]),ensure_ascii=False)}。本轮导航步骤未明确返回人行横道/地下/天桥等关键词，逐条表中的crossing_hints因此为空；这不表示路线无路口，不具备路口级开放核验依据。导航能算出路线不证明路口当前开放。','',
           '|站点|步速 km/h|时限 分钟|样本数|模型时限内|模型超时限|未知|','|---|---:|---:|---:|---:|---:|---:|']
    for station in dict.fromkeys(s['station'] for s in data['samples']):
        for speed in (4.5,3.5):
            for t in (10,15):
                rows=[r for r in data['scenarios'] if r['station']==station and r['speed_kmh']==speed and r['threshold_min']==t]
                c=Counter(r['model_status'] for r in rows)
                lines.append(f'|{station}|{speed}|{t}|{len(rows)}|{c["model_covered"]}|{c["model_not_covered"]}|{c["unknown"]}|')
    rate=data['quality']['valid_routes']/max(1,len(data['routes']))
    gates={'model_geometry_pass_rate':rate,'model_geometry_gate_pass':rate>=.95,
           'current_entrance_and_destination_access_verified':False,
           'expansion_of_verified_conclusions_allowed':False,
           'reason':'地图与导航检查只能提供模型证据；当前公众通行尚未确认，不将核验通过结论外推到其他站点。'}
    lines.extend(['','## 扩展判断','',f'几何有效率{rate:.1%}；技术门槛预设≥95%。公众通行门槛目前未通过，故本次不扩展“已核验可达性”的站点范围。先逐点完成优先清单中的入口开放、道路连接、地下通道和校区门禁核查；即使模型门槛通过，也不能替代通行核验。','',
        '下一批候选可考虑秋水广场、师大南路，作为办公/综合场景对照。新增站点需重新获取入口与目的地，沿用相同抽样、路线质量和未知状态规则；本轮尚未采集或计算这些站点。','',
        '## 成果','', '- [本地交互地图](步行接驳地图.html)：无需密钥或服务器，双击可打开；显示真实折线和目的地，支持时间、步速、站点切换。',
        '- `data/processed/access_samples.csv`：实际样本与选择依据。',
        '- `data/processed/sample_accessibility.csv`：4组参数情景下的逐样本结果。',
        '- `data/processed/route_passability_review.csv`：逐路线坐标、绕行、过街提示及待核验项。',
        '- `output/access_routes.json`、`access_results.json`：原始请求关联、折线、检查与情景。'])
    (ROOT/'output/三站步行接驳分析报告.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    (ROOT/'output/expansion_gate.json').write_text(json.dumps(gates,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(gates,ensure_ascii=True))


if __name__=='__main__': main()
