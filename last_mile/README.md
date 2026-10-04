# 南昌地铁步行接驳研究

本目录承接旧实习项目，只保存新项目代码与派生成果；不改动旧输入。公开网站构建及版本化数据接口见仓库根目录 README.md 和 DEPLOYMENT.md。

当前分析口径见 [分析假设](ANALYSIS_ASSUMPTIONS.md)：按用户要求暂不考虑门禁和临时封路，大学校门单列学生通勤组。此前 [本地审计与入口核验报告](output/本地审计与入口核验报告.md) 和三站报告保留作为历史快照。模型结果以选定入口为条件，样本比例不代表全站人口覆盖率。

## 本机复现

在迁移后的 `Subway_Sta_Analysis` 仓库根目录运行（Python 需安装 requirements.txt；完整采集及审计需要本地旧输入和原始缓存）：

```powershell
python -B last_mile/src/audit_local.py
# 以下两项会实际请求API并消耗调用配额；每次运行会重新采集。
python -B last_mile/src/collect_entrances.py --budget 20
python -B last_mile/src/probe_walking.py
# 可选复采：最多60次请求；校区/站点详情最多4次请求。
python -B last_mile/src/refresh_pilot_pois.py
python -B last_mile/src/probe_poi_details.py
python -B last_mile/src/prepare_destination_review.py
python -B last_mile/src/build_audit_report.py
```

密钥读取顺序：`AMAP_WEB_SERVICE_KEY`环境变量 → 本目录`.env`同名配置 → 旧项目config.py中的本地AMAP_KEY字面值。只读取该值，不执行旧配置文件。无需在聊天提供密钥。新密钥可在本目录`.env`中配置，本目录已忽略该文件。

入口查询使用150501独立类别，避免与父类150500混合检索导致子POI聚合。API周边距离和关键词匹配仅用于收集候选；以parent关联、编号与外部证据核查归属。原始请求参数不含密钥，网络异常只记录异常类型。快照位于`data/raw/`，本目录已忽略它。

`data/processed/`中所有坐标为高德来源，按GCJ-02管理。旧输入的坐标系为来源推断，尚未通过控制点独立确认；新API数据为GCJ-02。不能直接叠加WGS84底图。CSV表的`poi_center_proxy`表示目的地中心代理点，需要真实入口复核。

`matches_reported_count`只表示明细与该次API报告数量一致，不代表现实完整。末页空响应count=0不参与非空页数量一致性判断。旧缓存缺少采集日期。POI重复不等于多个独立校区/建筑；按校园、建筑或小区合并后再抽样。

报告脚本使用本轮23个入口和3条验证路线作结果完整性断言；今后扩展站点时应相应调整验收范围。报告中的桌面证据必须随入口状态变化重新核验。

## 三站步行接驳成果

[本地地图](output/步行接驳地图.html)可直接双击打开，无需密钥或服务器。[分析报告](output/三站步行接驳分析报告.md)记录24个样本、142条路线、参数情景和扩展门槛。

```powershell
python -B last_mile/src/analyze_access.py samples
# 最多170次新请求；相同成功请求会使用本地快照续算。
python -B last_mile/src/analyze_access.py collect
python -B last_mile/src/analyze_access.py summarize
python -B last_mile/src/build_access_outputs.py
python -B last_mile/src/check_access_results.py
```

原三站阶段仅通过模型几何检查，尚无现场通行确认。最新版本依据用户明确授权，在暂不考虑门禁和临时封路的情景下扩展；这不等于确认实际通行状态。地图有教育/医疗POI案例，其中包括托育、诊所，不能当作独立学校或综合医院统计。

## 扩展版本与代理分区

扩展目标为原三站加秋水广场、师大南路、学府大道东，大学校门作为学生通勤代表点单列。学生样本只计算校门至地铁口，不包括校内宿舍至校门距离。

- 采集：`src/expand_samples.py`，输出`expanded_samples.csv`与`expanded_entrances.csv`。
- 路线与验证：`src/analyze_expanded.py`、`src/check_expanded.py`，复用旧路线快照。
- 地图模板：`web/access_template.html`，展示站点、通勤组和阈值筛选。
- 主智能体汇总：`src/build_expanded_outputs.py`，生成[扩展地图](output/扩展站点步行接驳地图.html)与[扩展报告](output/扩展站点步行接驳分析报告.md)。

[PROJECT_STATE.md](PROJECT_STATE.md)记录文件所有权、已完成结果和接续动作，可用于上下文压缩后的恢复。先运行采集，再运行路线计算及检查，最后运行汇总脚本。各脚本按其命令行参数控制网络请求；不要重复全量采集。
