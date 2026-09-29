# ZCode 软件版本自动备份工具 (z-bak)

本项目用于自动监控官方安装页面 [https://zcode.z.ai/cn/docs/install](https://zcode.z.ai/cn/docs/install)，每天定时检查 ZCode 是否有新版本发布。一旦检测到新版本，自动下载全平台安装包资产、生成 SHA-256 校验清单，并备份发布至当前 GitHub 仓库的 **Releases**。

---

## 🌟 功能特性

- ⏰ **全自动定时检测**：通过 GitHub Actions 每天定时运行（默认每日 UTC 01:00 / 北京时间 09:00）。
- 🎯 **双保险版本探测**：
  - **主路径**：优先抓取官方安装文档页面提取最新版本号；
  - **降级路径**：若遇文档站维护或网络受限，自动触发 CDN 智能版本嗅探。
- 💻 **全平台资产全量覆盖**：
  - **macOS**：Apple Silicon (`arm64`) 与 Intel (`x64`) 的 `.dmg` 及 `.zip` 安装包；
  - **Windows**：`x64` 与 `ARM64` 的 `.exe` 安装程序；
  - **Linux**：`x64` 与 `ARM64` 的 `.AppImage`、`.deb`、`.rpm` 以及 `.pkg.tar.zst` 安装包。
- 📝 **自动同步更新说明**：自动从发布元数据解析中英双语 Release Notes，并附带官方原始直链。
- 🔒 **完整性校验**：每次备份均实时计算全部文件的 SHA-256 哈希值，生成 `SHA256SUMS.txt` 随 Release 一同分发。
- ⚙️ **灵活的手动控制**：支持通过 GitHub 网页端的 `Actions` -> `Run workflow` 手动触发，并支持指定特定版本、强制覆盖备份或演练模式（Dry Run）。

---

## 📁 目录结构

```text
.
├── .github/
│   └── workflows/
│       └── backup-zcode.yml       # GitHub Actions 自动化工作流配置
├── scripts/
│   └── backup_zcode.py            # 核心检查、下载与校验脚本（纯标准库，零依赖）
├── .gitignore
└── README.md
```

---

## 🚀 GitHub Actions 工作流说明

工作流定义在 [`.github/workflows/backup-zcode.yml`](.github/workflows/backup-zcode.yml)。

### 1. 自动定时执行
- 每日 UTC 01:00（北京时间 09:00）自动触发。
- 检查官方最新版本与当前仓库已有的 Release Tag（如 `v3.14.4`）。
- 若已备份，则直接退出，不消耗额外的构建时长和网络流量；
- 若检测到新版本，自动下载所有资产并发布为新 Release。

### 2. 手动触发 (Workflow Dispatch)
可在 GitHub 仓库页面进入 **Actions** -> **Daily Backup ZCode Releases** -> 点击 **Run workflow**：
- **`version`**：手动指定备份版本（例如 `3.14.4`，留空则自动检测官方最新版本）。
- **`force`**：若勾选，即使该版本已存在也会强制重新下载并覆盖上传。
- **`dry_run`**：演练模式，仅探测版本、解析资产与生成说明，不下载大文件和创建 Release。

---

## 🛠️ 本地运行与调试

本项目的 Python 脚本仅依赖 Python 3.8+ 标准库，无需 `pip install` 任何第三方依赖。

### 仅检查是否有新版本
```bash
python3 scripts/backup_zcode.py --repo <owner>/<repo> --check-only
```

### 演练模式（不下载大文件）
```bash
python3 scripts/backup_zcode.py --dry-run
```

### 指定版本并执行全量下载
```bash
python3 scripts/backup_zcode.py --version 3.14.4 --download-dir ./downloads
```

---

## 📄 License
MIT License
