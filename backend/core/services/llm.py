import json

from django.conf import settings

NEBIUS_BASE_URL = "https://api.tokenfactory.nebius.com/v1/"

BROWSER_FUNCTIONS = [
    {
        "name": "read_page",
        "description": "Read the visible interactive controls on the current tab. Prefer this over a screenshot.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filter": {"type": "string", "enum": ["interactive", "all"]},
                "ref": {"type": "string", "description": "Optional ref to expand one control."},
            },
        },
    },
    {
        "name": "left_click",
        "description": "Click a control by its read_page ref, such as ref_3 or f0::ref_3.",
        "input_schema": {
            "type": "object",
            "properties": {"target": {"type": "string"}},
            "required": ["target"],
        },
    },
    {
        "name": "form_input",
        "description": (
            "Set one field's value with the native setter. "
            "Call this once per field in the same response so the page fills in one batch."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string"},
                "value": {"type": "string"},
            },
            "required": ["target", "value"],
        },
    },
    {
        "name": "type",
        "description": "Append text to the currently focused field.",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "screenshot",
        "description": "Capture the current tab as a JPEG. Use only when the page dump is not enough.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "navigate",
        "description": "Open a URL in the current tab. http and https only.",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    },
]

BROWSER_NAMES = {tool["name"] for tool in BROWSER_FUNCTIONS}


class Block:
    def __init__(self, type, text=None, id=None, name=None, input=None, toolset_name=None):
        self.type = type
        self.text = text
        self.id = id
        self.name = name
        self.input = input
        self.toolset_name = toolset_name


class Completion:
    def __init__(self, content, stop_reason, provider):
        self.content = content
        self.stop_reason = stop_reason
        self.provider = provider


def _nebius_client():
    if not settings.NEBIUS_API_KEY:
        raise RuntimeError("NEBIUS_API_KEY is missing. Put it in backend/.env")
    from openai import OpenAI

    return OpenAI(api_key=settings.NEBIUS_API_KEY, base_url=NEBIUS_BASE_URL)


def _anthropic_client():
    if not settings.ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY is missing. Put it in backend/.env")
    from anthropic import Anthropic

    return Anthropic(api_key=settings.ANTHROPIC_API_KEY)


def _has_image(messages):
    for message in messages or []:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "image":
                return True
            inner = block.get("content")
            if isinstance(inner, list) and any(
                isinstance(item, dict) and item.get("type") == "image" for item in inner
            ):
                return True
    return False


def _image_part(block):
    source = block.get("source") or {}
    data = source.get("data") or ""
    media = source.get("media_type") or "image/jpeg"
    if str(data).startswith("data:"):
        url = data
    else:
        url = "data:" + media + ";base64," + str(data)
    return {"type": "image_url", "image_url": {"url": url}}


def _tool_result_text(block):
    content = block.get("content")
    texts = []
    images = []
    if isinstance(content, str):
        texts.append(content)
    elif isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                texts.append(item.get("text") or "")
            elif item.get("type") == "image":
                images.append(_image_part(item))
            elif item.get("type") == "browser_state":
                payload = {key: value for key, value in item.items() if key != "type"}
                texts.append(json.dumps(payload)[:2000])
    elif content is not None:
        texts.append(str(content))
    if block.get("is_error"):
        texts.insert(0, "Error")
    text = "\n".join(part for part in texts if part).strip()
    if not text:
        text = "screenshot" if images else "ok"
    return text, images


def _translate_user(message):
    content = message.get("content")
    if isinstance(content, str):
        return [{"role": "user", "content": content}] if content else []
    tool_messages = []
    parts = []
    for block in content or []:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind in {"thinking", "redacted_thinking"}:
            continue
        if kind == "tool_result":
            text, images = _tool_result_text(block)
            tool_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": block.get("tool_use_id") or "",
                    "content": text,
                }
            )
            parts.extend(images)
            continue
        if kind == "text" and block.get("text"):
            parts.append({"type": "text", "text": block.get("text")})
        elif kind == "image":
            parts.append(_image_part(block))
    out = tool_messages
    if parts:
        if len(parts) == 1 and parts[0].get("type") == "text":
            out.append({"role": "user", "content": parts[0]["text"]})
        else:
            out.append({"role": "user", "content": parts})
    return out


