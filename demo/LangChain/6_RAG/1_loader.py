"""文档加载器 loader"""

import warnings

from langchain_community.document_loaders import (
    CSVLoader,
    JSONLoader,
    PyPDFLoader,
    TextLoader,
)

warnings.filterwarnings("ignore", category=DeprecationWarning)

""" txt """
txt1 = TextLoader("./asset/01-langchain-gbk.txt").load()
txt2 = TextLoader(file_path="./asset/01-langchain-utf-8.txt", encoding="utf-8").load()
# for i, line in enumerate(txt1 + txt2):
#     print(i + 1, line.metadata, "\n", line.page_content)

""" pdf """
pdf1 = PyPDFLoader("./asset/02-load.pdf").load()  # 本地pdf文件
pdf2 = PyPDFLoader("https://arxiv.org/pdf/2302.03803").load()  # 网上pdf文件
# for i, page in enumerate(pdf1 + pdf2):
#     print(i + 1, page.metadata, "\n", page.page_content)

""" csv """
csv = CSVLoader(file_path="./asset/03-load.csv").load()
# for i, line in enumerate(csv):
#     print(i + 1, line.metadata, "\n", line.page_content)

""" json """
json1 = JSONLoader(
    file_path="./asset/04-load.json",
    jq_schema=".",  # 加载所有数据
    text_content=False,  # 把数据转换为string
).load()
# print(json1)

json2 = JSONLoader(
    file_path="./asset/04-load.json",
    jq_schema=".messages[].content",  # 加载messages中的content字段
).load()
# for item in json2:
#     print(item.page_content)

json3 = JSONLoader(
    file_path="./asset/04-response.json",
    jq_schema=".data.items[].content",
).load()
# for item in json3:
#     print(item.page_content)

json4 = JSONLoader(
    file_path="./asset/04-response.json",
    jq_schema=".data.items[]",
    content_key='.title + "\n" +.content',
    is_content_key_jq_parsable=True,
).load()
# for item in json4:
#     print(item.page_content)
