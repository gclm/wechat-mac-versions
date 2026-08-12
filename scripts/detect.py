#!/usr/bin/env python3
"""每日检测微信 macOS 官网，发现新 dmg 时下载、提取「版本号+构建号」并发布 release。

流程：
1. 抓官网 mac.weixin.qq.com 解析最新下载链接
2. HEAD 请求取 MD5（x-cos-meta-md5），与最新 release 的 Md5 对比，无变化则跳过
3. 下载 dmg → hdiutil 挂载 → 读 Info.plist 提取三键（WeChatBundleVersion / CFBundleVersion / CFBundleShortVersionString）
4. 按 Tag 规范 <dest>-<build> 发布 release（正文含 BuildVersion，机器可读）

用法：python3 scripts/detect.py [--force]
"""

import argparse
import datetime
import html.parser
import shutil
import time
import urllib.request
from pathlib import Path

from common import (
    build_release_notes,
    detach_dmg,
    extract_info_plist,
    gh,
    log,
    mount_dmg,
    publish_release,
    release_exists,
    release_tag,
    run,
)

WEBSITE_URL = "https://mac.weixin.qq.com/?t=mac&lang=zh_CN"
WORK_DIR = Path.cwd() / ".work"


class DownloadLinkParser(html.parser.HTMLParser):
    """从微信 Mac 官网 HTML 解析最新下载链接（class="download-button"）。"""

    def __init__(self) -> None:
        super().__init__()
        self.link = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a" or self.link:
            return
        attrs_dict = {key: value or "" for key, value in attrs}
        if "download-button" in attrs_dict.get("class", "").split():
            self.link = attrs_dict.get("href", "").strip()


def fetch_download_link() -> str:
    with urllib.request.urlopen(WEBSITE_URL, timeout=60) as response:
        html = response.read().decode("utf-8", errors="replace")
    parser = DownloadLinkParser()
    parser.feed(html)
    if not parser.link:
        raise RuntimeError("Download link not found on website.")
    return parser.link


def fetch_head_metadata(url: str) -> dict[str, str]:
    """HEAD 请求读取直接文件链接的元数据（键为小写）。"""
    attempts = 2
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            request = urllib.request.Request(url, method="HEAD")
            with urllib.request.urlopen(request, timeout=60) as response:
                return {key.lower(): value.strip() for key, value in response.headers.items()}
        except Exception as exc:
            last_error = exc
            if attempt < attempts:
                log(f"HEAD request failed (attempt {attempt}). Retrying in 10s...")
                time.sleep(10)
    if last_error:
        raise last_error


def parse_release_body(body: str) -> dict[str, str]:
    """从 release 正文解析 "Key: Value" 行。"""
    info: dict[str, str] = {}
    for line in body.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        info[key.lstrip("- ").strip()] = value.strip()
    return info


def get_latest_release_info() -> dict[str, str]:
    result = gh(["release", "view", "--json", "body", "--jq", ".body"], check=False)
    if result.returncode != 0 or not result.stdout:
        return {}
    return parse_release_body(result.stdout)


def download_dmg(url: str, dest: Path) -> None:
    """下载 dmg，失败重试。"""
    attempts = 2
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            run(["wget", "--quiet", "--tries", "5", "--waitretry", "5",
                 "--retry-connrefused", url, "-O", str(dest)])
            return
        except Exception as exc:
            last_error = exc
            if attempt < attempts:
                log(f"Download failed (attempt {attempt}). Retrying in 10s...")
                time.sleep(10)
    if last_error:
        raise last_error


def main() -> int:
    parser = argparse.ArgumentParser(description="检测微信官网新版本并发布")
    parser.add_argument("--force", action="store_true", help="即使 MD5 无变化也强制重新发布")
    args = parser.parse_args()

    mount_dir = ""
    try:
        WORK_DIR.mkdir(exist_ok=True)

        log("Step 1: resolving download link from official website...")
        download_link = fetch_download_link()
        log(f"Download link: {download_link}")

        log("Step 2: fetching HEAD metadata...")
        headers = fetch_head_metadata(download_link)
        remote_md5 = headers.get("x-cos-meta-md5", "")
        remote_size = headers.get("content-length", "")
        remote_last_modified = headers.get("last-modified", "")
        log(f"HEAD metadata: md5={remote_md5 or 'n/a'}, size={remote_size or 'n/a'}")

        log("Step 3: comparing with latest release...")
        latest = get_latest_release_info()
        latest_md5 = latest.get("Md5", "")
        latest_sha256 = latest.get("Sha256", "")
        if remote_md5 and latest_md5 and remote_md5 == latest_md5 and not args.force:
            log("No new version detected (MD5 unchanged). Skipping.")
            return 0
        if not latest_md5 and latest_sha256 and remote_md5 and not args.force:
            log("Latest release has no MD5, skip detection (no reliable baseline).")
            return 0

        log("Step 4: downloading DMG...")
        dmg_path = WORK_DIR / "WeChatMac.dmg"
        download_dmg(download_link, dmg_path)

        log("Step 5: mounting and extracting Info.plist...")
        mount_dir = mount_dmg(dmg_path)
        info = extract_info_plist(mount_dir)
        detach_dmg(mount_dir)
        mount_dir = ""
        log(f"Extracted: dest={info['dest']}, short={info['short']}, build={info['build']}")

        tag = release_tag(info)
        if release_exists(tag) and not args.force:
            log(f"Release {tag} already exists. Skipping.")
            return 0
        if not remote_md5:
            # 官网 HEAD 无 MD5 头时用 sha256 兜底对比
            log("No remote MD5 available; publishing without baseline check.")

        log("Step 6: publishing release...")
        published_tag = publish_release(
            info, dmg_path, download_link, remote_md5,
            remote_size, remote_last_modified,
        )
        log(f"Published: {published_tag}")
        return 0
    except Exception as exc:
        log(f"ERROR: {exc}")
        return 1
    finally:
        if mount_dir:
            detach_dmg(mount_dir)
        shutil.rmtree(WORK_DIR, ignore_errors=True)
        log("Cleanup completed.")


if __name__ == "__main__":
    raise SystemExit(main())
