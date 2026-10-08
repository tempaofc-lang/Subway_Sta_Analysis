# 南昌地铁站周边步行可达性分析

以地铁出入口为终点，比较居民、学生和其他目的地的步行接驳情景。当前快照包含 **109 个有分析数据站点、711 个样本、2,745 条路线**，支持站点、通勤组、步速和时限筛选。

全网站点目录包含113个去重站点，支持1—4号线筛选及换乘站多线路归属。全网数据分批导入；待采集、缺少入口、无合格样本和部分路线待核查分别标注，不把缺数据视为不可达。当前数字对应第四批发布快照：113站均已完成首轮采集尝试，109站有分析数据、4站暂无合格样本，无待采集站。额外补查留待后续决定。

## 本地构建与预览

在本仓库根目录执行，Python 3.10 及以上即可构建展示页面，不需要 API 密钥或第三方 Python 包：

```powershell
python -B last_mile/src/build_site.py
python -B last_mile/src/check_site.py
python -m http.server 8000 --directory _site
```

打开 http://localhost:8000 。页面发布及持续更新说明见 [DEPLOYMENT.md](DEPLOYMENT.md)。GitHub Actions 在推送 `main` 时构建并发布 GitHub Pages，也支持手动触发。

## 持续更新接口

- `last_mile/output/expanded_results.json`：页面构建的数据源；保留原始 GCJ-02 坐标和分析字段。
- `last_mile/web/access_template.html`：交互界面模板。
- `api/v1/manifest.json`、`api/v1/results.json`、`api/v1/stations.json`：网站发布的版本化、只读 JSON 接口。GitHub Pages 不运行采集后端。
- 数据更新后运行构建检查，再提交推送即可更新页面。API 调用在本地或另外授权的后端执行，不在访客浏览器中放置 Web 服务密钥。

## 分析口径

4.5/3.5 km/h，10/15 分钟，选定地铁入口中的最短路线；暂不考虑门禁和临时封路。大学校门代理点单列学生通勤，不包含宿舍至校门的校内距离。样本比例不能外推到全站人口覆盖率，路径未知也不等于不可达。地图中的参考底图只辅助空间定位，不能作为现场通行证明。

详见 [分析假设](last_mile/ANALYSIS_ASSUMPTIONS.md)、[分析报告](last_mile/output/扩展站点步行接驳分析报告.md) 和 [接续记录](last_mile/PROJECT_STATE.md)。

## 采集与复现边界

展示构建只依赖仓库内的派生成果；完整采集及历史审计另需本地旧输入与原始 API 快照。`python practice project/`、虚拟环境、原始接口缓存和含密钥的配置不公开上传。

继续采集可安装 `python -m pip install -r requirements.txt`，并通过 `AMAP_WEB_SERVICE_KEY` 环境变量或 `last_mile/.env` 配置凭证；既有本机旧配置读取兼容保留。所有采集命令应显式核对调用预算，具体入口见 [研究目录说明](last_mile/README.md)。
