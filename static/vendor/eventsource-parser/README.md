# vendor: eventsource-parser

第三方库的本地副本，供 `templates/minimall/agent.html` 使用。

| 项 | 值 |
|----|-----|
| 包 | `eventsource-parser` |
| 版本 | 3.1.1 |
| 来源 | `https://unpkg.com/eventsource-parser@3.1.1/dist/{index.js,stream.js}` |
| 许可 | MIT（见 `LICENSE`） |

## 为什么放本地而不走 CDN

商城其它页面（htmx / Alpine，见 `templates/minimall/base.html`）走 CDN 是合理的：它们属于
**渐进增强**，加载失败顶多少点交互，页面主体照样能读能点。

这个库不是同一类东西 —— 它是客服页那条链路的解析层，而且页面把「拦住表单提交」也挂在
同一个 `<script type="module">` 里。ES module 的语义是**任何一个 `import` 失败，整个模块
一行都不执行**，于是 CDN 一断，表单会静默退回浏览器原生提交，地址栏被刷成 `/agent/?`，
从用户视角看就是「按了发送没反应」（详见 `agent.html` 文件头的说明）。

本地化之后，加载只发生在 Django 的 staticfiles 里，不再有外部依赖。

## 怎么更新

```bash
V=3.1.1   # 换成目标版本
for f in index.js stream.js index.js.map stream.js.map; do
  curl -fsSL -o "$f" "https://unpkg.com/eventsource-parser@$V/dist/$f"
done
curl -fsSL -o LICENSE "https://unpkg.com/eventsource-parser@$V/LICENSE"
```

更新后拿 `https://unpkg.com/eventsource-parser@<版本>/?meta` 返回的 `integrity` 与文件大小
核对一遍。

注意 `stream.js` 用相对路径 `./index.js` 导入，两个文件必须留在同一目录。
