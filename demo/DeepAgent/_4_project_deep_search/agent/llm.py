import dotenv
from langchain.chat_models import init_chat_model

dotenv.load_dotenv(override=True)

model = init_chat_model(
    "deepseek:deepseek-v4-pro", extra_body={"thinking": {"type": "disabled"}}
)
