import dotenv
from langchain.agents import create_agent
from langchain.chat_models import init_chat_model

dotenv.load_dotenv()

model = init_chat_model("deepseek:deepseek-v4-pro", extra_body={"thinking": {"type": "disabled"}})

""" 各种 hook 在流程中的执行顺序 """
# before (before_model、before_agent)
# 执行顺序与传递给 agent 的顺序【一致】

# after (after_model、after_agent)
# 执行顺序与传递给 agent 的顺序【相反】

# wrap (wrap_model_call、wrap_tool_call)
# 有前后包裹结构, 先传递的包裹在外层, 即 1 - 2 - 3 - ... - 3 - 2 - 1

agent = create_agent(
    name="agent_assistant",
    model=model,
    middleware=[
        # before_1
        # before_2
        # before_3
        # after_1
        # after_2
        # after_3
        # wrap_1
        # wrap_2
        # wrap_3
    ],
)

# 调用前: before_1      -> before_2     -> before_3     -> wrap_1_before -> wrap_2_before -> wrap_3_before
# 调用后: wrap_3_after  -> wrap_2_after -> wrap_1_after -> after_3       -> after_2       -> after_1
