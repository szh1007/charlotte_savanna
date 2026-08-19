# Bugfix 0003 — 选「最佳画质」下载必报 Requested format is not available

> 记录日期: 2026-08-19 | 状态: 已修复 | 涉及文件: `backend/downloader.py`、`backend/task_manager.py`、`backend/schemas.py`、`tests/conftest.py`、`tests/test_downloader.py`、`tests/test_downloads.py`、`tests/test_resolve.py`、`tests/test_paid_differences.py`

> 更新: 2026-08-19 补充「无声问题」已实施音视频合并方案 (见文末章节)

## 问题现象

解析 B 站视频后，下拉框默认选中「最佳画质」，点击「开始下载」必失败，任务报错：

```
[BiliBili] BV1RU836PELv: Requested format is not available. Use --list-formats for a list of available formats
```

手动改选其他具体档位（360p / 720p / 1080p 等任意一个）下载均正常。即「最佳画质」档位 100% 失败、具体档位 100% 成功——规律稳定，与视频、账号无关。

## 原因追溯

### 直接原因：虚拟档位 `format_id="best"` 不是真实格式 id

`_to_formats()` 按高度去重生成展示档位后，末尾追加一个「最佳画质」虚拟档位：

```python
formats.append({
    "format_id": "best",   # ← 字面字符串 "best"
    ...
})
```

创建下载任务时该 id 通过校验（校验对象是加工后的档位列表，`best` 存在），随后原样传给 yt-dlp 的 `format` 参数。**`best` 是 yt-dlp 的格式选择表达式，不是格式 id**——这是设计缺陷。

### 深层原因：yt-dlp 的 `best` 只匹配「音视频合一」格式

yt-dlp 格式选择器（`YoutubeDL.py` `build_format_selector`）对无 `*` 修饰的 `best` 使用过滤器：

```python
# b / best / w / worst 且无 *: 只匹配单一音视频合一格式
lambda f: f.get('vcodec') != 'none' and f.get('acodec') != 'none'
```

实测目标视频 `BV1RU836PELv`（未登录）的 formats **全部是 DASH 分离流**，无任何合一格式：

| format_id | height | vcodec | acodec | 说明 |
|-----------|--------|--------|--------|------|
| `30216` / `30232` / `30280` | — | none | mp4a | 纯音频流 |
| `30016` / `30011` / `100022` | 360 | avc/hevc/av1 | none | DASH 视频流 (video-only) |
| `30032` / `30033` / `100023` | 480 | avc/hevc/av1 | none | DASH 视频流 |
| `30064` / `30066` / `100024` | 720 | avc/hevc/av1 | none | DASH 视频流 |
| `30080` / `30077` / `100026` | 1080 | avc/hevc/av1 | none | DASH 视频流 |

`best` 匹配为空。yt-dlp 的兜底分支（`best/worst` 对 `incomplete_formats` 的 fallback）要求「所有格式都没有视频**或**所有格式都没有音频」——B 站视频流、音频流两者都有，不满足 → 无任何兜底 → `formats_to_download` 为空 → 抛 `Requested format is not available`（`YoutubeDL.py:3095-3099`）。

具体档位（如 `30064`）走**字面 format_id 匹配**（`f['format_id'] == format_spec`），与 `best` 不同路径，故全部正常。

### 测试未拦截的原因

`conftest.py` 的 `FAKE_INFO` mock 数据中 1080p 档是**含音频**的 `999`（webm）——mock 格式结构与真实 B 站（全 DASH 分离）不符，`best` 表达式在 mock 数据上能匹配到合一格式 → 测试全绿、线上必炸。与 bugfix/0001 同模式：mock 数据掩盖真实引擎行为。

## 如何修复

`_to_formats()` 中「最佳画质」档位不再使用字面 `"best"`，改为**展示最高档的真实 format_id**：

```python
if formats:
    best = formats[-1]
    # 最佳画质指向最高档的真实 format_id, 而非字面 "best":
    # "best" 是 yt-dlp 格式选择表达式, 只匹配音视频合一的单一格式,
    # B 站等平台返回全 DASH 分离流时匹配为空 → 下载报
    # "Requested format is not available" (见 bugfix/0003)
    formats.append({
        "format_id": best["format_id"],
        "height": best["height"],
        "ext": "mp4",
        "label": f"最佳画质 ({best['height']}p)",
    })
```

