#!/usr/bin/env python3
"""wechat-mac-versions 公共库：dmg 下载、挂载、Info.plist 三键提取、release 发布。

三键定义（来自 WeChat.app/Contents/Info.plist）：
- dest  : WeChatBundleVersion     四段版本号，如 4.1.12.29
- short : CFBundleShortVersionString  三段版本号，如 4.1.12
- build : CFBundleVersion         构建号，如 269341

Tag 规范：<dest>-<build>（如 4.1.12.29-269341），组合唯一。
"""

import datetime
import hashlib
import plistlib
import shutil
import subprocess
from pathlib import Path

ASSET_PREFIX = "WeChatMac"


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    """运行子进程，默认捕获输出并检查返回码。"""
    return subprocess.run(
        cmd, check=check, text=True,
        stdout=subprocess.PIPE if check else None,
        stderr=subprocess.STDOUT if check else None,
    )


def log(message: str) -> None:
    print(message, flush=True)


def mount_dmg(dmg_path: Path) -> str:
    """挂载 dmg 到独立挂载点，返回挂载路径。"""
    mountpoint = Path("/tmp") / f"wxmount-{datetime.datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    mountpoint.mkdir(exist_ok=True)
    result = run(
        ["hdiutil", "attach", str(dmg_path), "-nobrowse", "-mountpoint", str(mountpoint)]
    )
    return str(mountpoint)


def detach_dmg(mountpoint: str) -> None:
    run(["hdiutil", "detach", mountpoint, "-quiet"], check=False)


def extract_info_plist(mount_dir: str) -> dict:
    """读取挂载的 WeChat.app 的 Info.plist，提取三键。"""
    info_plist = Path(mount_dir) / "WeChat.app" / "Contents" / "Info.plist"
    if not info_plist.exists():
        raise RuntimeError(f"Info.plist not found: {info_plist}")
    with info_plist.open("rb") as handle:
        data = plistlib.load(handle)
    short = str(data.get("CFBundleShortVersionString", "")).strip()
    build = str(data.get("CFBundleVersion", "")).strip()
    dest = str(data.get("WeChatBundleVersion", "")).strip()
    if not short:
        raise RuntimeError("CFBundleShortVersionString not found in Info.plist")
    if not build:
        raise RuntimeError("CFBundleVersion not found in Info.plist")
    if not dest:
        # 官方未提供四段版本号时退化（参考 zsbai 规则）
        dest = f"{short}+build.{build}"
    return {"dest": dest, "short": short, "build": build}


def compute_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def release_tag(info: dict) -> str:
    """Tag = <dest>-<build>，如 4.1.12.29-269341。"""
    return f"{info['dest']}-{info['build']}"


def asset_name(info: dict) -> str:
    """资产名 = WeChatMac-<dest>-build.<build>.dmg。"""
    return f"{ASSET_PREFIX}-{info['dest']}-build.{info['build']}.dmg"


def release_title(info: dict) -> str:
    return f"Wechat For Mac {info['dest']} (build {info['build']})"


def build_release_notes(info: dict, download_from: str, remote_md5: str,
                        sha256_sum: str, remote_size: str,
                        remote_last_modified: str) -> str:
    lines = [
        "WeChat for Mac automatic release",
        "",
        "Release details",
        f"- DestVersion: {info['dest']}",
        f"- BuildVersion: {info['build']}",
        f"- ShortVersion: {info['short']}",
        "",
        "Source and checksums",
        f"- DownloadFrom: {download_from}",
    ]
    if remote_md5:
        lines.append(f"- Md5: {remote_md5}")
    lines.append(f"- Sha256: {sha256_sum}")
    if remote_size:
        lines.append(f"- ContentLength: {remote_size}")
    if remote_last_modified:
        lines.append(f"- LastModified: {remote_last_modified}")
    lines.append(
        f"- UpdateTime: {datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} (UTC)"
    )
    return "\n".join(lines) + "\n"


def write_sha_file(sha_path: Path, info: dict, download_from: str, remote_md5: str,
                   sha256_sum: str, remote_size: str, remote_last_modified: str) -> None:
    lines = [
        f"DestVersion: {info['dest']}",
        f"BuildVersion: {info['build']}",
        f"ShortVersion: {info['short']}",
    ]
    if remote_md5:
        lines.append(f"Md5: {remote_md5}")
    lines.append(f"Sha256: {sha256_sum}")
    if remote_size:
        lines.append(f"ContentLength: {remote_size}")
    if remote_last_modified:
        lines.append(f"LastModified: {remote_last_modified}")
    lines.append(
        f"UpdateTime: {datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} (UTC)"
    )
    lines.append(f"DownloadFrom: {download_from}")
    sha_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def gh(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    """执行 gh CLI。"""
    return subprocess.run(
        ["gh"] + args, check=check, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )


def release_exists(tag: str) -> bool:
    result = gh(["release", "view", tag, "--json", "tagName"], check=False)
    return result.returncode == 0


def publish_release(info: dict, dmg_path: Path, download_from: str,
                    remote_md5: str, remote_size: str,
                    remote_last_modified: str) -> str:
    """按规范发布 release，返回 tag。dmg 会被复制为规范资产名。"""
    name = asset_name(info)
    final_dmg = dmg_path.parent / name
    shutil.copy2(dmg_path, final_dmg)

    sha256_sum = compute_sha256(final_dmg)
    sha_path = dmg_path.parent / (name + ".sha256")
    write_sha_file(sha_path, info, download_from, remote_md5, sha256_sum,
                   remote_size, remote_last_modified)

    notes = build_release_notes(info, download_from, remote_md5, sha256_sum,
                                remote_size, remote_last_modified)
    notes_file = dmg_path.parent / "release_notes.txt"
    notes_file.write_text(notes, encoding="utf-8")

    tag = release_tag(info)
    if release_exists(tag):
        # 组合撞 tag（官网异常重复）时追加日期后缀兜底
        tag = f"{tag}_{datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d')}"
    gh(["release", "create", tag, str(final_dmg), str(sha_path),
        "-F", str(notes_file), "-t", release_title(info)])
    return tag
