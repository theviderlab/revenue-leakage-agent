import pytest
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool

from tests.fake_llm import ScriptedLLM, ai, call


@tool
def echo(text: str) -> str:
    """Echo the text back."""
    return text


def test_scripted_llm_replays_steps_in_order() -> None:
    llm = ScriptedLLM().queue(ai(call("echo", text="hi")), ai(text="done"))
    bound = llm.bind_tools([echo])

    first = bound.invoke([HumanMessage("say hi")])
    assert first.tool_calls[0]["name"] == "echo"
    assert first.tool_calls[0]["args"] == {"text": "hi"}

    second = bound.invoke([HumanMessage("again")])
    assert second.content == "done"
    assert len(llm.seen) == 2

    with pytest.raises(AssertionError):
        bound.invoke([HumanMessage("one more")])
