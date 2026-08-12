#!/usr/bin/env python3
"""从 zsbai/wechat-versions 批量导入历史版本到本仓库。

每个版本：下载上游 release 的 dmg → 挂载提取三键 → 按本仓库 Tag 规范
（<dest>-<build>）发布 release。已存在的 tag 自动跳过，可分批续跑。

用法：
  python3 scripts/import_history.py --all              # 导入全部
  python3 scripts/import_history.py --v4-only          # 只导入微信 4 系列（4.x / v4.x）
  python3 scripts/import_history.py --limit 5          # 只导入 5 个（最新优先）
  python3 scripts/import_history.py --tag 4.1.11.55    # 导入指定上游 tag
  python3 scripts/import_history.py --v4-only --dry-run # 只提取不发布

依赖：gh CLI（GITHUB_TOKEN）+ macOS（hdiutil）。
"""

import argparse
import json
import shutil
import time
from pathlib import Path

from common import (
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

UPSTREAM = "zsbai/wechat-versions"
WORK_DIR = Path.cwd() / ".work"


def list_upstream_releases() -> list[dict]:
    """分页拉取上游全部 release（tag、dmg 资产名、release body）。"""
    result = gh([
        "api", f"repos/{UPSTREAM}/releases", "--paginate",
        "--jq", '.[] | {tag_name, assets: [.assets[] | select(.name | endswith(".dmg")) | .name], body}',
    ])
    releases = []
    for line in result.stdout.splitlines():
        try:
            releases.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return releases


def parse_body(body: str) -> dict[str, str]:
    info: dict[str, str] = {}
    for line in (body or "").splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        info[key.lstrip("- ").strip()] = value.strip()
    return info


def download_upstream_asset(tag: str, asset_name: str, dest: Path) -> None:
    """从上游 release 下载 dmg 资产（GitHub 内网，速度快）。"""
    run(["gh", "release", "download", tag, "-R", UPSTREAM,
         "-p", asset_name, "-D", str(dest.parent), "--clobber"])
    downloaded = dest.parent / asset_name
    if not downloaded.exists():
        raise RuntimeError(f"Download failed: {asset_name}")
    downloaded.rename(dest)


def import_one(upstream: dict, dry_run: bool = False) -> str:
    """导入单个上游 release，返回状态：skipped / imported / error:..."""
    tag = upstream["tag_name"]
    assets = upstream.get("assets") or []
    if not assets:
        return "skipped (no dmg asset)"
    dmg_asset = assets[0]
    body_info = parse_body(upstream.get("body", ""))

    WORK_DIR.mkdir(exist_ok=True)
    dmg_path = WORK_DIR / f"upstream-{tag.replace('/', '_')}.dmg"
    mount_dir = ""
    try:
        log(f"[{tag}] downloading {dmg_asset} ...")
        download_upstream_asset(tag, dmg_asset, dmg_path)

        log(f"[{tag}] extracting Info.plist ...")
        mount_dir = mount_dmg(dmg_path)
        info = extract_info_plist(mount_dir)
        detach_dmg(mount_dir)
        mount_dir = ""
        log(f"[{tag}] dest={info['dest']}, short={info['short']}, build={info['build']}")

        our_tag = release_tag(info)
        if release_exists(our_tag):
            log(f"[{tag}] our release {our_tag} already exists. Skipping.")
            return "skipped"

        if dry_run:
            log(f"[{tag}] DRY-RUN: would publish {our_tag}")
            return "dry-run"

        download_from = body_info.get("DownloadFrom", "")
        published = publish_release(
            info, dmg_path, download_from,
            body_info.get("Md5", ""),
            body_info.get("ContentLength", ""),
            body_info.get("LastModified", ""),
        )
        log(f"[{tag}] published {published}")
        return "imported"
    except Exception as exc:
        log(f"[{tag}] ERROR: {exc}")
        return f"error: {exc}"
    finally:
        if mount_dir:
            detach_dmg(mount_dir)
        if dmg_path.exists():
            dmg_path.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description="从 zsbai/wechat-versions 导入历史版本")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="导入全部历史版本（跳过已存在）")
    group.add_argument("--v4-only", action="store_true", help="只导入微信 4 系列（tag 以 4. 或 v4. 开头）")
    group.add_argument("--limit", type=int, metavar="N", help="只导入最新 N 个")
    group.add_argument("--tag", metavar="TAG", help="只导入指定上游 tag")
    parser.add_argument("--dry-run", action="store_true", help="只下载提取，不发布")
    parser.add_argument("--sleep", type=float, default=5.0, help="每个版本之间的间隔秒数（防限流）")
    args = parser.parse_args()

    log(f"Fetching upstream releases from {UPSTREAM} ...")
    releases = list_upstream_releases()
    log(f"Found {len(releases)} upstream releases.")

    if args.tag:
        releases = [r for r in releases if r["tag_name"] == args.tag]
        if not releases:
            log(f"Upstream tag not found: {args.tag}")
            return 1
    elif args.v4_only:
        releases = [r for r in releases if r["tag_name"].startswith(("4.", "v4."))]
        log(f"Filtered to v4 series: {len(releases)} releases.")
    elif args.limit:
        releases = releases[: args.limit]

    summary = {"imported": 0, "skipped": 0, "dry-run": 0, "errors": []}
    for i, rel in enumerate(releases, 1):
        status = import_one(rel, dry_run=args.dry_run)
        if status == "imported":
            summary["imported"] += 1
        elif status == "skipped":
            summary["skipped"] += 1
        elif status == "dry-run":
            summary["dry-run"] += 1
        elif status.startswith("error"):
            summary["errors"].append(status)
        if i < len(releases) and args.sleep > 0:
            time.sleep(args.sleep)

    shutil.rmtree(WORK_DIR, ignore_errors=True)
    log(f"Done. imported={summary['imported']}, skipped={summary['skipped']}, "
        f"dry-run={summary['dry-run']}, errors={len(summary['errors'])}")
    for err in summary["errors"]:
        log(f"  {err}")
    return 1 if summary["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