def _translate_assistant(message):
    content = message.get("content")
    if isinstance(content, str):
        return [{"role": "assistant", "content": content}] if content else []
    texts = []
    calls = []
    for block in content or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") in {"thinking", "redacted_thinking"}:
            continue
        if block.get("type") == "text" and block.get("text"):
            texts.append(block["text"])
        elif block.get("type") == "tool_use" and block.get("id") and block.get("name"):
            calls.append(
                {
                    "id": block["id"],
                    "type": "function",
                    "function": {
                        "name": block["name"],
                        "arguments": json.dumps(block.get("input") or {}),
                    },
                }
            )
    if not texts and not calls:
        return []
    payload = {"role": "assistant", "content": "\n".join(texts) if texts else None}
    if calls:
        payload["tool_calls"] = calls
    return [payload]


def _to_openai_messages(system, messages):
    converted = []
    if system:
        converted.append({"role": "system", "content": system})
    for message in messages or []:
        role = message.get("role")
        if role == "assistant":
            converted.extend(_translate_assistant(message))
        elif role == "user":
            converted.extend(_translate_user(message))
        elif role in {"system", "tool"}:
            converted.append(message)
    return converted


def _anthropic_tools(tools):
    converted = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        kind = str(tool.get("type") or "")
        if kind.startswith("browser_toolset"):
            converted.extend(BROWSER_FUNCTIONS)
            continue
        if tool.get("name") and tool.get("input_schema"):
            converted.append(
                {
                    "name": tool["name"],
                    "description": tool.get("description") or "",
                    "input_schema": tool["input_schema"],
                }
            )
            continue
        function = tool.get("function") or {}
        if kind == "function" and function.get("name"):
            converted.append(
                {
                    "name": function["name"],
                    "description": function.get("description") or "",
                    "input_schema": function.get("parameters") or {"type": "object", "properties": {}},
                }
            )
    return converted


def _to_openai_tools(tools):
    openai_tools = []
    for tool in _anthropic_tools(tools):
        openai_tools.append(
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description") or "",
                    "parameters": tool.get("input_schema") or {"type": "object", "properties": {}},
                },
            }
        )
    return openai_tools


def _parse_arguments(raw):
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    if isinstance(parsed, dict):
        return parsed
    return None


def _message_text(message):
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts = []
    for part in content:
        text = getattr(part, "text", None)
        if text is None and isinstance(part, dict):
            text = part.get("text")
        if text:
            parts.append(text)
    return "\n".join(parts)


def _message_calls(message):
    calls = getattr(message, "tool_calls", None)
    if calls is None and isinstance(message, dict):
        calls = message.get("tool_calls")
    return calls or []


def _from_openai(response):
    choices = getattr(response, "choices", None) or []
    if not choices:
        return Completion([], "end_turn", "nebius")
    message = choices[0].message
    blocks = []
    text = _message_text(message).strip()
    if text:
        blocks.append(Block(type="text", text=text))
    for call in _message_calls(message):
        function = getattr(call, "function", None)
        name = getattr(function, "name", None) if function is not None else None
        raw_args = getattr(function, "arguments", None) if function is not None else None
        call_id = getattr(call, "id", None)
        if isinstance(call, dict):
            function = call.get("function") or {}
            name = name or function.get("name")
            raw_args = raw_args if raw_args is not None else function.get("arguments")
            call_id = call_id or call.get("id")
        parsed = _parse_arguments(raw_args)
        if not name or not call_id or parsed is None:
            continue
        blocks.append(
            Block(
                type="tool_use",
                id=call_id,
                name=name,
                input=parsed,
                toolset_name="browser" if name in BROWSER_NAMES else None,
            )
        )
    stop = "tool_use" if any(block.type == "tool_use" for block in blocks) else "end_turn"
    return Completion(blocks, stop, "nebius")


def _usable(result):
    if result is None:
        return False
    for block in result.content or []:
        if getattr(block, "type", "") == "text" and (getattr(block, "text", "") or "").strip():
            return True
        if getattr(block, "type", "") == "tool_use" and getattr(block, "name", ""):
            return True
    return False


