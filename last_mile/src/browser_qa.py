import html
import json
import re
import subprocess
from audit_local import ROOT


def main():
    page=(ROOT/'output/步行接驳地图.html').read_text(encoding='utf-8')
    checks='''<script>
let checks={};checks.initial_rows=document.querySelectorAll('#rows tr').length===10;
document.getElementById('station').value='瑶湖西';document.getElementById('station').dispatchEvent(new Event('change'));
checks.station_switch=document.querySelectorAll('#rows tr').length===4;
document.getElementById('threshold').value='15';document.getElementById('threshold').dispatchEvent(new Event('change'));
checks.threshold_switch=document.getElementById('stats').textContent.includes('4 个样本');
document.querySelector('#rows button').click();checks.destination_detail=document.getElementById('detail').querySelector('h2')!==null;
document.getElementById('speed').value='3.5';document.getElementById('speed').dispatchEvent(new Event('change'));
checks.speed_switch=document.getElementById('detail').textContent.includes('选择目的地');
checks.no_horizontal_overflow=document.documentElement.scrollWidth<=window.innerWidth;
let result=document.createElement('pre');result.id='qa-result';result.textContent=JSON.stringify(checks);document.body.append(result);
</script>'''
    target=ROOT/'output/browser_qa.html'
    target.write_text(page.replace('</html>',checks+'</html>'),encoding='utf-8')
    results={}
    for name,size in [('desktop','1440,1100'),('mobile','430,1100')]:
        args=['C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe','--headless=new','--disable-gpu',
              '--no-first-run','--user-data-dir='+str(ROOT/'output'/('qa_profile_'+name)),
              '--dump-dom','--virtual-time-budget=2000','--window-size='+size,target.as_uri()]
        r=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=40,creationflags=subprocess.CREATE_NO_WINDOW)
        match=re.search(r'<pre id="qa-result">(.*?)</pre>',r.stdout.decode('utf-8',errors='replace'),re.S)
        results[name]=json.loads(html.unescape(match.group(1))) if match else {'script_executed':False}
    (ROOT/'output/browser_validation.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
    print(json.dumps(results))
    assert all(all(checks.values()) for checks in results.values())


if __name__=='__main__': main()
