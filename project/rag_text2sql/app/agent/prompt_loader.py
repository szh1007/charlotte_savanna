import asyncio
from pathlib import Path


async def load_prompt(name: str):
    prompt_path = Path(__file__).parents[2] / "prompts" / f"{name}.prompt"
    return prompt_path.read_text(encoding="utf-8")


if __name__ == "__main__":

    async def test():
        print(await load_prompt("generate_sql"))

    asyncio.run(test())
