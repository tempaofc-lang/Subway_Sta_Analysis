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
- `api/v1/stations.json`：`schema_version` 与 `stations` 数组；各站包括名称、站点 ID、样本/出入口/路线计数及分情景汇总。
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
