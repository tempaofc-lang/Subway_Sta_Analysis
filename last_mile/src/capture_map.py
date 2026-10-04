import subprocess
from audit_local import ROOT


def main():
    page=(ROOT/'output/步行接驳地图.html').as_uri()
    browser='C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
    for label,size in [('desktop','1440,1100'),('mobile','430,1100')]:
        args=[browser,'--headless=new','--disable-gpu','--no-first-run','--hide-scrollbars',
              '--user-data-dir='+str(ROOT/'output'/('browser_'+label)),
              '--screenshot='+str(ROOT/'output'/('map_'+label+'.png')),
              '--window-size='+size,'--virtual-time-budget=2500',page]
        r=subprocess.run(args,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=45,
                         creationflags=subprocess.CREATE_NO_WINDOW)
        print(label,'exit=',r.returncode,'screenshot_exists=',(ROOT/'output'/('map_'+label+'.png')).exists())


if __name__=='__main__': main()
