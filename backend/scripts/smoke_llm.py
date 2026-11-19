"""Tool-calling round trip against the configured LLM. Run with `make smoke-llm`."""

import sys

from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import tool

from app.config import settings
from app.llm import get_llm


@tool
def multiply(a: int, b: int) -> int:
    """Multiply two integers."""
    return a * b


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    sys.exit(1)


def main() -> None:
    print(f"model: {settings.llm_model}")
    print(f"base_url: {settings.llm_base_url}")
    llm = get_llm().bind_tools([multiply])
    messages = [HumanMessage("What is 1234 times 5678? Use the tool.")]

    first = llm.invoke(messages)
    if not first.tool_calls:
        fail(f"no tool call; got: {first.content!r}")
    messages.append(first)
    for call in first.tool_calls:
        result = multiply.invoke(call["args"])
        messages.append(ToolMessage(str(result), tool_call_id=call["id"]))

    final = llm.invoke(messages)
    print(f"content type: {type(final.content).__name__}")
    print(f"answer: {final.content}")
    if not isinstance(final.content, str):
        fail("content is not a string")
    if "7006652" not in final.content and "7,006,652" not in final.content:
        fail("answer does not contain 7006652")
    print("OK")


if __name__ == "__main__":
    main()
