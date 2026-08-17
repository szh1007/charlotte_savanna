import os

import dotenv
from langchain.chat_models import init_chat_model

dotenv.load_dotenv(override=True)

model = init_chat_model(
    model=os.getenv("DEEPSEEK_MODEL_NAME", ""),
    extra_body={"thinking": {"type": "disabled"}},
)
