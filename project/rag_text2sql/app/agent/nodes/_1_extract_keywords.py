from jieba.analyse import extract_tags
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.log import logger


async def extract_keywords(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    writer({"stage": "提取关键词"})

    try:
        # 定义词性
        allow_pos = (
            "n",  # 名词: 数据、服务器、表格
            "nr",  # 人名: 张三、李四
            "ns",  # 地名: 北京、上海
            "nt",  # 机构团体名: 政府、学校、某公司
            "nz",  # 其他专有名词: Unicode、哈希算法、诺贝尔奖
            "v",  # 动词: 运行、开发
            "vn",  # 名动词: 工作、研究
            "a",  # 形容词: 美丽、快速
            "an",  # 名形词: 难度、合法性、复杂度
            "eng",  # 英文
            "i",  # 成语
            "l",  # 常用固定短语
        )

        query = state["query"]

        # 提取关键词
        keywords: list[str] = extract_tags(query, topK=20, allowPOS=allow_pos)

        # 避免分词后, 缺失语义, 将问题也添加到提词列表中
        keywords: list[str] = list(set([query, *keywords]))  # noqa: C405

        logger.info(f"关键词提取成功\n{keywords}")

        return {"keywords": keywords}
    except Exception as e:
        logger.error(f"关键词提取失败\n{e!s}")
        raise
