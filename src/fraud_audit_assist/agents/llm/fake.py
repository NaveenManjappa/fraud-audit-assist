"""A free, deterministic chat model for development, demos and tests. No network, no key, no spend.

Without a script it plays each agent from the `# Role:` line its prompt starts with: an Investigator that calls two
tools and then writes a Finding citing real refs, and a Reviewer that approves. With a script it replies with the
given messages in order, which is how tests drive every workflow path.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import PrivateAttr

_ROLE = re.compile(r"^# Role: (\w+)", re.MULTILINE)
_VERDICTS = re.compile(r"^Allowed verdicts: (.+)$", re.MULTILINE)
_ACTIONS = re.compile(r"^Allowed recommendations: (.+)$", re.MULTILINE)
_BAND = re.compile(r"Risk Band: (\w+)")
_EVENT_REF = re.compile(r"\bE-\d+\b")

ScriptItem = AIMessage | str | Exception


class ScriptedChatModel(BaseChatModel):
    model_name: str = "scripted-fake"

    _script: list[ScriptItem] = PrivateAttr(default_factory=list)
    _calls: list[list[BaseMessage]] = PrivateAttr(default_factory=list)

    def __init__(self, script: Sequence[ScriptItem] | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._script = list(script or [])

    @property
    def _llm_type(self) -> str:
        return "scripted-fake"

    @property
    def calls(self) -> list[list[BaseMessage]]:
        """Every message list the model was called with, for assertions in tests."""
        return self._calls

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Any | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, AIMessage]:
        return self.bind(tools=[convert_to_openai_tool(t) for t in tools], **kwargs)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        self._calls.append(list(messages))
        if self._script:
            item = self._script.pop(0)
            if isinstance(item, Exception):
                raise item
            message = AIMessage(content=item) if isinstance(item, str) else item
        else:
            message = self._play(messages, bool(kwargs.get("tools")))
        if message.usage_metadata is None:
            prompt_chars = sum(len(str(m.content)) for m in messages)
            output_chars = len(str(message.content)) + len(json.dumps(message.tool_calls))
            message = message.model_copy(
                update={
                    "usage_metadata": {
                        "input_tokens": prompt_chars // 4,
                        "output_tokens": output_chars // 4,
                        "total_tokens": (prompt_chars + output_chars) // 4,
                    }
                }
            )
        return ChatResult(generations=[ChatGeneration(message=message)])

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return self._generate(messages, stop, None, **kwargs)

    def _play(self, messages: list[BaseMessage], has_tools: bool) -> AIMessage:
        system = next((str(m.content) for m in messages if isinstance(m, SystemMessage)), "")
        role = match.group(1) if (match := _ROLE.search(system)) else ""
        if role == "investigator":
            return self._investigator(system, messages, has_tools)
        if role == "reviewer":
            return AIMessage(content=json.dumps({"approved": True, "reasons": []}))
        return AIMessage(content="OK")

    @staticmethod
    def _investigator(system: str, messages: list[BaseMessage], has_tools: bool) -> AIMessage:
        tool_results = [m for m in messages if isinstance(m, ToolMessage)]
        if has_tools and not tool_results:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "query_graph_neighbourhood",
                        "args": {},
                        "id": "fake-call-1",
                        "type": "tool_call",
                    },
                    {"name": "get_customer_baseline", "args": {}, "id": "fake-call-2", "type": "tool_call"},
                ],
            )
        brief = str(messages[1].content) if len(messages) > 1 else ""
        verdicts = [v.strip() for v in _first_group(_VERDICTS, system, "Inconclusive").split(",")]
        actions = [a.strip() for a in _first_group(_ACTIONS, system, "NoAction").split(",")]
        band = _first_group(_BAND, brief, "Low")
        fraud_lines = [
            line.split()[0]
            for message in tool_results
            for line in str(message.content).splitlines()
            if "Confirmed Fraud" in line and line.startswith("G-")
        ]
        likely_fraud = band in ("High", "Critical")
        restrictive = [a.removesuffix(" (restrictive)") for a in actions if a.endswith("(restrictive)")]
        gentle = [a for a in actions if not a.endswith("(restrictive)")]
        refs = sorted(set(_EVENT_REF.findall(brief)), key=lambda r: int(r[2:]))
        finding = {
            "verdict": verdicts[0] if likely_fraud else "Inconclusive",
            "confidence": "Medium",
            "summary": (
                f"Scripted Finding from the fake provider, not a real assessment: {len(refs)} flagged events"
                + (", and a shared identifier with a Confirmed Fraud Account." if fraud_lines else ".")
            ),
            "recommendation": (restrictive or gentle)[0] if likely_fraud else gentle[0],
            "evidence": refs + fraud_lines,
        }
        return AIMessage(content=json.dumps(finding))


def _first_group(pattern: re.Pattern[str], text: str, default: str) -> str:
    match = pattern.search(text)
    return match.group(1) if match else default
