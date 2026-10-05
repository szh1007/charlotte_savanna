"""构建 rag_knowledge 的评测语料 (C17).

十份**政府公开文本** -> 十篇 md, 落到 `assets/eval_corpus/`。

为什么是政府文本: 语料要干净 —— 法规是"为阅读而发布的纯文本", 没有站点容器语法、
没有外链代码、没有 HTML 组件、没有图片, 而且依《著作权法》第五条不适用著作权法。
官方文档站的 md 恰恰相反 (它是给渲染器写的中间产物), 试过一轮, 清洗完仍然勉强。

语料是**派生数据**: 直接提交成品的话, 没人知道它从哪来、清洗规则是什么、
下次怎么重建。脚本 + `SOURCES.md` = 可复现。

用法 (无需参数, 原文由脚本自己抓并缓存到系统临时目录):

```bash
python scripts/build_eval_corpus.py
```
"""

from __future__ import annotations

import argparse
import html
import re
import shutil
import tempfile
from pathlib import Path
from urllib.request import Request, urlopen

# ---------------------------------------------------------------- 切割

LAW_ARTICLE = re.compile(r"^第[一二三四五六七八九十百零]+条[ 　]", re.M)
# 末条的收尾句. 两种写法都要认: 「自2021年9月1日起施行」与「自公布之日起施行」
LAW_BODY_END = re.compile(r"自[^。]{0,16}?起施行。")
# 「第一章 总则」这类章/节标题单独成行 -> markdown 标题
CHAPTER = re.compile(r"^\s*(第[一二三四五六七八九十]+章\s+.*)$", re.M)
SECTION = re.compile(r"^\s*(第[一二三四五六七八九十]+节\s+.*)$", re.M)

# 效力层级从高到低: 法律 -> 行政法规 -> 部门规章. 同一层级内按主题聚拢
# (数据出境那三件是一组: 评估办法 / 标准合同 / 跨境流动规定).
LAWS = [
    (
        "中华人民共和国网络安全法.md",
        "https://www.cac.gov.cn/2025-12/29/c_1768735112911946.htm",
        "中华人民共和国网络安全法",
    ),
    (
        "中华人民共和国数据安全法.md",
        "https://www.cac.gov.cn/2021-06/11/c_1624994566919140.htm",
        "中华人民共和国数据安全法",
    ),
    (
        "中华人民共和国个人信息保护法.md",
        "http://www.npc.gov.cn/npc/c2/c30834/202108/t20210820_313088.html",
        "中华人民共和国个人信息保护法",
    ),
    (
        "关键信息基础设施安全保护条例.md",
        "https://www.gov.cn/zhengce/content/2021-08/17/content_5631671.htm",
        "关键信息基础设施安全保护条例",
    ),
    (
        "网络数据安全管理条例.md",
        "https://www.gov.cn/zhengce/content/202409/content_6977766.htm",
        "网络数据安全管理条例",
    ),
    (
        "网络安全审查办法.md",
        "https://www.cac.gov.cn/2022-01/04/c_1642894602182845.htm",
        "网络安全审查办法",
    ),
    (
        "数据出境安全评估办法.md",
        "https://www.gov.cn/zhengce/2022-07/07/content_5728937.htm",
        "数据出境安全评估办法",
    ),
    (
        "个人信息出境标准合同办法.md",
        "https://www.gov.cn/zhengce/202311/content_6917770.htm",
        "个人信息出境标准合同办法",
    ),
    (
        "促进和规范数据跨境流动规定.md",
        "https://www.cac.gov.cn/2024-03/22/c_1712776611775634.htm",
        "促进和规范数据跨境流动规定",
    ),
    (
        "生成式人工智能服务管理暂行办法.md",
        "https://www.cac.gov.cn/2023-07/13/c_1690898327029107.htm",
        "生成式人工智能服务管理暂行办法",
    ),
    (
        "未成年人网络保护条例.md",
        "https://www.cac.gov.cn/2023-10/24/c_1699806932316206.htm",
        "未成年人网络保护条例",
    ),
    (
        "互联网信息服务算法推荐管理规定.md",
        "https://www.cac.gov.cn/2022-01/04/c_1642894606364259.htm",
        "互联网信息服务算法推荐管理规定",
    ),
    (
        "网络暴力信息治理规定.md",
        # 主源 (中国网信网) 的直链没检索到, 用广东省公安厅的转载页 —— 政府门户,
        # 条文与网信办发布版一致. 换成主源只需改这一行.
        "https://gdga.gd.gov.cn/xxgk/zcwj/content/post_4502664.html",
        "网络暴力信息治理规定",
    ),
    (
        "个人信息保护合规审计管理办法.md",
        "https://www.cac.gov.cn/2025-02/14/c_1741233507681519.htm",
        "个人信息保护合规审计管理办法",
    ),
    (
        "中华人民共和国密码法.md",
        # 国家密码管理局 (该法的主管部门) 发布的全文
        "https://www.sca.gov.cn/sca/xwdt/2019-10/28/content_1057251.shtml",
        "中华人民共和国密码法",
    ),
]


