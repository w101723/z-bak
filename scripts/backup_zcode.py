#!/usr/bin/env python3
"""
ZCode 自动版本检查与备份脚本
功能：
1. 优先抓取 https://zcode.z.ai/cn/docs/install 获取官方最新发布版本；
   若文档站点不可达（如本地无代理环境或网络抖动），自动启动 CDN 智能嗅探作为双保险；
2. 检索所有支持平台（macOS arm64/x64, Windows x64/arm64, Linux x64/arm64）的 latest.yml 和安装包；
3. 比对 GitHub 仓库 Releases，若已有该版本且未指定 force，则跳过；
4. 若有新版本，下载全部安装包资产、生成 SHA256 校验和清单及 Release 说明；
5. 支持 GitHub Actions step output 和 Job Summary。
"""

import argparse
import hashlib
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DOCS_URL = "https://zcode.z.ai/cn/docs/install"
CDN_BASE = "https://cdn-zcode.z.ai/zcode/electron/releases"
DEFAULT_BASELINE_VERSION = "3.14.4"

PLATFORMS = [
    "macos-arm64",
    "macos-x64",
    "windows-x64",
    "windows-arm64",
    "linux-x64",
    "linux-arm64",
]


def create_ssl_context():
    """创建宽容的 SSL 上下文以兼容不同运行环境"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def http_get(url, headers=None, decode=True, timeout=12, max_retries=2):
    """发起 HTTP GET 请求，带超时与自动重试"""
    req_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    if headers:
        req_headers.update(headers)

    ctx = create_ssl_context()
    last_err = None

    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.Request(url, headers=req_headers)
            with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
                content = resp.read()
                if decode:
                    return content.decode("utf-8", errors="replace")
                return content
        except Exception as e:
            last_err = e
            if attempt < max_retries:
                time.sleep(1)
            else:
                raise last_err


def http_head(url, headers=None, timeout=8, max_retries=2):
    """发起 HTTP HEAD 请求探测资源是否存在"""
    req_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }
    if headers:
        req_headers.update(headers)

    ctx = create_ssl_context()
    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.Request(url, method="HEAD", headers=req_headers)
            with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
                return resp.status == 200, resp.headers
        except urllib.error.HTTPError as e:
            return False, e.headers
        except Exception:
            if attempt < max_retries:
                time.sleep(0.5)
            else:
                return False, None


def get_latest_version_from_docs():
    """从官方安装文档中提取最新发布的版本号"""
    print(f"[*] 正在抓取官方安装页面: {DOCS_URL}")
    html = http_get(DOCS_URL, timeout=10, max_retries=2)

    # 匹配类似于 https://cdn-zcode.z.ai/zcode/electron/releases/3.14.4/
    pattern = r"https://cdn-zcode\.z\.ai/zcode/electron/releases/(\d+\.\d+(?:\.\d+)?(?:-[a-zA-Z0-9.]+)?)/"
    matches = re.findall(pattern, html)
    if not matches:
        version_pattern = r"ZCode-(\d+\.\d+(?:\.\d+)?(?:-[a-zA-Z0-9.]+)?)"
        matches = re.findall(version_pattern, html)

    if not matches:
        raise RuntimeError("未能从安装页面解析出 ZCode 版本号")

    version = matches[0]
    print(f"[+] 成功从官方文档解析到最新版本: {version}")
    return version


def check_version_on_cdn(ver):
    """快速探测某个版本是否存在于官方 CDN 上"""
    url = f"{CDN_BASE}/{ver}/windows-x64/latest.yml"
    ok, _ = http_head(url, timeout=5)
    return ok


def probe_latest_version_from_cdn(start_version=DEFAULT_BASELINE_VERSION):
    """
    当官方安装网页暂时不可用或网络受限时，从起始基准版本出发，
    通过 CDN 向上智能嗅探最新发布的语义化版本号。
    """
    print(f"[*] 启动 CDN 智能版本嗅探，起始版本基线: {start_version}")
    clean_ver = start_version.lstrip("v")
    try:
        parts = [int(p) for p in clean_ver.split(".")[:3]]
        curr_major, curr_minor, curr_patch = parts
    except Exception:
        curr_major, curr_minor, curr_patch = 3, 14, 4

    latest_found = f"{curr_major}.{curr_minor}.{curr_patch}"

    # 1. 探测当前 minor 的后续 patch
    while True:
        candidate = f"{curr_major}.{curr_minor}.{curr_patch + 1}"
        if check_version_on_cdn(candidate):
            curr_patch += 1
            latest_found = candidate
            print(f"[+] 探测到更新的 Patch 版本: {latest_found}")
        else:
            break

    # 2. 探测更高 minor 版本
    while True:
        candidate = f"{curr_major}.{curr_minor + 1}.0"
        if check_version_on_cdn(candidate):
            curr_minor += 1
            curr_patch = 0
            latest_found = candidate
            print(f"[+] 探测到更新的 Minor 版本: {latest_found}")
            # 继续探测该 minor 的 patch
            while True:
                cand_patch = f"{curr_major}.{curr_minor}.{curr_patch + 1}"
                if check_version_on_cdn(cand_patch):
                    curr_patch += 1
                    latest_found = cand_patch
                    print(f"[+] 探测到更新的 Patch 版本: {latest_found}")
                else:
                    break
        else:
            break

    print(f"[+] CDN 智能嗅探完成，最新版本为: {latest_found}")
    return latest_found


def get_latest_github_release_tag(repo, token=None):
    """获取当前 GitHub 仓库中最新 Release 的 Tag 名称"""
    if not repo:
        return None

    url = f"https://api.github.com/repos/{repo}/releases/latest"
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"

    try:
        req = urllib.request.Request(url, headers=headers)
        ctx = create_ssl_context()
        with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("tag_name")
    except Exception:
        pass
    return None


def resolve_latest_version(repo=None, token=None):
    """综合解析最新版本：优先官方文档，异常时自动降级至 CDN 嗅探"""
    try:
        return get_latest_version_from_docs()
    except Exception as e:
        print(f"[!] 访问或解析官方文档页面异常: {e}")
        print("[*] 自动切换至 CDN 双保险嗅探模式...")

        start_ver = DEFAULT_BASELINE_VERSION
        latest_tag = get_latest_github_release_tag(repo, token)
        if latest_tag:
            start_ver = latest_tag.lstrip("v")

        return probe_latest_version_from_cdn(start_ver)


def check_release_exists(repo, tag, token=None):
    """检查 GitHub 仓库是否已存在指定的 Release Tag"""
    if not repo:
        print("[-] 未指定 GitHub 仓库 (GITHUB_REPOSITORY)，跳过已发布检查")
        return False

    url = f"https://api.github.com/repos/{repo}/releases/tags/{tag}"
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"

    try:
        req = urllib.request.Request(url, headers=headers)
        ctx = create_ssl_context()
        with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
            if resp.status == 200:
                return True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        print(f"[!] GitHub API 请求状态异常: {e.code} {e.reason}")
    except Exception as e:
        print(f"[!] 检查 GitHub Release 失败: {e}")

    return False


def parse_latest_yml(yaml_text):
    """解析 latest.yml 文件提取发布文件和更新日志"""
    files = []
    release_notes_zh = ""
    release_notes_en = ""
    release_date = ""

    file_blocks = re.findall(
        r"-\s+url:\s*([^\s\n]+)(?:.*?sha512:\s*([^\s\n]+))?(?:.*?size:\s*(\d+))?",
        yaml_text,
        re.DOTALL,
    )
    for url, sha512, size in file_blocks:
        files.append({
            "url": url.strip(),
            "sha512": sha512.strip() if sha512 else None,
            "size": int(size) if size and size.isdigit() else None,
        })

    if not files:
        path_match = re.search(r"path:\s*([^\s\n]+)", yaml_text)
        if path_match:
            files.append({"url": path_match.group(1).strip()})

    zh_match = re.search(r"zh-CN:\s*\n\s*markdown:\s*\|-?\s*\n(.*?)(?=\n\s*[a-zA-Z0-9_-]+:|\Z)", yaml_text, re.DOTALL)
    if zh_match:
        release_notes_zh = "\n".join(line.strip() for line in zh_match.group(1).strip().splitlines())

    en_match = re.search(r"en-US:\s*\n\s*markdown:\s*\|-?\s*\n(.*?)(?=\n\s*[a-zA-Z0-9_-]+:|\Z)", yaml_text, re.DOTALL)
    if en_match:
        release_notes_en = "\n".join(line.strip() for line in en_match.group(1).strip().splitlines())

    if not release_notes_zh:
        gen_notes = re.search(r"releaseNotes:\s*\|-?\s*\n(.*?)(?=\n\s*[a-zA-Z0-9_-]+:|\Z)", yaml_text, re.DOTALL)
        if gen_notes:
            release_notes_zh = "\n".join(line.strip() for line in gen_notes.group(1).strip().splitlines())

    date_match = re.search(r"releaseDate:\s*['\"]?([^'\"\n]+)['\"]?", yaml_text)
    if date_match:
        release_date = date_match.group(1).strip()

    return {
        "files": files,
        "release_notes_zh": release_notes_zh,
        "release_notes_en": release_notes_en,
        "release_date": release_date,
    }


def discover_assets(version):
    """探测并汇总所有平台的最新资产链接和更新日志"""
    print(f"[*] 正在检索版本 {version} 的各平台发布资产信息...")
    assets = []
    release_notes_zh = ""
    release_notes_en = ""
    release_date = ""

    seen_filenames = set()

    for platform in PLATFORMS:
        yml_url = f"{CDN_BASE}/{version}/{platform}/latest.yml"
        found, _ = http_head(yml_url)
        if found:
            try:
                yml_content = http_get(yml_url)
                info = parse_latest_yml(yml_content)
                if not release_notes_zh and info.get("release_notes_zh"):
                    release_notes_zh = info["release_notes_zh"]
                if not release_notes_en and info.get("release_notes_en"):
                    release_notes_en = info["release_notes_en"]
                if not release_date and info.get("release_date"):
                    release_date = info["release_date"]

                for item in info.get("files", []):
                    fn = item["url"]
                    if fn not in seen_filenames:
                        seen_filenames.add(fn)
                        download_url = f"{CDN_BASE}/{version}/{platform}/{fn}"
                        assets.append({
                            "platform": platform,
                            "filename": fn,
                            "download_url": download_url,
                            "expected_size": item.get("size"),
                            "expected_sha512": item.get("sha512"),
                        })
            except Exception as e:
                print(f"[!] 解析 {platform}/latest.yml 出现异常: {e}")
        else:
            candidates = []
            if "macos" in platform:
                arch = "arm64" if "arm64" in platform else "x64"
                candidates.extend([f"ZCode-{version}-mac-{arch}.dmg", f"ZCode-{version}-mac-{arch}.zip"])
            elif "windows" in platform:
                arch = "arm64" if "arm64" in platform else "x64"
                candidates.append(f"ZCode-{version}-win-{arch}.exe")
            elif "linux" in platform:
                arch = "arm64" if "arm64" in platform else "x64"
                candidates.extend([
                    f"ZCode-{version}-linux-{arch}.AppImage",
                    f"ZCode-{version}-linux-{arch}.deb",
                    f"ZCode-{version}-linux-{arch}.rpm",
                ])

            for fn in candidates:
                if fn not in seen_filenames:
                    cand_url = f"{CDN_BASE}/{version}/{platform}/{fn}"
                    c_found, headers = http_head(cand_url)
                    if c_found:
                        seen_filenames.add(fn)
                        cl = headers.get("content-length") if headers else None
                        assets.append({
                            "platform": platform,
                            "filename": fn,
                            "download_url": cand_url,
                            "expected_size": int(cl) if cl and cl.isdigit() else None,
                            "expected_sha512": None,
                        })

    return {
        "version": version,
        "assets": assets,
        "release_notes_zh": release_notes_zh,
        "release_notes_en": release_notes_en,
        "release_date": release_date,
    }


def download_file(url, target_path, expected_size=None, max_retries=3):
    """分块流式下载文件并显示进度，带重试机制"""
    ctx = create_ssl_context()
    target_path = Path(target_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target_path.with_suffix(target_path.suffix + ".part")

    for attempt in range(1, max_retries + 1):
        try:
            print(f"[*] 正在下载 ({attempt}/{max_retries}): {url}")
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            )
            with urllib.request.urlopen(req, context=ctx, timeout=90) as resp:
                total_bytes = resp.headers.get("content-length")
                total_bytes = int(total_bytes) if total_bytes and total_bytes.isdigit() else expected_size

                downloaded = 0
                chunk_size = 1024 * 1024 * 4  # 4MB 缓冲区
                with open(temp_path, "wb") as f:
                    while True:
                        chunk = resp.read(chunk_size)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total_bytes:
                            pct = (downloaded / total_bytes) * 100
                            mb_down = downloaded / (1024 * 1024)
                            mb_total = total_bytes / (1024 * 1024)
                            print(f"\r    -> 进度: {mb_down:.1f}MB / {mb_total:.1f}MB ({pct:.1f}%)", end="", flush=True)

            print()
            if total_bytes and downloaded < total_bytes:
                raise IOError(f"下载不完整: 预期 {total_bytes} 字节，实际得到 {downloaded} 字节")

            if temp_path.exists():
                temp_path.replace(target_path)
            print(f"[+] 下载完成并保存至: {target_path}")
            return True
        except Exception as e:
            print(f"\n[!] 第 {attempt} 次下载失败: {e}")
            if temp_path.exists():
                temp_path.unlink()
            if attempt == max_retries:
                raise
            time.sleep(2)

    return False


def calculate_sha256(file_path):
    """计算文件的 SHA256 散列值"""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(1024 * 1024 * 4):
            h.update(chunk)
    return h.hexdigest()


def generate_release_body(version, release_info, sha256_map):
    """生成详尽的 GitHub Release 说明 Markdown 文档"""
    zh_notes = release_info.get("release_notes_zh") or "暂无详细说明"
    en_notes = release_info.get("release_notes_en")
    release_date = release_info.get("release_date") or ""

    md = []
    md.append(f"## ZCode 备份版本 v{version}")
    md.append("")
    md.append(f"> 本 Release 由 GitHub Actions 自动从官方安装源同步备份。")
    md.append(f"> 官方安装页面: [{DOCS_URL}]({DOCS_URL})")
    if release_date:
        md.append(f"> 官方发布时间: `{release_date}`")
    md.append("")
    md.append("### 📝 更新日志 (Release Notes)")
    md.append("")
    md.append(zh_notes)
    md.append("")
    if en_notes and en_notes != zh_notes:
        md.append("<details>")
        md.append("<summary>English Release Notes</summary>\n")
        md.append(en_notes)
        md.append("\n</details>")
        md.append("")

    md.append("### 📦 资产下载与 SHA-256 校验和")
    md.append("")
    md.append("| 平台 / 架构 | 文件名 | 大小 | SHA-256 校验和 |")
    md.append("| :--- | :--- | :--- | :--- |")

    for asset in release_info["assets"]:
        fn = asset["filename"]
        plat = asset["platform"]
        sha = sha256_map.get(fn, "待生成")
        size_str = "-"
        if asset.get("actual_size"):
            size_mb = asset["actual_size"] / (1024 * 1024)
            size_str = f"{size_mb:.2f} MB"
        elif asset.get("expected_size"):
            size_mb = asset["expected_size"] / (1024 * 1024)
            size_str = f"{size_mb:.2f} MB"

        md.append(f"| `{plat}` | `{fn}` | {size_str} | `{sha}` |")

    md.append("")
    md.append("### 🔗 原始官方 CDN 下载直链 (参考备用)")
    md.append("")
    for asset in release_info["assets"]:
        md.append(f"- **{asset['filename']}**: [{asset['download_url']}]({asset['download_url']})")

    md.append("")
    return "\n".join(md)


def set_github_output(name, value):
    """向 GitHub Actions $GITHUB_OUTPUT 写入输出变量"""
    output_file = os.environ.get("GITHUB_OUTPUT")
    if output_file:
        with open(output_file, "a", encoding="utf-8") as f:
            if "\n" in str(value):
                delimiter = "EOF"
                f.write(f"{name}<<{delimiter}\n{value}\n{delimiter}\n")
            else:
                f.write(f"{name}={value}\n")
    print(f"[Output] {name}={value}")


def set_step_summary(markdown_text):
    """向 GitHub Actions $GITHUB_STEP_SUMMARY 写入作业概览"""
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_file:
        with open(summary_file, "a", encoding="utf-8") as f:
            f.write(markdown_text + "\n")


def main():
    parser = argparse.ArgumentParser(description="ZCode 自动版本备份工具")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""), help="GitHub仓库名 (owner/repo)")
    parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN", ""), help="GitHub Token")
    parser.add_argument("--version", default="", help="手动指定要备份的版本 (例如 3.14.4)")
    parser.add_argument("--force", action="store_true", help="如果版本已存在，仍然强制重新备份")
    parser.add_argument("--dry-run", action="store_true", help="演练模式，仅探测版本和资产，不下载大文件")
    parser.add_argument("--check-only", action="store_true", help="仅检查是否有新版本，不执行下载")
    parser.add_argument("--download-dir", default="./downloads", help="安装包下载目录")
    parser.add_argument("--output-notes", default="./release_notes.md", help="生成的 Release 说明文件路径")

    args = parser.parse_args()

    # 1. 确定目标版本
    version = args.version.strip()
    if not version:
        version = resolve_latest_version(args.repo, args.token)

    tag = f"v{version}"
    print(f"[*] 检查目标 Tag: {tag}")

    # 2. 检查 Release 是否已存在
    exists = check_release_exists(args.repo, tag, args.token)
    if exists:
        print(f"[!] GitHub Release {tag} 已存在于仓库 {args.repo} 中！")
        if not args.force:
            print("[+] 无需重复备份。退出流程。")
            set_github_output("need_backup", "false")
            set_github_output("version", version)
            set_github_output("tag", tag)
            set_step_summary(f"### ZCode 版本检查\n- 官方最新版本: `{version}`\n- 状态: **已备份** (`{tag}` 已存在)，跳过本次同步。")
            return
        else:
            print("[!] 用户指定了 --force，将强制重新下载与备份。")

    set_github_output("need_backup", "true")
    set_github_output("version", version)
    set_github_output("tag", tag)

    if args.check_only:
        print("[+] 检查完成（--check-only），检测到需要备份新版本。")
        return

    # 3. 检索资产清单及更新日志
    release_info = discover_assets(version)
    assets = release_info["assets"]
    print(f"[+] 共发现 {len(assets)} 个发布资产文件:")
    for a in assets:
        print(f"    - [{a['platform']}] {a['filename']} ({a['download_url']})")

    download_dir = Path(args.download_dir)
    download_dir.mkdir(parents=True, exist_ok=True)

    sha256_map = {}

    # 4. 下载资产 (若非 dry-run)
    if args.dry_run:
        print("[*] 处于 --dry-run 模式，跳过实际大文件下载。")
        for a in assets:
            sha256_map[a["filename"]] = "dry-run-checksum-placeholder"
    else:
        print(f"[*] 开始下载安装包至 {download_dir} ...")
        for asset in assets:
            fn = asset["filename"]
            target_file = download_dir / fn
            download_file(asset["download_url"], target_file, expected_size=asset.get("expected_size"))
            actual_size = target_file.stat().st_size
            asset["actual_size"] = actual_size
            sha256 = calculate_sha256(target_file)
            sha256_map[fn] = sha256
            print(f"[✓] {fn}: SHA-256 = {sha256} ({actual_size / (1024 * 1024):.2f} MB)")

        # 生成 SHA256SUMS.txt
        sums_file = download_dir / "SHA256SUMS.txt"
        with open(sums_file, "w", encoding="utf-8") as f:
            for fn, sha in sorted(sha256_map.items()):
                f.write(f"{sha}  {fn}\n")
        print(f"[+] 校验和清单已写入: {sums_file}")

    # 5. 生成 Release 说明
    notes_content = generate_release_body(version, release_info, sha256_map)
    notes_path = Path(args.output_notes)
    notes_path.write_text(notes_content, encoding="utf-8")
    print(f"[+] Release 说明已保存至: {notes_path}")

    # 6. 生成 GitHub Step Summary
    summary = f"""### 🚀 ZCode 新版本备份准备就绪
- **最新版本**: `{version}` ({tag})
- **发现资产数**: {len(assets)} 个
- **Release 说明**: 已生成至 `{args.output_notes}`

#### 资产列表：
| 文件名 | 平台 | 大小 |
| :--- | :--- | :--- |
"""
    for a in assets:
        sz = a.get("actual_size") or a.get("expected_size") or 0
        mb = sz / (1024 * 1024) if sz else 0
        summary += f"| `{a['filename']}` | `{a['platform']}` | {mb:.1f} MB |\n"

    set_step_summary(summary)
    print("[+] 准备工作全部完成！")


if __name__ == "__main__":
    main()
