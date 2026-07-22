import os

from langchain_community.document_loaders import (
    CSVLoader,
    Docx2txtLoader,
    JSONLoader,
    PyPDFLoader,
    TextLoader,
)
from rich import print as rprint

load_dir = os.path.join(os.path.dirname(__file__), "load")

""" txt """
txt_docs = TextLoader(os.path.join(load_dir, "sample.txt"), encoding="utf-8").load()
# rprint(txt_docs)

""" csv """
csv_docs = CSVLoader(os.path.join(load_dir, "sample.csv"), encoding="utf-8").load()
# rprint(csv_docs)

""" json """
json_docs1 = JSONLoader(os.path.join(load_dir, "sample.json"), jq_schema=".[]", text_content=False).load()
# rprint(json_docs1)
# json_docs2 = JSONLoader(json_fp, jq_schema=".[].skills", text_content=False).load()
# rprint(json_docs2)

""" pdf """
pdf_docs = PyPDFLoader(os.path.join(load_dir, "sample.pdf"), extraction_mode="plain").load()
# rprint(pdf_docs)

""" word (使用 Docx2txtLoader 替代 UnstructuredWordDocumentLoader, 避免 segfault) """
word_docs = Docx2txtLoader(os.path.join(load_dir, "sample.docx")).load()
# rprint(word_docs)

""" md (md 是纯文本, 直接用 TextLoader 即可, 避免 segfault) """
md_docs = TextLoader(os.path.join(load_dir, "sample.md"), encoding="utf-8").load()
rprint(md_docs)
