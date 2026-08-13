"""调用本地大模型 Ollama

1.下载 Ollama: https://ollama.com/download
2.在 Ollama 中下载模型 Models, 例如 ollama run deepseek-r1:7b
"""

from langchain_ollama import ChatOllama

model = ChatOllama(model="deepseek-r1:7b")
