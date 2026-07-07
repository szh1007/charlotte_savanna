import os, dotenv, warnings, ast

dotenv.load_dotenv()
warnings.filterwarnings("ignore", category=DeprecationWarning)

from typing import Any
from langchain_classic.chains.sql_database.query import create_sql_query_chain
from langchain_community.utilities import SQLDatabase
from langchain_openai import ChatOpenAI

# mysql
username = os.getenv("DB_USERNAME", "")
password = os.getenv("DB_PASSWORD", "")
host = os.getenv("DB_HOST", "")
port = os.getenv("DB_PORT", "")
name = os.getenv("DB_NAME", "")
db = SQLDatabase.from_uri(f"mysql+pymysql://{username}:{password}@{host}:{port}/{name}")

# llm
os.environ["OPENAI_BASE_URL"] = os.getenv("OPENAI_BASE_URL", "")
os.environ["OPENAI_API_KEY"] = os.getenv("OPENAI_API_KEY", "")
model = ChatOpenAI(model="gpt-4o-mini")

# sql chain
chain = create_sql_query_chain(model, db)
raw_sql = chain.invoke({"question": "数据库中有多少个表, 每个表中有多少条记录"})
print(raw_sql)
print("-" * 100)

# result
sql = (
    raw_sql
    .replace("SQLQuery: ", "")
    .replace("sql", "")
    .replace("`", "")
    .strip()
)
raw_result: Any = db.run(sql)
result = ast.literal_eval(raw_result)
for key, value in result:
    print(f"{key}: {value}")
