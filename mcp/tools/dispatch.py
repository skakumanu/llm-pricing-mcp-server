"""MCP Tool: run a chat request through the dispatch gateway (v1.80.0).

A thin transport adapter over src/services/dispatch_entry.prepare_and_dispatch — the same
pipeline the HTTP dispatch branch of POST /v1/chat/completions uses — so eligibility,
objectives, cost cap, budget hard stop, failover, shadow and every ledger write behave
exactly as on HTTP. The tool only:

- acts for the authenticated customer on the MCP connection (never an identity named in
  the arguments; ToolManager refuses foreign org_id values before execute runs);
- builds an HTTP-equivalent body from an allow-list of argument keys and validates it
  through the same ChatCompletionsRequest model HTTP uses;
- uses the org's stored provider keys only (an empty header resolver: provider keys are
  never tool arguments);
- turns every refusal into a structured ``success: false`` result with HTTP's error code
  and payload, and never echoes request content, a validation ``input``/``ctx`` or an
  unexpected exception's message.

It logs nothing. This module deliberately imports nothing from ``src.main``.
"""
from typing import Any, Dict, Optional

from pydantic import ValidationError

from mcp.caller import CUSTOMER_ONLY, McpCaller
from src.models.dispatch import ChatCompletionsRequest
from src.services.dispatch_entry import (
    DispatchCustomer, default_deps, dispatch_requires_api_key_payload, prepare_and_dispatch,
)
from src.services.dispatch_executors import HeaderKeyResolver
from src.services.dispatch_service import DispatchError

# Argument keys copied verbatim into the HTTP-equivalent body (besides prompt/system/
# messages, model and dispatch, handled explicitly). Anything else — org_id,
# api_key, customer_id, provider keys, stream, user — is never read.
_PASSTHROUGH_KEYS = ("max_tokens", "temperature", "top_p", "stop")
# Not advertised in the input schema; passed through only so the shared feature check
# returns exactly HTTP's unsupported_feature refusal instead of silently dropping them.
_UNSUPPORTED_PASSTHROUGH_KEYS = ("tools", "functions", "tool_choice", "n", "response_format")

STDIO_REFUSAL_MESSAGE = (
    "dispatch runs only on the hosted MCP endpoint, where your service key identifies your org. "
    "Connect to https://llm-pricing-api.fly.dev/mcp with your service key as x-api-key or "
    "Authorization: Bearer, or use `npx -y llm-pricing-mcp` with LLM_PRICING_API_KEY set."
)


def _validation_error(errors) -> Dict[str, Any]:
    """HTTP 422's type/loc/msg per error, without ``input`` or ``ctx`` (never echo content)."""
    return {
        "success": False,
        "status_code": 422,
        "error": "validation_error",
        "detail": [
            {"type": e.get("type"), "loc": list(e.get("loc", ())), "msg": e.get("msg")}
            for e in errors
        ],
    }


def _local_validation_error(loc: str, msg: str) -> Dict[str, Any]:
    return _validation_error([{"type": "value_error", "loc": ("body", loc), "msg": msg}])


class DispatchTool:
    """Send a text chat request through the gateway with the caller's own stored provider keys."""

    # Needs a real org ledger: customers only. Anonymous and operator callers both get
    # access_refusal() (HTTP's dispatch_requires_api_key) from ToolManager.
    caller_access = CUSTOMER_ONLY
    # ToolManager never echoes str(e) of an unexpected exception for this tool.
    redact_errors = True

    @staticmethod
    def access_refusal() -> Dict[str, Any]:
        return {"success": False, "status_code": 401, **dispatch_requires_api_key_payload()}

    @staticmethod
    def stdio_refusal() -> Dict[str, Any]:
        return {
            "success": False,
            "error": "dispatch_requires_hosted_mcp",
            "message": STDIO_REFUSAL_MESSAGE,
            "hosted_endpoint": "/mcp",
            "npx_env": "LLM_PRICING_API_KEY",
            "signup": "/billing/signup",
            "docs": "/mcp-setup",
        }

    @staticmethod
    def _build_body(arguments: Dict[str, Any]):
        """HTTP-equivalent request body from the allow-listed arguments, or a validation refusal."""
        prompt = arguments.get("prompt")
        system = arguments.get("system")
        messages = arguments.get("messages")
        has_prompt = prompt is not None
        has_messages = messages is not None
        if has_prompt and has_messages:
            return None, _local_validation_error("prompt", "Pass either prompt or messages, not both")
        if not has_prompt and not has_messages:
            return None, _local_validation_error("prompt", "Either prompt or messages is required")
        if has_messages and system is not None:
            return None, _local_validation_error(
                "system", "system is only used with prompt; put a system message in messages instead"
            )
        if has_prompt:
            if not isinstance(prompt, str):
                return None, _validation_error([{"type": "string_type", "loc": ("body", "prompt"),
                                                 "msg": "Input should be a valid string"}])
            if system is not None and not isinstance(system, str):
                return None, _validation_error([{"type": "string_type", "loc": ("body", "system"),
                                                 "msg": "Input should be a valid string"}])
            messages = ([{"role": "system", "content": system}] if system is not None else []) + [
                {"role": "user", "content": prompt}
            ]
        body: Dict[str, Any] = {"model": arguments.get("model") or "auto", "messages": messages}
        for key in _PASSTHROUGH_KEYS + _UNSUPPORTED_PASSTHROUGH_KEYS:
            if key in arguments:
                body[key] = arguments[key]
        dispatch = arguments.get("dispatch")
        body["dispatch"] = {} if dispatch is None else dispatch
        return body, None

    async def execute(self, arguments: Dict[str, Any], caller: Optional[McpCaller] = None) -> Dict[str, Any]:
        # Direct construction is never unrestricted: no customer, no dispatch.
        if caller is None or not caller.is_customer or not caller.org_id:
            return self.access_refusal()
        if not isinstance(arguments, dict):
            arguments = {}

        body, refusal = self._build_body(arguments)
        if refusal is not None:
            return refusal
        try:
            req = ChatCompletionsRequest.model_validate(body)
        except ValidationError as exc:
            # FastAPI reports body errors under a leading "body" segment; match HTTP's loc exactly.
            return _validation_error([
                {**e, "loc": ("body", *e.get("loc", ()))}
                for e in exc.errors(include_input=False, include_context=False, include_url=False)
            ])

        customer = DispatchCustomer.from_caller(caller)

        async def _resolve_customer():
            return customer

        try:
            out = await prepare_and_dispatch(
                req,
                resolve_customer=_resolve_customer,
                header_resolver=HeaderKeyResolver({}),  # stored keys only
                header_currency=None,
                header_country=None,
                deps=default_deps(),
                stream=False,
            )
        except DispatchError as exc:
            result = {"success": False, "status_code": exc.status_code, **exc.payload}
            if "error" not in exc.payload:  # 429 is {"detail": ...} on HTTP
                result["error"] = "rate_limit_exceeded"
            return result
        except Exception as exc:  # never str(exc): it could carry request content
            return {"success": False, "error": "dispatch_internal_error", "error_type": type(exc).__name__}

        choice = (out.get("choices") or [{}])[0]
        return {
            "success": True,
            "completion": (choice.get("message") or {}).get("content"),
            "finish_reason": choice.get("finish_reason"),
            "id": out.get("id"),
            "created": out.get("created"),
            "model": out.get("model"),
            "usage": out.get("usage"),
            "routing_metadata": out.get("routing_metadata"),
            "dispatch_receipt": out.get("dispatch_receipt"),
        }