语义修正：**「最佳画质」= 展示列表最高档**（前端选它 = 手选最高档，行为完全一致），而不是 yt-dlp 的 `best` 表达式。创建任务校验、下载前重校验、档位锁定判断（按 height）全部无需改动。

**验证**：

- 真实环境：同一 B 站视频，`format=30080`（1080p）与 `format=30064`（720p）走完整格式选择均成功（修复前 `format=best` 必失败）
- 新增回归测试：
  - `tests/test_downloader.py`：全 DASH 分离流下 `_to_formats` 最佳画质指向真实最高档 id
  - `tests/test_downloads.py`：API 层集成——mock 真实 B 站结构，解析 → 取最佳画质档位 → 创建下载 → 断言引擎收到真实 id `30080` 且任务完成
  - `tests/test_resolve.py` / `tests/test_paid_differences.py`：断言更新为真实 id（`999`）
- 全量测试 61 passed（此前 59），ruff check + format 通过

## 附带发现（已实施：音视频合并方案）

**B 站全 DASH 分离流下，选择任何单一档位（含修复后的最佳画质）下载的都是 video-only 纯视频流——文件无声。** 此问题与本次报错同源（B 站不提供合一格式），原属独立行为缺陷。

### 实施（2026-08-19，用户确认需要有声视频）

所有 DASH video-only 档位下载时自动合并音频流：

1. **档位标记**：`_to_formats()` 每个档位增加 `has_audio` 字段（合一格式 `True`，DASH 分离流 `False`）；最佳画质档位复制最高档标记。`FormatOut` 同步新增该字段（API 契约扩展，前端无感知）。
2. **任务记录**：`create_download` 校验时计算 `merge_audio = not fmt["has_audio"]`，存入 Task；`_run_download` 透传给引擎。
3. **格式表达式**：`_format_spec(format_id, merge_audio)` ——
   `merge_audio=True` 时 `format = "{format_id}+bestaudio*/{format_id}"`（指定视频流 + 最佳音频流合并；`/` 回退单流，平台无音频流时不至于整个选择失败）；合一档位保持原样不合并。

   > 与文档此前设想的 `bestvideo*+bestaudio` 不同：用户选择具体档位（如 720p）时必须绑定该档位视频流，`bestvideo*` 会选到更高画质。**合并只发生在同一任务内**，无跨档位升级。
4. **ffmpeg 预检**：`merge_audio=True` 且 `shutil.which("ffmpeg")` 为空 → 下载前即报明确错误「需要 ffmpeg 合并」，不浪费流量等下载完才失败。

**验证**：

- 真实环境：`BV1RU836PELv` 选 720p 档（`30064`）→ 视频流 95.5 MiB + 音频流 20.2 MiB（`30280`）→ 合并输出 `BV1RU836PELv_30064+30280.mp4`，ffprobe 确认含 `h264 (720p) + aac` 双流
- 新增 5 个单元测试（has_audio 标记、`_format_spec` 双分支、ffmpeg 缺失预检、检测函数）；API 层集成测试断言最佳画质链路 `merge_audio=True` 透传
- 全量 66 passed，ruff check + format 通过

**降级行为**：ffmpeg 缺失时合并档位报明确错误（提示安装）；合一格式平台（YouTube 部分档位等）不合并、不受影响。

## 替换方案（备选，当前已采用合并方案）

| 方案 | 说明 | 评价 |
|------|------|------|
| `format={format_id}+bestaudio*/{format_id}`（已实施 ✅） | 指定视频流 + 最佳音频流由 ffmpeg 合并，输出有声文件；`/` 回退单流 | 需要 ffmpeg（本机已装 9.0）；档位精确绑定不跨档升级；用户选具体档位与最佳画质均有声音 |
| `format=bestvideo*+bestaudio` | 最高画质视频流 + 最佳音频流合并 | 仅适合「最佳画质」语义；用户手选具体档位时会升级到更高画质，不可用于具体档位 |
| `format=best*`（带 `*`） | 匹配所有格式（含分离流），选中 quality 最高单一格式 | 不依赖 ffmpeg；但选中的仍是 video-only 流（无声），且对 YouTube 等平台语义从「最佳合一」变为「最佳任意流」——行为回归，不采纳 |
| 保持字面 `best` | 不改动 | 对 B 站必然失败，不可行 |

> 无 audio 平台（纯视频流无音频可合并）由 `/{format_id}` 回退，输出无声但不报错。