def _call_nebius(kwargs):
    messages = kwargs.get("messages") or []
    if _has_image(messages):
        model = settings.VISION_MODEL
    else:
        model = kwargs.get("model") or settings.RUN_MODEL
    payload = {
        "model": model,
        "messages": _to_openai_messages(kwargs.get("system") or "", messages),
        "max_tokens": kwargs.get("max_tokens") or 4096,
    }
    tools = _to_openai_tools(kwargs.get("tools"))
    if tools:
        payload["tools"] = tools
    client = _nebius_client()
    response = client.chat.completions.create(**payload)
    result = _from_openai(response)
    finish = ""
    choices = getattr(response, "choices", None) or []
    if choices:
        finish = getattr(choices[0], "finish_reason", "") or ""
    if finish == "length" and not _usable(result) and int(payload["max_tokens"]) < 8192:
        payload["max_tokens"] = 8192
        response = client.chat.completions.create(**payload)
        result = _from_openai(response)
    return result


def _claude_kwargs(kwargs):
    sent = {
        "model": settings.FALLBACK_MODEL,
        "max_tokens": kwargs.get("max_tokens") or 4096,
        "messages": kwargs.get("messages") or [],
    }
    if kwargs.get("system"):
        sent["system"] = kwargs["system"]
    tools = _anthropic_tools(kwargs.get("tools"))
    if tools:
        sent["tools"] = tools
    return sent


def _from_anthropic(response):
    blocks = []
    for block in getattr(response, "content", None) or []:
        if isinstance(block, dict):
            kind = block.get("type")
            text = block.get("text")
            block_id = block.get("id")
            name = block.get("name")
            tool_input = block.get("input")
            toolset = block.get("toolset_name")
        else:
            kind = getattr(block, "type", "")
            text = getattr(block, "text", None)
            block_id = getattr(block, "id", None)
            name = getattr(block, "name", None)
            tool_input = getattr(block, "input", None)
            toolset = getattr(block, "toolset_name", None)
        if kind in {"thinking", "redacted_thinking"}:
            continue
        if kind == "text" and (text or "").strip():
            blocks.append(Block(type="text", text=text))
        elif kind == "tool_use" and name and block_id:
            blocks.append(
                Block(
                    type="tool_use",
                    id=block_id,
                    name=name,
                    input=tool_input if isinstance(tool_input, dict) else {},
                    toolset_name=toolset or ("browser" if name in BROWSER_NAMES else None),
                )
            )
    stop = getattr(response, "stop_reason", None)
    if stop not in {"tool_use", "end_turn"}:
        stop = "tool_use" if any(block.type == "tool_use" for block in blocks) else "end_turn"
    return Completion(blocks, stop, "claude")


def anthropic_create(client, kwargs):
    try:
        return client.messages.create(**kwargs)
    except Exception as exc:
        from core.services.agent import _drop_bound_thinking, _echo_toolset_on_results, repair_tool_pairing

        text = str(exc).lower()
        thinking = kwargs.get("thinking") or {}
        if "signature" in text and "thinking" in text:
            retry = dict(kwargs)
            retry["messages"] = _drop_bound_thinking(kwargs.get("messages") or [])
            return client.messages.create(**retry)
        if thinking.get("type") == "enabled" and "adaptive" in text:
            retry = dict(kwargs)
            retry["thinking"] = {"type": "adaptive", "display": "summarized"}
            retry.pop("max_tokens", None)
            retry["max_tokens"] = 16384
            retry["output_config"] = {"effort": "high"}
            return client.messages.create(**retry)
        if "toolset_name" in text:
            retry = dict(kwargs)
            retry["messages"] = _echo_toolset_on_results(kwargs.get("messages") or [])
            return client.messages.create(**retry)
        if "tool_use" in text and "tool_result" in text:
            retry = dict(kwargs)
            retry["messages"] = _echo_toolset_on_results(repair_tool_pairing(kwargs.get("messages") or []))
            return client.messages.create(**retry)
        if "output_config" in text:
            retry = dict(kwargs)
            retry.pop("output_config", None)
            return client.messages.create(**retry)
        if thinking and "thinking" in text and "field required" not in text and "signature" not in text:
            retry = dict(kwargs)
            retry.pop("thinking", None)
            retry.pop("output_config", None)
            return client.messages.create(**retry)
        raise


def complete(kwargs):
    failure = None
    result = None
    try:
        result = _call_nebius(kwargs)
    except Exception as exc:
        failure = exc
        result = None
    if _usable(result):
        result.provider = "nebius"
        return result
    try:
        raw = anthropic_create(_anthropic_client(), _claude_kwargs(kwargs))
    except Exception:
        if failure is not None:
            raise RuntimeError("Nebius call failed and the Claude fallback failed.") from failure
        raise
    return _from_anthropic(raw)