def _tidy(text: str) -> str:
    """收尾: 去行尾空白, 折叠三连空行."""
    text = re.sub(r"[ \t]+$", "", text, flags=re.M)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def clean_law(raw_html: str, name: str) -> str:
    """从政府网页里抠出法规正文, 转成 md.

    页面里混着导航, 目录与页脚 (版权 / ICP 备案 / 编辑署名), 三处都要切掉:
    - 头: 从**精确等于文号名**的那一行起 (页面 <title> 带站点后缀, 靠它定位会
      把导航和目录一起带进来), 中间只留注明通过日期的括号行;
    - 尾: 用末条的「自……起施行。」收尾 —— 它必定是全篇最后一句.
    """
    text = re.sub(r"<script.*?</script>", "", raw_html, flags=re.S)
    text = re.sub(r"<style.*?</style>", "", text, flags=re.S)
    text = re.sub(r"<p[^>]*>", "\n", text)
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = text.replace("　", " ").replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]

    first = next(i for i, ln in enumerate(lines) if LAW_ARTICLE.match(ln))
    # 标题行以**精确匹配**为主; 个别发布页的标题里有错字 (实测国家密码管理局那页
    # 把「中华人民共和国密码法」写成了「中国人民共和国密码法」), 于是退一步:
    # 认「不含书名号 + 长度接近法名 + 含法名末尾三字」的那一行. 取最后一个 ——
    # 前面的目录与令文都可能沾边, 离第一条最近的那个才是正文标题.
    head_at = max(
        (
            i
            for i, ln in enumerate(lines[:first])
            if ln == name
            or ("《" not in ln and name[-3:] in ln and len(ln) <= len(name) + 4)
        ),
        default=None,
    )
    if head_at is None:
        raise ValueError(f"定位不到标题行: {name}")
    passed = next(
        # 全角括号是原文的 —— 法规里注明通过日期的那一行就以它开头
        (ln for ln in lines[head_at + 1 : first] if ln.startswith("（")),  # noqa: RUF001
        "",
    )
    # 正文从**第一条之前最后一个章标题**起 —— 目录里也有一串同名的章标题,
    # 取最后一个才是真的那一个 (目录在前, 正文在后).
    chapters = [
        i
        for i, ln in enumerate(lines[:first])
        if CHAPTER.match(ln) or SECTION.match(ln)
    ]
    body_at = chapters[-1] if chapters else first
    end = next(
        (i for i, ln in enumerate(lines[first:], first) if LAW_BODY_END.search(ln)),
        len(lines) - 1,
    )

    head = [f"# {name}"]
    if passed:
        head += ["", passed]
    body = "\n\n".join(lines[body_at : end + 1])
    body = CHAPTER.sub(r"## \1", body)
    body = SECTION.sub(r"### \1", body)
    return _tidy("\n".join([*head, "", body]))


# ---------------------------------------------------------------- main


def _fetch(url: str, cache: Path, name: str) -> str:
    """取原文页面, 缓存到临时目录 (页面本身不入库: 它带站点导航与页脚)."""
    page = cache / f"{name}.html"
    if not page.exists():
        # 政府站点对没有 UA 的请求直接 403
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        page.write_bytes(urlopen(req, timeout=30).read())
    return page.read_text(encoding="utf-8", errors="replace")


def build(out: Path, cache: Path) -> None:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    cache.mkdir(parents=True, exist_ok=True)

    for name, url, doc_name in LAWS:
        text = clean_law(_fetch(url, cache, doc_name), doc_name)
        (out / name).write_text(text, encoding="utf-8")
        articles = len(LAW_ARTICLE.findall(text))
        chapters = len(re.findall(r"^## ", text, flags=re.M))
        print(f"{name}: {len(text)} 字, {articles} 条, {chapters} 章")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="构建评测语料 (C17)")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "assets/eval_corpus",
        help="语料输出目录",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=Path(tempfile.gettempdir()) / "rag_knowledge_eval_corpus",
        help="原文页面缓存目录",
    )
    args = parser.parse_args()
    build(args.out, args.cache)
