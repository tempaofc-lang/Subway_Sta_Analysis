"""Headless, offline-safe network UI regression checks; optional --page for real output."""
import argparse
import html
import json
import re
import subprocess
import tempfile
from playwright.sync_api import sync_playwright
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def fixture():
    sample = dict(station='换乘站', sample_id='one', name='测试起点', lng=115.87, lat=28.68, commuter_group='resident')
    route = dict(station='换乘站', request_id='r1', polyline=[[115.87,28.68],[115.871,28.681]], steps=[])
    scenarios = [dict(sample, speed_kmh=speed, threshold_min=limit, model_status='model_covered', best_time_min=2, best_request_id='r1') for speed in [4.5,3.5] for limit in [10,15]]
    catalog = [dict(station='换乘站',lines=['1号线','2号线'],analysis_status='analyzed',lng=115.87,lat=28.68)]
    for i,status in enumerate(['pending','no_entrances','no_samples','needs_review','failed']):
        catalog.append(dict(station='状态'+str(i),lines=['3号线'],analysis_status=status,lng=115.88,lat=28.69))
    review_sample=dict(sample,station='有数据待核查站',sample_id='review',point_basis='university_gate_poi_parent_matched',commuter_group='student')
    catalog.append(dict(station='有数据待核查站',lines=['3号线'],analysis_status='needs_review',lng=115.87,lat=28.68))
    scenarios += [dict(review_sample,speed_kmh=4.5,threshold_min=10,model_status='unknown',best_time_min='',best_request_id='')]
    return dict(samples=[sample,review_sample],routes=[route],scenarios=scenarios,entrances=[],station_catalog=catalog)

CHECKS = r'''
<script>
const checks={};
const change=(id,value)=>{document.getElementById(id).value=value;document.getElementById(id).dispatchEvent(new Event('change'))};
const options=()=>[...document.querySelectorAll('#station option')].map(o=>o.value);
checks.line_control=!!document.getElementById('line');
checks.catalog_deduplicated=options().length===new Set(DATA.station_catalog.map(s=>s.station)).size;
checks.progress=document.getElementById('scope').textContent.includes('目录');
for(const line of document.querySelectorAll('#line option')){
 change('line',line.value);
 const expected=new Set(DATA.station_catalog.filter(s=>line.value==='all'||s.lines.includes(line.value)).map(s=>s.station));
 checks['membership_'+line.value]=options().length===expected.size&&options().every(name=>expected.has(name));
}
change('line','all');
const analyzed=DATA.station_catalog.find(s=>s.analysis_status==='analyzed'&&DATA.samples.some(x=>x.station===s.station));
if(analyzed){change('station',analyzed.station);document.querySelector('#rows button')?.click();checks.detail_selected=!!document.querySelector('#detail h2');change('speed','3.5');checks.detail_reset=!document.querySelector('#detail h2');change('speed','4.5')}
for(const station of DATA.station_catalog.filter(s=>s.analysis_status!=='analyzed'&&!(s.analysis_status==='needs_review'&&DATA.scenarios.some(x=>x.station===s.station)))){
 change('station',station.station);
 checks['empty_'+station.station+'_'+station.analysis_status]=!document.querySelector('#rows button')&&!document.querySelector('#layer-routes polyline')&&!document.querySelector('#detail h2')&&document.getElementById('stats').textContent.includes('未分析不代表步行不可达')&&!document.getElementById('stats').textContent.includes('0 个');
}
for(const station of DATA.station_catalog.filter(s=>s.analysis_status==='needs_review'&&DATA.scenarios.some(x=>x.station===s.station))){
 change('station',station.station);change('group','all');change('speed','4.5');change('threshold','10');
 checks['review_data_visible_'+station.station]=!!document.querySelector('#rows button')&&document.getElementById('stats').textContent.includes('个样本');
 const gate=DATA.samples.find(s=>s.station===station.station&&s.point_basis==='university_gate_poi_parent_matched');
 if(gate){document.querySelector(`[data-sample="${gate.sample_id}"][data-surface="table"]`)?.click();checks['gate_basis_'+station.station]=document.getElementById('detail').textContent.includes('未现场核验')&&!document.getElementById('detail').textContent.includes('未确认独立门')}
}
change('line','all');
checks.no_horizontal_overflow=document.documentElement.scrollWidth<=innerWidth;
const out=document.createElement('pre');out.id='network-qa';out.textContent=JSON.stringify(checks);document.body.append(out);
</script>'''

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--page',type=Path)
    parser.add_argument('--browser',default='C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')
    args=parser.parse_args()
    source=args.page.read_text(encoding='utf-8') if args.page else (ROOT/'web/access_template.html').read_text(encoding='utf-8').replace('__DATA__',json.dumps(fixture(),ensure_ascii=False))
    results={}
    with tempfile.TemporaryDirectory(prefix='network-ui-') as folder:
        temp=Path(folder)
        target=temp/'test.html'
        target.write_text(source.replace('</body>',CHECKS+'</body>'),encoding='utf-8')
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch(executable_path=args.browser,headless=True)
            for name,width in [('desktop',1440),('mobile',430)]:
                page=browser.new_page(viewport={'width':width,'height':1100})
                errors=[]
                page.on('pageerror',lambda error: errors.append(str(error)))
                page.goto(target.as_uri())
                page.wait_for_selector('#network-qa')
                results[name]=json.loads(page.locator('#network-qa').text_content())
                results[name]['no_script_errors']=not errors
                page.close()
            browser.close()
    print(json.dumps(results,ensure_ascii=False,indent=2))
    assert all(all(checks.values()) for checks in results.values()),'Network UI regression failed'

if __name__=='__main__':
    main()
