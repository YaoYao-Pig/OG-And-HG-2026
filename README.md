# OpenGalaxy Graphia 数据导出

这个工具把 ClickHouse 中最近 12 个完整月的数据转换成 Graphia 可直接打开的仓库协作网络。大规模筛选、聚合、二部图投影和边裁剪全部在 ClickHouse 内完成，本地只接收最终节点和边，绝不下载 `events` 明细。

## Web 双星图

仓库同时包含一套可交互的科学图版式 Web Atlas：

- **OpenGalaxy**：19,771 个 GitHub 仓库、106,650 条协作关系，可按语言、AI 子领域和总技术领域观察。
- **HubGalaxy**：从 [`cfahlgren1/hub-stats`](https://huggingface.co/datasets/cfahlgren1/hub-stats) 每日快照抽取的模型、数据集与 Space 生态。
- **自动发布**：[GitHub Actions workflow](.github/workflows/pages.yml) 在 `main` 更新、手动触发及每日计划任务时构建 GitHub Pages。

前端开发、数据重建与首次 Pages 设置见 [visualization/README.md](visualization/README.md)。

## 仓库内的可直接使用成品

默认 Preview 与 Web 使用的近两万节点 Final 均已生成并完成完整性校验，周期为 2025-08 至 2026-07：

- [Final QA](artifacts/open-galaxy-github-202508-202607-final/qa.json) / [Final manifest](artifacts/open-galaxy-github-202508-202607-final/manifest.json)：19,771 个仓库节点、106,650 条边，供 Web 星图使用。

- [graph.graphml](artifacts/open-galaxy-github-202508-202607-preview/graph.graphml)：Graphia 直接打开，包含 4,243 个仓库节点和 46,173 条边。
- [qa.json](artifacts/open-galaxy-github-202508-202607-preview/qa.json)：月份、重复率、节点、边和端点 QA，状态为 `pass`。
- [manifest.json](artifacts/open-galaxy-github-202508-202607-preview/manifest.json)：参数、语义、执行耗时与全部文件 SHA-256。
- [nodes.csv](artifacts/open-galaxy-github-202508-202607-preview/nodes.csv) / [edges.csv](artifacts/open-galaxy-github-202508-202607-preview/edges.csv)：需要自定义导入时使用。
- [queries](artifacts/open-galaxy-github-202508-202607-preview/queries/)：本次实际执行的三份 SQL。

连接地址、用户名和密码均未写入仓库或产物。

## 默认口径

- 节点：GitHub 仓库。
- 候选仓库：按 12 个月 `global_openrank` 累计值选 Top-K；排除已知 Fork，缺少元数据的仓库保留并在 QA 中标记。
- 边：同一个已知非 Bot 账号对两个候选仓库都有归一化贡献。
- 来源：`normalized_community_openrank`；它已过滤数据源能够识别的 Bot，但不能保证所有自动账号都被识别。
- 月度去重：`global_openrank` 按 `(repo_id, month)`、归一化贡献表按 `(repo_id, actor_id, month)` 取 `max(openrank)`，避免源表多版本记录被重复计权；原始重复率和“同键不同值”冲突键数写入 QA，任一来源重复率超过 1% 时中止。
- 超级账号过滤：先在全年所有仓库中计算贡献者 degree，只保留 degree 2～30，再连接 Top-K，Preview/Final 的账号口径一致。
- 边阈值：至少 2 位共享贡献者。
- 边权 `weight`：每个合格贡献者在其全年所有仓库对之间总共分配 1 单位质量，减少高 degree 账号的影响。
- `collaboration_strength`：`Σ actor_weight × sqrt(contribution_a × contribution_b)`，额外体现双方贡献强度。
- 硬裁剪：每个节点最多提名最强的 N 条边，再取双向提名的并集，避免未来数据增长导致边爆炸。
- 输出节点：只保留裁剪后至少连接一条边的候选仓库，因此节点数通常小于 Top-K。

预设：

| 参数 | `preview` | `final` |
|---|---:|---:|
| 候选仓库 | 5,000 | 20,000 |
| 每节点提名边数 | 30 | 20 |
| 标签数量 | 150 | 300 |
| 最多返回边 | 200,000 | 500,000 |

## 运行要求

- Python 3.10 或更新版本，无第三方包依赖。
- ClickHouse HTTP 接口可访问。
- 账号只需对 `opensource.*` 有 `SELECT` 权限；每个请求还会强制设置 `readonly=1`。

不要把密码写进源码、命令参数或版本库。PowerShell 中为当前进程设置环境变量：

```powershell
$env:CLICKHOUSE_HOST = 'https://your-clickhouse-endpoint:8443'
$env:CLICKHOUSE_USERNAME = 'readonly_user'
$env:CLICKHOUSE_PASSWORD = 'replace_me'
$env:CLICKHOUSE_DATABASE = 'opensource'
```

优先使用 HTTPS。若服务端当前只开放明文 HTTP，必须显式确认 Basic Auth 未加密的风险：

```powershell
python .\export_graph.py --preset preview --allow-insecure-http
```

HTTPS 正常时：

```powershell
python .\export_graph.py --preset preview
python .\export_graph.py --preset final
```

脚本默认选择“两张 OpenRank 表共同拥有的最新月份”与“服务端当前月份的上一个月”中的较早者，并验证连续 12 个月在两张表中都存在。固定周期和输出目录：

```powershell
$exportArgs = @(
  '--preset', 'final',
  '--end-month', '202607',
  '--output-dir', 'D:\graph-output\open-galaxy-2026'
)
python .\export_graph.py @exportArgs
```

输出目录已存在时默认拒绝覆盖。`--force` 只接受工具自己的已知文件，先在同级唯一 staging 目录完成全部查询、QA 和哈希，再整体发布；中途失败会保留旧快照。

## 输出

```text
output/open_galaxy_github_202508_202607_preview/
├── edges.csv
├── nodes.csv
├── graph.graphml
├── manifest.json
├── qa.json
└── queries/
    ├── edges.sql
    ├── nodes.sql
    └── source_qa.sql
```

- `graph.graphml`：节点、边和全部属性已经合并；Graphia 最快的单文件入口。
- `edges.csv`：`source,target,weight,shared_contributors,collaboration_strength`。
- `nodes.csv`：包含 `name`、只为头部仓库赋值的 `display_label`、OpenRank、贡献者数、语言、Topics、简介和 URL。
- `manifest.json`：周期、参数、语义、执行耗时和文件 SHA-256；不含连接地址、用户名或密码。
- `qa.json`：月份连续性、来源键重复率与去重策略、重复边、自环、数值合法性、端点和元数据覆盖。
- `queries/*.sql`：本次实际执行的 SQL，可审计和复现。

## Graphia 最快出图流程

1. 选择 **File → Open**，直接加载 `graph.graphml`。
2. 添加 **Weighted Louvain Cluster** transform，Weighting Attribute 选择 `weight`。
   - 社区太碎：降低 Granularity。
   - 社区过大：提高 Granularity。
3. 如果仍觉得太密，可再添加 **Edge Reduction → k-NN**：Ranked Attribute 选 `weight`、Descending、`k=15～30`。
4. 推荐视觉映射：
   - 节点颜色：`Weighted Louvain Cluster`
   - 节点大小：`openrank_sum`，通过 **Custom Mapping…** 把 exponent 从 `0.35` 左右开始调整
   - 边宽：优先 `collaboration_strength`；通过 **Custom Mapping…** 使用约 `0.25～0.5` exponent
   - 文字：`display_label`，它只包含头部 150/300 个仓库，避免全图标签爆炸
5. 使用 2D、深色背景、较低边透明度；布局稳定后暂停，通过 **File → Save As Image…** 导出高分辨率图片。

如果不想使用 GraphML，可以手动导入 CSV：

1. **File → Open** 加载 `edges.csv`，在 Pairwise Parameters 中务必勾选 **Treat First Row as Header**。
2. 映射 `source` → Source Node、`target` → Target Node；其余三列作为 Edge Attribute。
3. **Tools → Import Attributes From Table…** 加载 `nodes.csv`，明确选择 Graphia 的 **Node Name** ↔ CSV 的 `id`。

若希望进一步降低边数，优先把 `--min-shared` 提到 3；不要取消全年 degree 上限。

## 常用参数

```text
--preset preview|final
--end-month YYYYMM
--months 12
--top-repos 5000
--min-shared 2
--max-actor-degree 30
--max-edges-per-node 30
--label-count 150
--max-result-rows 200000
--max-threads 4
--max-execution-time 300
--output-dir PATH
--force
```

## 数据解释边界

该图表示“共享已知非 Bot 贡献者驱动的仓库协作亲和关系”，不表示仓库间存在直接合作，也不表示代码依赖、代码相似度或组织隶属关系。

`openrank_sum` 是 12 个“带历史继承的月度 Global OpenRank”之和，不是重新计算的年度 OpenRank。OpenRank 社区数据重点覆盖 Issue、PR、Review 等可观测协作，纯 Push/Commit 贡献可能不完整；`collaboration_strength` 是本工具定义的可视化权重，不是 OpenDigger 官方指标。

## 验证

```powershell
python -W error::ResourceWarning -m unittest discover -s tests -v
python -m py_compile .\export_graph.py
```
