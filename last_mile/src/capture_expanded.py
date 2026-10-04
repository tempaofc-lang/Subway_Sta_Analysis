"""Verify online layers with intercepted tile fixtures and the offline map in installed Edge at real desktop/mobile viewports."""
import base64
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

from audit_local import ROOT


def main():
    page_path = ROOT / 'output/扩展站点步行接驳地图.html'
    data_path = ROOT / 'output/expanded_results.json'
    if not page_path.exists() or not data_path.exists():
        raise SystemExit('Build expanded_results.json and the expanded offline map first.')
    data = json.loads(data_path.read_text(encoding='utf-8'))
    stations = list(dict.fromkeys(row['station'] for row in data['samples']))
    sample_lookup = {row['sample_id']: row for row in data['samples']}

    def group_of(row):
        base = sample_lookup.get(row['sample_id'], {})
        value = str(row.get('commuter_group') or base.get('commuter_group') or '')
        if value in ('student', '学生通勤'):
            return 'student'
        if value in ('resident', '居民通勤'):
            return 'resident'
        return 'other'

    output = {}
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(
            executable_path='C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
            headless=True,
        )
        for name, width, height in [('desktop', 1440, 1100), ('mobile', 430, 1300)]:
            context = browser.new_context(viewport={'width': width, 'height': height}, device_scale_factor=1)
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            tile_fixture = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
            context.route('https://tile.openstreetmap.org/**', lambda route: route.fulfill(status=200, content_type='image/png', body=tile_fixture))
            context.route('https://map-test.example/**', lambda route: route.fulfill(status=200, content_type='text/html', body=page_path.read_text(encoding='utf-8')))
            page.goto('https://map-test.example/index.html', wait_until='load')
            checks = {'actual_viewport': page.evaluate('window.innerWidth') == width,
                      'station_options': page.locator('#station option').count() == len(stations),
                      'base_tiles_loaded_fixture': '已加载' in page.locator('#tile-status').inner_text(),
                      'tile_point_alignment': page.evaluate(r'''(()=>{const tile=document.querySelector('#layer-base image'),point=document.querySelector('.map-point');if(!tile||!point)return false;const parts=tile.getAttribute('href').match(/\/(\d+)\/(\d+)\/(\d+)\.png$/),span=2*Math.PI*6378137/2**(+parts[1]),origin=[+parts[2]*span-Math.PI*6378137,Math.PI*6378137-(+parts[3])*span],sample=DATA.scenarios.find(s=>s.sample_id===point.dataset.sample),world=mercator([sample.lng,sample.lat]),ratio=(+tile.getAttribute('width')-.15)/span,m=point.transform.baseVal.getItem(0).matrix;return Math.abs(m.e-(+tile.getAttribute('x')+(world[0]-origin[0])*ratio))<1e-4&&Math.abs(m.f-(+tile.getAttribute('y')-(world[1]-origin[1])*ratio))<1e-4})()'''),
                      'tile_geometry': page.evaluate("[...document.querySelectorAll('#layer-base image')].every(n=>+n.getAttribute('width')>0 && n.getAttribute('preserveAspectRatio')==='none')"),
                      'conversion_roundtrip': page.evaluate("DATA.samples.every(s=>{const q=displayWgs([s.lng,s.lat]),d=gcjOffset(...q);return Math.abs(q[0]+d[0]-s.lng)<1e-7&&Math.abs(q[1]+d[1]-s.lat)<1e-7})"),
                      'known_conversion_reference': page.evaluate("(()=>{const p=displayWgs([116.403372494,39.91793075]);return Math.abs(p[0]-116.397128)<1e-5&&Math.abs(p[1]-39.916527)<1e-5})()")}
            counts_ok = True
            for station in stations:
                page.select_option('#station', station)
                for speed in ('4.5', '3.5'):
                    page.select_option('#speed', speed)
                    for threshold in ('10', '15'):
                        page.select_option('#threshold', threshold)
                        for group in ('all', 'student', 'resident', 'other'):
                            page.select_option('#group', group)
                            rows = [row for row in data['scenarios'] if row['station'] == station
                                    and float(row['speed_kmh']) == float(speed)
                                    and float(row['threshold_min']) == float(threshold)
                                    and (group == 'all' or group_of(row) == group)]
                            counts_ok &= page.locator('#rows button').count() == len(rows)
                            counts_ok &= page.locator('#stats').inner_text().startswith(f'{len(rows)} 个样本')
            checks['all_station_parameter_group_counts'] = counts_ok
            student_row = next((row for row in data['scenarios'] if group_of(row) == 'student'), None)
            if student_row:
                page.select_option('#station', student_row['station'])
                page.select_option('#speed', str(student_row['speed_kmh']))
                page.select_option('#threshold', str(int(student_row['threshold_min'])))
                page.select_option('#group', 'student')
                page.locator('#rows button').first.click()
                checks['student_origin_copy'] = '学生出发点' in page.locator('#detail').inner_text()
                checks['selected_button'] = page.locator('#rows button[aria-pressed="true"]').count() == 1
                checks['student_diamond'] = page.locator('.map-point path.point-shape').count() > 0
                point = page.locator('.map-point').first
                point.focus()
                point.press('Space')
                checks['keyboard_select_and_focus'] = page.evaluate(
                    "document.activeElement?.classList.contains('map-point')") and page.locator('#detail h2').count() == 1
                page.select_option('#group', 'all')
                checks['filter_clears_detail'] = page.locator('#detail h2').count() == 0
                checks['filter_clears_selection'] = page.locator('[aria-pressed="true"]').count() == 0
            page.select_option('#station', stations[0])
            page.select_option('#group', 'all')
            page.select_option('#speed', '4.5')
            page.select_option('#threshold', '10')
            checks['no_horizontal_overflow'] = page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            for layer, control in [('layer-grid','show-grid'), ('layer-routes','show-routes'), ('layer-points','show-points')]:
                page.locator('#'+control).uncheck()
                checks[layer+'_hidden'] = page.locator('#'+layer).evaluate("n=>getComputedStyle(n).display==='none'")
                page.locator('#'+control).check()
                checks[layer+'_shown'] = page.locator('#'+layer).evaluate("n=>getComputedStyle(n).display!=='none'")
            context.unroute('https://tile.openstreetmap.org/**')
            context.route('https://tile.openstreetmap.org/**', lambda route: route.abort())
            page.locator('#show-base').uncheck()
            page.locator('#show-base').check()
            page.wait_for_function("document.getElementById('tile-status').textContent.includes('未加载')")
            checks['tile_failure_preserves_routes'] = page.locator('#layer-routes polyline').count() > 0 and '仍可用' in page.locator('#tile-status').inner_text()
            checks['no_script_errors'] = not errors
            offline = context.new_page()
            offline.goto(page_path.as_uri(), wait_until='load')
            checks['offline_no_requests'] = offline.evaluate("performance.getEntriesByType('resource').length") == 0
            checks['offline_base_disabled'] = offline.locator('#show-base').is_disabled() and offline.locator('#layer-routes polyline').count() > 0
            offline.close()
            page.screenshot(path=str(ROOT / f'output/expanded_map_{name}.png'), full_page=False)
            output[name] = {'viewport': page.evaluate('({width:innerWidth,height:innerHeight})'),
                            'checks': checks, 'errors': errors}
            context.close()
        browser.close()
    (ROOT / 'output/expanded_browser_validation.json').write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(output, ensure_ascii=True))
    assert all(all(value['checks'].values()) for value in output.values())


if __name__ == '__main__':
    main()
