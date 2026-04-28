from openmanus_runtime.llm import (
    LLM,
    normalize_assistant_message_content,
    should_replay_reasoning_content,
)
from openmanus_runtime.schema import Message


class FakeMessage:
    def __init__(self, content, reasoning_content=None):
        self.content = content
        self._reasoning_content = reasoning_content

    def model_dump(self):
        return {"reasoning_content": self._reasoning_content}


def test_normalize_assistant_message_content_extracts_minimax_think_block():
    thinking, visible = normalize_assistant_message_content(
        FakeMessage("<think>hidden reasoning</think>\nvisible answer")
    )

    assert thinking == "hidden reasoning"
    assert visible == "visible answer"


def test_normalize_assistant_message_content_uses_deepseek_reasoning_field():
    thinking, visible = normalize_assistant_message_content(
        FakeMessage("visible answer", reasoning_content="hidden reasoning")
    )

    assert thinking == "hidden reasoning"
    assert visible == "visible answer"


def test_deepseek_message_format_replays_reasoning_content():
    formatted = LLM.format_messages(
        [
            Message.user_message("build app"),
            Message.assistant_message("calling tool", thinking="deepseek hidden reasoning"),
        ],
        include_reasoning_content=True,
    )

    assert formatted[1] == {
        "role": "assistant",
        "content": "calling tool",
        "reasoning_content": "deepseek hidden reasoning",
    }


def test_default_message_format_does_not_replay_reasoning_content():
    formatted = LLM.format_messages(
        [
            Message.user_message("build app"),
            Message.assistant_message("calling tool", thinking="provider-specific reasoning"),
        ]
    )

    assert formatted[1] == {
        "role": "assistant",
        "content": "calling tool",
    }


def test_reasoning_content_replay_is_deepseek_specific():
    assert should_replay_reasoning_content("deepseek-v4-pro") is True
    assert should_replay_reasoning_content("MiniMax-M2.7") is False
    assert should_replay_reasoning_content("mimo-v2.5-pro") is False
