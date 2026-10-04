"""Build a whitelist-only, credential-free static GitHub Pages artifact."""
import hashlib
import json
from pathlib import Path
import shutil
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
SITE = ROOT / '_site'
CSV_NAMES = ('expanded_samples', 'expanded_entrances', 'expanded_accessibility', 'expanded_group_summary')
ASSET_EXTENSIONS = {'.js', '.css', '.json', '.png', '.jpg', '.jpeg', '.svg', '.webp', '.woff', '.woff2', '.txt'}

def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

def main():
    from check_site import validate_data, scan_public
    source = ROOT / 'last_mile/output/expanded_results.json'
    raw = source.read_bytes()
    data = json.loads(raw)
    validate_data(data)
    template = (ROOT / 'last_mile/web/access_template.html').read_text(encoding='utf-8')
    if template.count('__DATA__') != 1:
        raise ValueError('Template must contain exactly one __DATA__ placeholder')
    # Only remove the fixed artifact directory, never a caller-provided path.
    if SITE.is_symlink() or SITE.resolve().parent != ROOT.resolve():
        raise ValueError('Unsafe artifact directory')
    if SITE.exists():
        shutil.rmtree(SITE)
    SITE.mkdir()
    embedded = json.dumps(data, ensure_ascii=False).replace('<', '\\u003c').replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')
    page = template.replace('__DATA__', embedded)
    footer = '<footer style="padding:18px 24px;background:#f3f5f4;color:#253a34;font:14px/1.8 system-ui" aria-label="公开数据下载"><strong>数据与接口</strong> · <a href="downloads/analysis-report.md">分析报告</a> · <a href="downloads/expanded_samples.csv">样本 CSV</a> · <a href="downloads/expanded_accessibility.csv">可达性 CSV</a> · <a href="api/v1/manifest.json">只读 API v1</a></footer>'
    page = page.replace('</body>', footer + '</body>')
    (SITE / 'index.html').write_text(page, encoding='utf-8')
    (SITE / '.nojekyll').touch()
    web = ROOT / 'last_mile/web'
    for path in web.rglob('*'):
        if path.is_file() and path.suffix.lower() in ASSET_EXTENSIONS:
            if path.is_symlink() or any(part.startswith('.') for part in path.relative_to(web).parts):
                raise ValueError('Hidden or linked web asset is not publishable')
            target = SITE / path.relative_to(web)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
    api = SITE / 'api/v1'
    dump(api / 'results.json', data)
    stations = []
    for name in sorted({s['station'] for s in data['samples']}):
        station_entrances = [e for e in data['entrances'] if e['station'] == name]
        stations.append({'station': name, 'station_id': station_entrances[0]['station_id'],
                         'sample_count': sum(s['station'] == name for s in data['samples']),
                         'entrance_count': len(station_entrances),
                         'route_count': sum(r['station'] == name for r in data['routes']),
                         'summary': [r for r in data['station_summary'] if r['station'] == name]})
    dump(api / 'stations.json', {'schema_version': '1.0.0', 'stations': stations})
    downloads = SITE / 'downloads'
    downloads.mkdir()
    for name in CSV_NAMES:
        shutil.copyfile(ROOT / ('last_mile/data/processed/' + name + '.csv'), downloads / (name + '.csv'))
    report = (ROOT / 'last_mile/output/扩展站点步行接驳分析报告.md').read_text(encoding='utf-8')
    report = report.replace('(扩展站点步行接驳地图.html)', '(../index.html)')
    (downloads / 'analysis-report.md').write_text(report, encoding='utf-8')
    dump(api / 'manifest.json', {
        'schema_version': '1.0.0', 'api_kind': 'static_read_only',
        'coordinate_system': 'GCJ-02',
        'data_updated_at_utc': data.get('updated_at_utc'),
        'data_updated_at_meaning': 'Timestamp stored by the analysis pipeline; not a collection timestamp for every record.',
        'built_at_utc': datetime.now(timezone.utc).isoformat(),
        'source_sha256': hashlib.sha256(raw).hexdigest(),
        'counts': {'stations': len(stations), 'samples': len(data['samples']), 'entrances': len(data['entrances']), 'routes': len(data['routes'])},
        'endpoints': {'results': 'results.json', 'stations': 'stations.json'},
        'downloads': ['../../downloads/' + name + '.csv' for name in CSV_NAMES] + ['../../downloads/analysis-report.md'],
        'scenario_assumption': data['scenario_assumption'],
        'physical_passability_verified': data['quality'].get('physical_passability_verified', False),
        'compatibility': 'v1 retains existing result field meanings; additive fields allowed; breaking changes use a new version path.'
    })
    scan_public(SITE)
    print('Built _site: %s stations, %s samples, %s routes' % (len(stations), len(data['samples']), len(data['routes'])))

if __name__ == '__main__':
    main()
