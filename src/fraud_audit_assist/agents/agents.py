"""The two agents. The Investigator gathers evidence with tools and writes a Finding; the Reviewer checks it.

Both reply in JSON that code parses and validates against a schema, so a malformed reply is caught here and never
reaches an analyst (ADR-0006).
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from pydantic import BaseModel, ValidationError

from fraud_audit_assist.agents.llm.gateway import LlmGateway
from fraud_audit_assist.agents.prompts import Prompt
from fraud_audit_assist.domain.cases import AgentUsage, Finding, Review
from fraud_audit_assist.errors import LlmError

INVESTIGATOR = "investigator"
REVIEWER = "reviewer"

_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


class ReplyParseError(Exception):
    """The model's reply was not the JSON object its prompt asks for."""


class AgentCallError(Exception):
    """A model call failed mid-agent. Carries the usage of the calls that did return, because they were billed."""

    def __init__(self, cause: LlmError, usage: list[AgentUsage]) -> None:
        super().__init__(str(cause))
        self.usage = usage


@dataclass
class AgentResult:
    usage: list[AgentUsage] = field(default_factory=list)


@dataclass
class InvestigatorResult(AgentResult):
    finding: Finding | None = None
    parse_error: str | None = None


@dataclass
class ReviewerResult:
    review: Review
    usage: list[AgentUsage]


def parse_json_reply[ModelT: BaseModel](
    text: str, schema: type[ModelT], normalise: dict[str, Any] | None = None
) -> ModelT:
    """Parses the first JSON object in a reply, tolerating a Markdown fence around it."""
    match = _JSON_OBJECT.search(text)
    if match is None:
        raise ReplyParseError("the reply contains no JSON object")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as error:
        raise ReplyParseError(f"the reply is not valid JSON: {error.msg}") from error
    if not isinstance(data, dict):
        raise ReplyParseError("the reply is not a JSON object")
    for key, fix in (normalise or {}).items():
        if isinstance(data.get(key), str):
            data[key] = fix(data[key])
    try:
        return schema.model_validate(data)
    except ValidationError as error:
        problems = "; ".join(f"{'.'.join(map(str, e['loc'])) or 'reply'}: {e['msg']}" for e in error.errors())
        raise ReplyParseError(f"the reply does not match the schema: {problems}") from error


class Investigator:
    def __init__(self, gateway: LlmGateway, prompt: Prompt, max_tool_rounds: int) -> None:
        self._gateway = gateway
        self._prompt = prompt
        self._max_tool_rounds = max_tool_rounds

    @property
    def prompt_version(self) -> str:
        return self._prompt.version

    async def investigate(
        self, brief: str, reasons: Sequence[str], tools: Sequence[BaseTool]
    ) -> InvestigatorResult:
        """Runs the tool loop to a Finding. Raises `AgentCallError` on a model failure; a bad reply is a `parse_error`."""
        result = InvestigatorResult()
        try:
            return await self._investigate(result, brief, reasons, tools)
        except LlmError as error:
            raise AgentCallError(error, result.usage) from error

    async def _investigate(
        self, result: InvestigatorResult, brief: str, reasons: Sequence[str], tools: Sequence[BaseTool]
    ) -> InvestigatorResult:
        messages: list[BaseMessage] = [
            SystemMessage(self._prompt.text),
            HumanMessage(_with_reasons(brief, reasons)),
        ]
        by_name = {t.name: t for t in tools}
        reply: AIMessage | None = None
        for _ in range(self._max_tool_rounds):
            response = await self._gateway.chat(INVESTIGATOR, self._prompt.version, messages, tools)
            result.usage.append(response.usage)
            reply = response.message
            messages.append(reply)
            if not reply.tool_calls:
                break
            for call in reply.tool_calls:
                messages.append(await _run_tool(by_name, call))
        else:
            # The model kept calling tools: one last call without tools forces an answer from what it has.
            messages.append(
                HumanMessage("You have used every tool call allowed. Reply now with the Finding JSON.")
            )
            response = await self._gateway.chat(INVESTIGATOR, self._prompt.version, messages)
            result.usage.append(response.usage)
            reply = response.message

        try:
            # Models often vary the case of an enum ("medium"); that is not worth a retry.
            result.finding = parse_json_reply(_text(reply), Finding, {"confidence": str.capitalize})
        except ReplyParseError as error:
            result.parse_error = str(error)
        return result


class Reviewer:
    def __init__(self, gateway: LlmGateway, prompt: Prompt) -> None:
        self._gateway = gateway
        self._prompt = prompt

    @property
    def prompt_version(self) -> str:
        return self._prompt.version

    async def review(self, finding: Finding, cited_lines: Sequence[str]) -> ReviewerResult:
        """Raises `AgentCallError` on a model failure and `ReplyParseError` on an unreadable review."""
        evidence = "\n".join(cited_lines) or "(none of the cited refs exist)"
        messages: list[BaseMessage] = [
            SystemMessage(self._prompt.text),
            HumanMessage(f"Finding:\n{finding.model_dump_json(indent=2)}\n\nCited evidence:\n{evidence}"),
        ]
        try:
            response = await self._gateway.chat(REVIEWER, self._prompt.version, messages)
        except LlmError as error:
            raise AgentCallError(error, []) from error
        return ReviewerResult(
            review=parse_json_reply(_text(response.message), Review), usage=[response.usage]
        )


async def _run_tool(tools: dict[str, BaseTool], call: Any) -> ToolMessage:
    tool = tools.get(call["name"])
    if tool is None:
        # The model may invent a tool name; telling it so lets it recover within the loop.
        return ToolMessage(
            f"Unknown tool '{call['name']}'. Available: {', '.join(tools)}.", tool_call_id=call["id"]
        )
    try:
        output = await tool.ainvoke(call["args"])
    except ValidationError as error:
        return ToolMessage(
            f"Invalid arguments for {call['name']}: {error.errors()[0]['msg']}", tool_call_id=call["id"]
        )
    return ToolMessage(str(output), tool_call_id=call["id"])


def _with_reasons(brief: str, reasons: Sequence[str]) -> str:
    if not reasons:
        return brief
    listed = "\n".join(f"- {r}" for r in reasons)
    return f"{brief}\n\nYour previous Finding was rejected. Address each reason:\n{listed}"


def _text(message: AIMessage | None) -> str:
    if message is None:
        return ""
    content = message.content
    if isinstance(content, str):
        return content
    # Some providers return a list of content blocks; only the text blocks carry the answer.
    return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
