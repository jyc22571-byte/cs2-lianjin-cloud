# 炼金云端（GitHub Actions 每日自动扫描）

CS2 炼金"找赚钱炉子"的云端每日扫描：每天北京时间 **00:05** 自动跑一遍全库取价，
把三档榜写回仓库的 `净EV榜.json`。本地 EXE 只需读这个文件即可看到当天最新榜，
不用自己开电脑挂着扫。

## 目录结构

```
炼金云端/
├── 云端扫描.py               ← 扫描程序（纯标准库，无第三方依赖）
├── 净EV榜.json               ← 产物：每天由 Actions 自动更新
├── 数据/                     ← 静态规则库（皮肤归属/稀有度/上级产物池/SteamDT 名称桥）
│   ├── skins_collections_containers.json
│   ├── 上级产物池.json
│   └── 规则库_v03.json
└── .github/workflows/scan.yml
```

## 原理

1. `云端扫描.py` 从 `数据/` 读出全部收藏品 → 各级稀有度的上级产物池 → 生成约
   4907 个需取价的 Steam 市场名称（主料档 FT/MW/WW + 产物档 FN/MW/FT + 通用辅料）。
2. 用 SteamDT **批量接口**（100 个名字/次，限 1 次/分钟）逐个批次拉价，
   每批之间睡 61 秒 → 全程约 **50 批 ≈ 50-60 分钟**（远低于 Actions 6 小时上限）。
3. 按与本地 `扫描服务.py` 相同算法（手续费 买 2.5%/卖 2.5%、平台 BUFF、卖价口径）
   算出三档榜：`久经→略磨`、`略磨→崭新`、`破损→久经`，各按净EV降序写入
   `净EV榜.json`。
4. GitHub Actions 每批失败的批次自动重试 4 次，最后提交回仓库。

## 首次部署（约 5 分钟）

1. **建私有仓库**：GitHub 右上 New repository → 选 Private →
   把本文件夹里所有内容推上去（含 `.github/`，注意别漏隐藏文件夹）。
2. **加 API Key 密钥**：仓库 → Settings → Secrets and variables → Actions →
   New repository secret：
   - Name：`STEAMDT_API_KEY`
   - Secret：你的 SteamDT API Key（本地 配置/settings.json 里那个）
3. **开写权限**：仓库 → Settings → Actions → General →
   Workflow permissions → 选 **Read and write permissions** → Save。
4. **手动跑一次验证**：仓库 → Actions → 左侧「每日炼金扫描」→
   Run workflow → 绿色按钮。第一次全跑约 50-60 分钟，跑完刷新仓库根目录
   应看到更新时间的 `净EV榜.json`（点开里面是 date + 三档 modes）。
5. 之后每天北京时间 00:05 自动跑，无需任何操作。

## 本地读取当天结果

打开 `F:\dsh\捡钱v2.0` 目录里生成的 EXE B 页，选择"读取本地结果/云端同步"
指向云端仓库拉下来的 `净EV榜.json` 即可（与本地扫描产出的文件同构，可直接读）。

（进阶：手动拉取 —— 仓库 Code 页点 `净EV榜.json` → 右键 Raw 另存，覆盖本地
`缓存/净EV榜.json`。）

## EA 算法升级说明

当前榜单仍是**简化版口径**（3 个最便宜主料 + 7 个通用辅料、产物按上级低一档磨损均价），
与本地一致，仅作过渡。新的 EA 扫描方式（枚举主料数量 n=1..9、低浮点辅料归一化拉浮点、
承接 S/求购/流动性、回归真实炉子）届时只需改写 `云端扫描.py` 里的
`compute_board()`（以及需要时扩展 `build_need()` 的取价名单），工作流无需改动，
下次零点自动用新算法出榜。

## 数据更新说明

`数据/` 里的规则库是本地从 SteamDB / SteamDT 核对整理的静态数据（94 个收藏品 /
1455 皮肤，稀有度已权威核对）。后续若官方出新收藏品，需在本地更新这三个 JSON
后重新推送，云端才会覆盖到新皮肤。
