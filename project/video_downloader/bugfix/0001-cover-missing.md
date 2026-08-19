# Bugfix 0001 — 解析出的视频信息没有封面

> 记录日期: 2026-08-19 | 状态: 已修复 (两阶段) | 涉及文件: `backend/downloader.py`、`frontend/src/components/ResolveResult.vue`、`frontend/src/components/TaskPanel.vue`、`tests/test_downloader.py`、`tests/test_resolve.py`

## 问题现象

在 B 站等国内平台解析视频链接后，前端解析结果卡和任务列表均不显示封面（破图），只显示占位符 🎬。YouTube 等国外平台正常。**本地 `http://localhost` 开发环境下同样不显示**——这是定位根因的关键线索。

## 原因追溯

排查了三次，才找到真正的主因：

### 阶段一：误判为 http 混合内容问题（未根治）

实测 yt-dlp 返回 B 站 `thumbnail` 为 `http://i1.hdslb.com/...`，曾判断主因是 https 页面加载 http 图片被浏览器混合内容策略拦截，做了后端 http→https 归一化（`_cover_url()`，详见下文"阶段二修复"）。**但用户反馈本地开发（http 页面，无混合内容限制）仍不显示**，说明这不是根因。

### 阶段二：真正根因 — 图床 Referer 防盗链

用 curl 模拟浏览器行为（带 Referer）逐场景验证图床 `i1.hdslb.com`：

| 请求场景 | 结果 |
|----------|------|
| 无 Referer | 200 ✓ |
| Referer: `localhost:5173`（浏览器加载 `<img>` 时自动携带） | **403 被拒** |
| Referer: `bilibili.com`（平台自身） | 200 ✓ |

**浏览器加载第三方 `<img>` 时自动携带页面自己的 Referer**（`http://localhost:5173`），B 站图床按防盗链策略只放行平台自身域名和无 Referer 请求，第三方站点 Referer 一律 403 → 图片加载失败 → 封面不显示。

此根因同时解释了：之前 curl 直接测 URL 显示 200（无 Referer）而浏览器里始终不显示；以及本地开发同样失败（403 与 http/https 协议无关，与本地/部署环境无关）。国内平台（B 站、抖音、小红书等）图床普遍采用此模式。

### 阶段三：附带发现 — thumbnail 字段缺失无兜底

部分平台 extractor 不设置顶层 `thumbnail`，只有 `thumbnails` 列表（或为空），原实现直接返回 `None`。这是次要问题，一并修复。

### 排除项

后端 → 路由 → `schemas.py` → 前端渲染链路字段传递完整无丢失；测试 mock 数据使用 https 图床且不带真实浏览器 Referer，掩盖了真实引擎 + 真实浏览器的行为差异。

## 如何修复

### 阶段一（后端，2026-08-19）：`backend/downloader.py` 新增 `_cover_url()`

```python
def _cover_url(info: dict[str, Any]) -> str | None:
    """取封面 URL: 优先顶层 thumbnail, 缺失时回退 thumbnails 列表首个.

    国内平台 (B 站等) 缩略图常返回 http:// 链接, 统一升级为 https://:
    https 页面加载 http 图片会被浏览器混合内容策略 (Mixed Content) 拦截,
    封面显示失败. 主流平台图床均支持 https, 升级不会引入新的失败.
    """
    raw = info.get("thumbnail")
    if not raw:
        thumbnails = info.get("thumbnails") or []
        if thumbnails and thumbnails[0].get("url"):
            raw = thumbnails[0]["url"]
    if not raw:
        return None
    if raw.startswith("http://"):
        raw = "https://" + raw[len("http://") :]
    return raw
```

配套测试：`tests/test_downloader.py`（4 个纯函数用例）+ `tests/test_resolve.py` API 层集成用例。**此阶段修复是必要的（部署 https 时必现问题），但不是充分的**。

### 阶段二（前端，2026-08-19）：`<img referrerpolicy="no-referrer">`

核心修复。浏览器加载封面图时不发送 Referer 头 → 图床无 Referer 判定为 200 放行。两处封面渲染点均加：

- `frontend/src/components/ResolveResult.vue`（解析结果卡）
- `frontend/src/components/TaskPanel.vue`（任务列表）

```html
<img v-if="result.cover" :src="result.cover" :alt="result.title"
     loading="lazy" referrerpolicy="no-referrer" />
```

已验证：B 站图床空 Referer 返回 200；YouTube i.ytimg.com 无防盗链，不受影响。

**验证**：前端 `npm run build` 通过；后端全量测试 59 通过。

## 替换方案（如需大变动时）

| 方案 | 说明 | 何时考虑 |
|------|------|----------|
| 后端图片代理 | 后端新增 `/api/cover?url=` 转发图床图片（Python 请求无 Referer 或按平台伪装），前端引用同源地址；需校验域名白名单防 SSRF | 遇到「必须带平台特定 Referer 才放行」的平台时（no-referrer 无法满足），以及未来想给封面加后端缓存时；代价是新增端点 + 安全审查 |
| 页面级 `<meta name="referrer" content="no-referrer">` | 全站所有请求不发 Referer | 影响面过大（内部请求统计/日志丢失来源），不推荐 |
| 保留 http 直出 | 不升级仅依赖浏览器放行 | 不可取：部署 https 后封面必然全部失效 |

> 当前方案（前端 no-referrer + 后端归一化）覆盖主流平台防盗链模式，暂不需要代理。若后续扩展到「必须带 Referer」的平台（少数海外 CDN），再评估后端代理。
