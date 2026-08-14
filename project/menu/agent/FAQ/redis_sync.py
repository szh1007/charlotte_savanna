"""
将 FAQ 的数据, 导入到 redis 中, 用于后续快速查询
"""

from redis import Redis
from rich import print as rprint

FAQ_ITEMS = [
    {
        "id": "address",
        "question": "餐厅地址",
        "answer": "深圳市南山区万象天地A201, 欢迎您的到来",
    },
    {
        "id": "phone",
        "question": "餐厅电话",
        "answer": "010-66666666, 欢迎随时联系咨询店铺",
    },
    {
        "id": "work_time",
        "question": "营业时间",
        "answer": "周一至周五 12:00-21:00\n周六至周日 10:00-23:00\n欢迎随时光临",
    },
]

# 创建 client 实例
client = Redis.from_url("redis://localhost:6379/0", decode_responses=True)
pipeline = client.pipeline()

# 数据写入 redis

# 用户输入 query 时, 把所有 FAQ 取出与 query 进行相似度计算, 去除相似度最高的 topk 结果
# 1.faq_keys = client.keys("faq:items:*"): .keys() 资源占用大, 数据量很大时不建议使用
# 2.把所有的 key 以set的形式储存在 redis 中, 用于后续快速查询 (推荐)
#   后续每次新增一个 faq item 时, 就需要向这个 set 中添加一个元素
key_list = []
for items in FAQ_ITEMS:
    key = f"faq:items:{items['id']}"
    client.hset(key, mapping={"question": items["question"], "answer": items["answer"]})
    key_list.append(key)

pipeline.sadd("faq:all_items", *key_list)
result = pipeline.execute()

all_faq_items = client.smembers("faq:all_items")
rprint(all_faq_items)
