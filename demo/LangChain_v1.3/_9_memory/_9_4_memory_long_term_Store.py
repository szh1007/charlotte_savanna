import os

import dotenv
from langchain.embeddings import init_embeddings
from langgraph.store.memory import InMemoryStore
from langgraph.store.postgres import PostgresStore
from rich import print as rprint

"""
长期记忆: store -> namespace -> key -> value
"""

dotenv.load_dotenv()

embedding_model = init_embeddings(
    model=os.getenv("CLOSEAI_EMBEDDING_MODEL", ""),
    api_key=os.getenv("CLOSEAI_API_KEY", ""),
    base_url=os.getenv("CLOSEAI_BASE_URL", ""),
)

""" InMemoryStore 内存级长期记忆存储 """
store1 = InMemoryStore(
    index={
        "embed": embedding_model,
        "dims": 3072,
        "fields": ["$", "name"],
    }
)

store1.put(namespace=("User",), key="user_1", value={"name": "InMemoryStore-Charlotte"})
item = store1.get(namespace=("User",), key="user_1")
rprint(item)

store1.put(
    namespace=("User",), key="user_1", value={"name": "InMemoryStore-Savanna"}
)  # 覆盖
item = store1.get(namespace=("User",), key="user_1")
rprint(item)

# search
rprint(f"store1 search: {store1.search(('User',))}")  # 根据命名空间检索
rprint(
    f"store1 search: "
    f"{store1.search(('User',), filter={'name': 'InMemoryStore-Charlotte'})}"
)  # 根据内容检索
rprint(f"store1 search: {store1.search(('User',), query='sava')}")  # 根据语义检索


""" InMemoryStore 内存级长期记忆存储 """
PGSQL_URL = (
    f"postgresql://{os.getenv('PGSQL_USERNAME', '')}:{os.getenv('PGSQL_PASSWORD', '')}\
    @{os.getenv('PGSQL_HOST', '')}:{os.getenv('PGSQL_PORT', '')}\
        /{os.getenv('PGSQL_NAME', '')}\
            ?sslmode=disable"
)

with PostgresStore.from_conn_string(PGSQL_URL) as store2:
    store2.setup()

    store2.put(
        namespace=("User",), key="user_1", value={"name": "PostgresStore-Charlotte"}
    )
    item = store2.get(namespace=("User",), key="user_1")
    rprint(item)

    store2.put(
        namespace=("User",), key="user_1", value={"name": "PostgresStore-Savanna"}
    )  # 覆盖
    item = store2.get(namespace=("User",), key="user_1")
    rprint(item)

    # search
    rprint(f"store2 search: {store2.search(('User',))}")  # 根据命名空间检索
    rprint(
        f"store2 search: "
        f"{store2.search(('User',), filter={'name': 'PostgresStore-Charlotte'})}"
    )  # 根据内容检索
    rprint(f"store2 search: {store2.search(('User',), query='sava')}")  # 根据语义检索
