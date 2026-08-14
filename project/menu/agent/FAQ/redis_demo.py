from redis import Redis
from rich import print as rprint

client = Redis.from_url("redis://localhost:6379/0", decode_responses=True)

"""
基本操作
"""
# # string
# client.set("name", "charlotte")
# rprint(client.get("name"))

# # hashmap
# client.hset(
#     "faq:items:address:test",
#     mapping={"quetions": "餐厅地址", "answer": "TEST_ADDRESS"},
# )
# rprint(client.hgetall("faq:items:address:test"))

# # set
# client.sadd("faq:items", "address", "phone", "email")
# rprint(client.smembers("faq:items"))

"""
pipeline
"""
pipeline = client.pipeline()
pipeline.set("name", "charlotte")
pipeline.hset(
    "faq:items:address:test",
    mapping={"quetions": "餐厅地址", "answer": "TEST_ADDRESS"},
)
client.sadd("faq:items", "address", "phone", "email")
results = pipeline.execute()
rprint(results)
