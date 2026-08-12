# WeChat Mac Versions

微信 macOS 官方版本存档：每天自动检测官网，**同时提取版本号（`WeChatBundleVersion`）与构建号（`CFBundleVersion`）**，按 `版本号-构建号` 打 tag 发布到 Releases。

## 为什么需要这个仓库

微信官网 CDN 只保留每个大版本的**最终热修** dmg（如 `WeChatMac_4.1.12.dmg`），历史构建号无法从官网下载。本仓库记录官网每次 dmg 更新的完整轨迹，并直接给出机器可读的构建号。
已从 [zsbai/wechat-versions](https://github.com/zsbai/wechat-versions) 导入微信 4 全系列 **63 个历史版本**（含 4.0.x 旧格式），完整映射表见 **[docs/version-map.md](docs/version-map.md)**（含 wechat-antirecall 支持状态标注）。，供下游项目（如 [wechat-antirecall](https://github.com/fzlzjerry/wechat-antirecall)）在打包、适配、排障时零成本获取「版本号 + 构建号」。

## Release 规范

| 项目 | 规范 | 示例 |
| --- | --- | --- |
| Tag | `<WeChatBundleVersion>-<CFBundleVersion>`（组合唯一，同一版本号下的不同构建号各自成 tag） | `4.1.12.29-269341` |
| 标题 | `Wechat For Mac <版本号> (build <构建号>)` | `Wechat For Mac 4.1.12.29 (build 269341)` |
| 资产 | `WeChatMac-<版本号>-build.<构建号>.dmg` + `.sha256` | `WeChatMac-4.1.12.29-build.269341.dmg` |
| 正文 | `Key: Value` 行，机器可解析 | 见下 |

Release 正文示例：

```text
Release details
- DestVersion: 4.1.12.29
- BuildVersion: 269341
- ShortVersion: 4.1.12

Source and checksums
- DownloadFrom: https://dldir1v6.qq.com/weixin/Universal/Mac/WeChatMac_4.1.12.dmg
- Md5: ...
- Sha256: ...
- ContentLength: 509508392
- LastModified: ...
- UpdateTime: ... (UTC)
```

消费端只需读取最新 release 正文的 `BuildVersion` 行即可，无需下载 dmg。

## 如何消费（查询最新微信版本号 + 构建号）

```bash
# 最新 release 的版本号与构建号（零下载）
gh release view --repo gclm/wechat-mac-versions --json body --jq .body | grep -E 'DestVersion|BuildVersion'

# 与 wechat-antirecall 的 patches.json 对比，判断是否已被支持
curl -s https://raw.githubusercontent.com/fzlzjerry/wechat-antirecall/main/patches.json \
  | grep -q '"269341"' && echo "supported" || echo "not supported"
```

## 运行机制

- `.github/workflows/daily.yml`：每天 UTC 07:00 自动检测官网；`workflow_dispatch` 可手动触发（`force` 强制重发）。
- `.github/workflows/import.yml`：手动触发，从 [zsbai/wechat-versions](https://github.com/zsbai/wechat-versions) 批量导入历史版本（`all` 全量 / `limit` 只导最新 N 个），已存在的 tag 自动跳过，可分批续跑。
- `scripts/detect.py`：日常检测流程（抓官网 → HEAD 对比 MD5 → 下载 → 提取三键 → 发布）。
- `scripts/import_history.py`：历史批量导入流程。
- `scripts/common.py`：共享库（挂载 / 提取 / 校验和 / 发布）。

### 提取的三键（来自 `WeChat.app/Contents/Info.plist`）

| 键 | 含义 | 示例 |
| --- | --- | --- |
| `WeChatBundleVersion` | 四段版本号（官方 UI 展示）；4.0.x 时代无此键，退化为 `短版本+build.构建号` | `4.1.12.29` |
| `CFBundleVersion` | 构建号（逆向/补丁匹配用） | `269341` |
| `CFBundleShortVersionString` | 三段版本号 | `4.1.12` |

## 本地运行

```bash
# 日常检测（含发布）
python3 scripts/detect.py --force

# 历史导入（--dry-run 只提取不发布；--all 全量；--limit N 只导最新 N 个）
python3 scripts/import_history.py --limit 3 --dry-run
```

依赖：macOS（`hdiutil`）、`gh` CLI（已登录或 `GH_TOKEN`）、`wget`。

## 致谢

存档思路与抓取逻辑参考 [zsbai/wechat-versions](https://github.com/zsbai/wechat-versions)（MIT），在其基础上增加构建号提取与 `版本号-构建号` tag 规范。
