from pathlib import Path

import yaml


def load_prompt(file_path):
    with open(file_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


# 项目根目录
root_path = Path(__file__).parents[2]

# 提示词配置文件路径
prompt_file_path = root_path / "agent" / "prompt" / "prompt.yaml"

# 加载提示词配置文件
prompt_config_content = load_prompt(prompt_file_path)

# agent 配置
agent_config = prompt_config_content["agent"]
