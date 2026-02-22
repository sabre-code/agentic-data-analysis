"""
Gemini client — wraps google-genai SDK for gemini-2.5-flash.

Supports:
  - Async content generation (single response)
  - Async streaming generation (token-by-token)
  - Function/tool calling (agent dispatch loop)
"""
from __future__ import annotations

import logging
from typing import Any, AsyncGenerator

from google import genai
from google.genai import types

from app.config import get_settings

logger = logging.getLogger(__name__)


class GeminiClient:
    def __init__(self) -> None:
        settings = get_settings()
        self._client = genai.Client(api_key=settings.GEMINI_API_KEY)
        self._model = settings.GEMINI_MODEL

    # ── Single-shot generation ─────────────────────────────────────────────

    async def generate(
        self,
        messages: list[dict[str, Any]],
        system_prompt: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.2,
    ) -> types.GenerateContentResponse:
        """
        Single generation call. Returns full response.
        Use for orchestrator routing and agent code generation.
        """
        config = types.GenerateContentConfig(
            temperature=temperature,
            tools=self._build_tools(tools) if tools else None,
            system_instruction=system_prompt,
        )

        contents = self._build_contents(messages)

        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=contents,
            config=config,
        )
        return response

    # ── Streaming generation ───────────────────────────────────────────────

    async def stream(
        self,
        messages: list[dict[str, Any]],
        system_prompt: str | None = None,
        temperature: float = 0.7,
    ) -> AsyncGenerator[str, None]:
        """
        Streaming generation — yields text chunks as they arrive.
        Use for final presentation / summary streaming to the frontend.
        """
        config = types.GenerateContentConfig(
            temperature=temperature,
            system_instruction=system_prompt,
        )

        contents = self._build_contents(messages)

        async for chunk in await self._client.aio.models.generate_content_stream(
            model=self._model,
            contents=contents,
            config=config,
        ):
            if chunk.text:
                yield chunk.text

    # ── Function calling loop ─────────────────────────────────────────────

    async def run_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_executor: "ToolExecutor",
        system_prompt: str | None = None,
        max_iterations: int = 10,
    ) -> tuple[str, list[dict[str, Any]]]:
        """
        Agentic function-calling loop:
          1. Send messages + tool declarations to Gemini
          2. If model returns FunctionCall → execute via tool_executor
          3. Feed FunctionResponse back into conversation
          4. Loop until model returns plain text (no more tool calls)

        Returns: (final_text_response, updated_messages_with_tool_turns)

        This is the core of the Orchestrator — Gemini itself decides which
        agents to call, in what order, with what arguments. We just execute.
        """
        config = types.GenerateContentConfig(
            temperature=0.2,
            tools=self._build_tools(tools),
            system_instruction=system_prompt,
            # Allow model to choose tools automatically
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(
                    mode="AUTO"
                )
            ),
        )

        # Work on a copy so we don't mutate the caller's history
        working_messages = list(messages)

        for iteration in range(max_iterations):
            contents = self._build_contents(working_messages)
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=contents,
                config=config,
            )

            # Extract function calls from response
            function_calls = self._extract_function_calls(response)

            if not function_calls:
                # Model returned plain text — we're done
                final_text = response.text or ""
                return final_text, working_messages

            # Execute all function calls (model may request multiple in one turn)
            function_responses = []
            for fc in function_calls:
                logger.info(
                    "Gemini function call: %s args=%s iteration=%d",
                    fc["name"], fc["args"], iteration
                )
                result = await tool_executor.execute(fc["name"], fc["args"])
                function_responses.append({
                    "name": fc["name"],
                    "response": result,
                    "id": fc.get("id"),
                })

            # Add model's function call turn + our function responses to history
            working_messages.append({
                "role": "model",
                "parts": [{"function_call": fc} for fc in function_calls],
            })
            working_messages.append({
                "role": "user",
                "parts": [
                    {"function_response": {"name": fr["name"], "response": fr["response"]}}
                    for fr in function_responses
                ],
            })

        # Exhausted max iterations — ask for a final answer
        logger.warning("Tool loop exhausted %d iterations, requesting final answer", max_iterations)
        working_messages.append({
            "role": "user",
            "parts": [{"text": "Please provide your final answer based on the information gathered."}],
        })
        final_response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=self._build_contents(working_messages),
            config=types.GenerateContentConfig(temperature=0.3, system_instruction=system_prompt),
        )
        return final_response.text or "", working_messages

    # ── Private helpers ───────────────────────────────────────────────────

    def _build_contents(self, messages: list[dict[str, Any]]) -> list[types.Content]:
        """Convert our internal message format to google-genai Content objects."""
        contents = []
        for msg in messages:
            role = msg.get("role", "user")
            # Map our roles to Gemini roles
            gemini_role = "model" if role in ("assistant", "model") else "user"

            parts = msg.get("parts")
            if parts:
                # Already in Gemini parts format (function call/response turns)
                built_parts = []
                for part in parts:
                    if "text" in part:
                        built_parts.append(types.Part(text=part["text"]))
                    elif "function_call" in part:
                        fc = part["function_call"]
                        built_parts.append(types.Part(
                            function_call=types.FunctionCall(
                                name=fc["name"],
                                args=fc.get("args", {}),
                                id=fc.get("id"),
                            )
                        ))
                    elif "function_response" in part:
                        fr = part["function_response"]
                        built_parts.append(types.Part(
                            function_response=types.FunctionResponse(
                                name=fr["name"],
                                response=fr["response"],
                                id=fr.get("id"),
                            )
                        ))
                contents.append(types.Content(role=gemini_role, parts=built_parts))
            else:
                # Simple text message
                content_text = msg.get("content", "")
                if content_text:
                    contents.append(types.Content(
                        role=gemini_role,
                        parts=[types.Part(text=content_text)],
                    ))
        return contents

    def _build_tools(self, tool_specs: list[dict[str, Any]]) -> list[types.Tool]:
        """Convert our tool declaration dicts to google-genai Tool objects."""
        function_declarations = []
        for spec in tool_specs:
            fd = types.FunctionDeclaration(
                name=spec["name"],
                description=spec["description"],
                parameters=spec.get("parameters"),
            )
            function_declarations.append(fd)
        return [types.Tool(function_declarations=function_declarations)]

    def _extract_function_calls(
        self, response: types.GenerateContentResponse
    ) -> list[dict[str, Any]]:
        """Extract all FunctionCall parts from a response."""
        calls = []
        if not response.candidates:
            return calls
        for candidate in response.candidates:
            if not candidate.content or not candidate.content.parts:
                continue
            for part in candidate.content.parts:
                if part.function_call:
                    fc = part.function_call
                    calls.append({
                        "name": fc.name,
                        "args": dict(fc.args) if fc.args else {},
                        "id": getattr(fc, "id", None),
                    })
        return calls


# ── Tool executor protocol ─────────────────────────────────────────────────

class ToolExecutor:
    """
    Protocol for tool execution. The Orchestrator implements this
    by mapping tool names → agent.run() calls.
    """
    async def execute(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError
