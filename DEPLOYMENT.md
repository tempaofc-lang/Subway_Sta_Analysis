# GitHub Pages 发布与持续更新

本项目使用 GitHub Actions 构建静态页面。仓库 Settings → Pages → Source 选择 **GitHub Actions**；推送 `main` 或手动运行 Deploy analysis to GitHub Pages 工作流即可更新。无需部署密钥、地图采集 API 密钥或原始请求缓存。

## 本地构建与检查

在仓库根目录运行（Python 3.12，仅标准库）：

```sh
python last_mile/src/build_site.py
python last_mile/src/check_site.py
python -m http.server 8000 --directory _site
```

访问 http://localhost:8000 。构建会清空并重新生成固定的 `_site` 目录。页面内嵌结果数据，直接打开 HTML 也能显示分析；网络底图是否可用取决于网络与底图服务，数据分析不依赖底图请求。

## 公开只读接口 v1

以下路径均相对于站点根目录；项目型 Pages 需保留仓库名前缀。客户端应使用相对路径，避免写死部署域名。

- `api/v1/manifest.json`：接口版本、坐标系、数据更新时间、构建时间、源文件 SHA-256、计数和下载索引。
- `api/v1/results.json`：完整分析结果，保持 `expanded_results.json` 的已有字段结构。
- `api/v1/stations.json`：完整站点目录，含多线路归属、分析状态、样本/出入口/路线计数与情景汇总；无样本站点汇总为空，不表示不可达。
- `api/v1/lines.json`：各线路所属站点ID、目录站位数和有分析数据站位数；换乘站可属多线，全网总数去重。
- `downloads/expanded_samples.csv`、`expanded_entrances.csv`、`expanded_accessibility.csv`、`expanded_group_summary.csv`：选定分析表。
- `downloads/analysis-report.md`：扩展站点分析报告。

这是静态 JSON 只读接口，没有写入、动态查询或服务端密钥代理功能。调用者自行筛选 JSON。v1 可增加字段；破坏性变化通过新版本目录提供，不改变 v1 既有语义。更新时用 manifest 的 `source_sha256` 判断分析数据是否变化。`data_updated_at_utc` 来自分析文件的 `updated_at_utc`，不是全部记录的采集时间；`built_at_utc` 仅表示构建时间。记录级采集时间保留在各记录中。

原始坐标为 GCJ-02。外部接入其他坐标系时应转换展示坐标；不得直接将 GCJ-02 当作 WGS84。路线是导航模型结果，门禁、临时封路及公众实际通行情况未实地验证。

## 更新流程与公开范围

1. 在私有本地环境运行采集和分析，复核 `last_mile/output/expanded_results.json`、四张 CSV 及报告。
2. 构建和检查通过后提交更新；不要提交 `.env`、凭据、虚拟环境、原始接口缓存或无关练习目录。
3. 推送 `main`，确认 Actions 的 build 和 deploy 成功，再检查线上页面及 manifest。

构建只发布结果 JSON、地图模板生成的 HTML、上述五个下载文件及 `last_mile/web` 下受扩展名限制的资源。HTML 模板必须保留唯一 `__DATA__` 占位符。验证检查 ID 唯一性、引用完整性、计数、内嵌数据与 API 一致性，并扫描常见凭据与本机绝对路径；该扫描不替代人工公开内容复核。新增 web 资源会进入公开页面，应先检查许可和内容。

工作流遵循 GitHub 官方自定义 Pages 工作流：
https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages


## 全网分批扩展（本机）

目录为113个去重站点，来源与核验边界见 `last_mile/NETWORK_SCOPE.md`。API `manifest.counts.stations` 继续表示有样本分析的站点数量；`catalog_station_count` 表示完整目录规模。待采集/无入口/无样本与不可达是不同状态。

在本仓库根目录，先查看计划，再执行本批；采集需要本地API凭证及历史缓存，不能在Pages构建中运行：

```powershell
python -B last_mile/src/network_expand.py plan --batch 1 --size 26
python -B last_mile/src/network_expand.py collect --batch 1 --size 26 --budget 1500 --global-budget 6000
python -B last_mile/src/network_check.py
python -B last_mile/src/build_expanded_outputs.py
python -B last_mile/src/build_site.py
python -B last_mile/src/check_site.py
```

后续轮次依次使用新的 `--batch` 编号。同一批续跑固定原站点列表，`--budget` 为本批累计上限，不因重启清零；全任务累计不超过6000次（失败尝试也计入）。可用 `--line 1号线` 限定选站，批次中不能并行启动多个采集进程。`--retry-failed` 是明确的失败重试入口，不自动重复全部失败站；`--retry-missing` 补查无入口站，`--retry-empty` 补查无合格样本站。补查复用缓存，并计入累计预算，已完成站不会自动重采。

限额、API拒绝或连续网络故障会停止采集。此时可运行 `network_expand.py publish` 导出已完成站，再运行独立检查；未完成站继续以状态显示。断点、原始响应、预算账本与基线快照均只保存在本地忽略目录，不公开上传。

每批验证原9站69样本/312路线保持不变；新增异常几何及缺路径保留未知，不删除异常记录来提高通过率。按线路选择是案例分组，不将样本统计解读为线路人口覆盖率。
