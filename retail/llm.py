# retail/llm.py
# One small interface over two AI providers, so the features in retail/ai.py don't care which
# one is used:
#   - Google Gemini (default; has a free tier)  -> needs GEMINI_API_KEY
#   - Anthropic Claude (paid, higher quality)   -> needs ANTHROPIC_API_KEY
# Choose with AI_PROVIDER = "gemini" or "claude". If it is not set, whichever key exists is used,
# Gemini first.
import base64
import datetime
import json
import os

import streamlit as st

GEMINI_MODEL = "gemini-3.6-flash"
# Tried in order when the main model is overloaded (503) or its free quota is used up (429).
# On the free tier each model has its own small daily allowance, so backups also add capacity.
GEMINI_BACKUP_MODELS = ["gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-2.5-flash", "gemini-2.5-flash-lite"]
CLAUDE_MODEL = "claude-opus-5-5"
# If Claude declines a request, the API retries it on a suitable fallback model automatically
CLAUDE_FALLBACK = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
KEY_NAMES = {"gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"), "claude": ("ANTHROPIC_API_KEY",)}


class AIError(Exception):
    pass


def setting(name: str):
    try:
        value = st.secrets.get(name)
    except Exception:  # no secrets.toml
        value = None
    return value or os.environ.get(name)


def _key(provider: str):
    return next((setting(n) for n in KEY_NAMES[provider] if setting(n)), None)


def provider():
    """The provider in use, or None when no key is configured."""
    chosen = (setting("AI_PROVIDER") or "").strip().lower()
    if chosen in KEY_NAMES:
        return chosen if _key(chosen) else None
    return next((p for p in ("gemini", "claude") if _key(p)), None)


def model_name() -> str:
    if provider() == "claude":
        return setting("CLAUDE_MODEL") or CLAUDE_MODEL
    return setting("GEMINI_MODEL") or GEMINI_MODEL


def is_configured() -> bool:
    return provider() is not None


def friendly_error(e: Exception) -> str:
    if isinstance(e, AIError):
        return str(e)
    try:
        from google.genai import errors as gerr
        if isinstance(e, gerr.APIError):
            if e.code == 429:
                return ("The free Gemini limit is used up for now. Wait a minute and try again; "
                        "if it keeps happening, the daily limit is reached until tomorrow.")
            if e.code in (400, 401, 403) and "key" in str(e).lower():
                return "The Gemini API key was rejected. Check GEMINI_API_KEY."
            if e.code == 404:
                return f"The model '{model_name()}' is not available. Set GEMINI_MODEL to another model."
            return f"Gemini returned an error ({e.code}). Try again shortly."
    except ImportError:
        pass
    try:
        import anthropic
        if isinstance(e, anthropic.AuthenticationError):
            return "The Anthropic API key was rejected. Check ANTHROPIC_API_KEY."
        if isinstance(e, anthropic.RateLimitError):
            return "Too many AI requests right now. Wait a minute and try again."
        if isinstance(e, anthropic.APIStatusError):
            return f"Claude returned an error ({e.status_code}). Try again shortly."
        if isinstance(e, anthropic.APIConnectionError):
            return "Could not reach the AI service. Check your internet connection."
    except ImportError:
        pass
    return f"AI request failed: {e}"


# -------------------------------
# Clients
# -------------------------------
@st.cache_resource
def _gemini_client(key: str):
    from google import genai
    from google.genai import types
    # No built-in retries: a busy model hands over to the next backup model immediately
    return genai.Client(api_key=key, http_options=types.HttpOptions(
        timeout=45_000, retry_options=types.HttpRetryOptions(attempts=1)))


@st.cache_resource
def _used_up_today() -> dict:
    """Models whose free daily allowance ran out, mapped to the date it happened."""
    return {}


@st.cache_resource
def _claude_client(key: str):
    import anthropic
    return anthropic.Anthropic(api_key=key)


def _require():
    p = provider()
    if p is None:
        raise AIError("No AI key configured. Add GEMINI_API_KEY (free) or ANTHROPIC_API_KEY.")
    return p


# -------------------------------
# Gemini
# -------------------------------
def _gemini_text(response) -> str:
    text = (response.text or "").strip()
    if not text:
        raise AIError("Gemini returned no answer (it may have blocked the request). Try rephrasing.")
    return text


def _gemini_contents(prompt: str, image=None):
    if image is None:
        return prompt
    from google.genai import types
    return [types.Part.from_bytes(data=image[0], mime_type=image[1]), prompt]


def _gemini_generate(contents, config):
    """Call Gemini, moving on to a backup model when one is busy or out of free quota."""
    from google.genai import errors
    today = datetime.date.today()
    used_up = _used_up_today()
    models = [model_name()] + [m for m in GEMINI_BACKUP_MODELS if m != model_name()]
    models = [m for m in models if used_up.get(m) != today]
    if not models:
        raise AIError("Today's free Gemini allowance is used up on every model. It resets tomorrow; "
                      "or add an ANTHROPIC_API_KEY to keep going with Claude.")
    for i, model in enumerate(models):
        last = i == len(models) - 1
        try:
            response = _gemini_client(_key("gemini")).models.generate_content(
                model=model, contents=contents, config=config)
            st.session_state["ai_model_used"] = model
            return response
        except errors.APIError as e:
            if e.code == 429 and "PerDay" in str(e):
                used_up[model] = today
                if last:
                    raise AIError("Today's free Gemini allowance is used up on every model. It resets "
                                  "tomorrow; or add an ANTHROPIC_API_KEY to keep going with Claude.")
            elif e.code not in (429, 500, 503, 504) or last:
                raise
        except Exception as e:  # timeout or dropped connection
            if "timeout" not in type(e).__name__.lower() or last:
                raise


def _gemini_complete(system, prompt, image=None, schema=None):
    from google.genai import types
    config = types.GenerateContentConfig(system_instruction=system)
    if schema is not None:
        config.response_mime_type = "application/json"
        config.response_schema = schema
    return _gemini_generate(_gemini_contents(prompt, image), config)


def _gemini_chat(system, tools, history, question):
    from google.genai import types
    contents = [types.Content(role="model" if m["role"] == "assistant" else "user",
                              parts=[types.Part(text=m["content"])]) for m in history]
    contents.append(types.Content(role="user", parts=[types.Part(text=question)]))
    # The SDK runs the Python functions the model asks for and feeds the results back
    config = types.GenerateContentConfig(
        system_instruction=system,
        tools=tools,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(maximum_remote_calls=10),
    )
    return _gemini_text(_gemini_generate(contents, config))


# -------------------------------
# Claude
# -------------------------------
def _claude_check(message):
    if message.stop_reason == "refusal":
        raise AIError("Claude declined this request.")
    return message


def _claude_text(message) -> str:
    _claude_check(message)
    return "\n".join(b.text for b in message.content if b.type == "text").strip()


def _claude_content(prompt: str, image=None):
    if image is None:
        return prompt
    return [{"type": "image", "source": {"type": "base64", "media_type": image[1],
                                         "data": base64.standard_b64encode(image[0]).decode()}},
            {"type": "text", "text": prompt}]


# -------------------------------
# Public interface
# -------------------------------
def complete(system: str, prompt: str, effort: str = "low") -> str:
    """Plain text answer."""
    if _require() == "gemini":
        return _gemini_text(_gemini_complete(system, prompt))
    message = _claude_client(_key("claude")).beta.messages.create(
        model=model_name(), max_tokens=16000, system=system,
        messages=[{"role": "user", "content": prompt}],
        output_config={"effort": effort}, **CLAUDE_FALLBACK)
    return _claude_text(message)


def structured(system: str, prompt: str, schema, image=None):
    """Answer that fills in a Pydantic model. `image` is (bytes, media_type) or None."""
    if _require() == "gemini":
        response = _gemini_complete(system, prompt, image, schema)
        if isinstance(response.parsed, schema):
            return response.parsed
        try:
            return schema.model_validate_json(response.text or "")
        except ValueError:
            raise AIError("The AI response could not be read. Try again.")
    message = _claude_client(_key("claude")).beta.messages.parse(
        model=model_name(), max_tokens=16000, system=system,
        messages=[{"role": "user", "content": _claude_content(prompt, image)}],
        output_format=schema, **CLAUDE_FALLBACK)
    _claude_check(message)
    if message.parsed_output is None:
        raise AIError("The AI response could not be read. Try again.")
    return message.parsed_output


def chat_with_tools(system: str, tools: list, history: list, question: str) -> str:
    """Answer a question, letting the model call the given Python functions for data.
    Each function needs type hints and a docstring with an Args section."""
    if _require() == "gemini":
        return _gemini_chat(system, tools, history, question)
    from anthropic import beta_tool
    runner = _claude_client(_key("claude")).beta.messages.tool_runner(
        model=model_name(), max_tokens=16000, system=system,
        tools=[beta_tool(t) for t in tools],
        messages=history + [{"role": "user", "content": question}],
        output_config={"effort": "medium"}, **CLAUDE_FALLBACK)
    return _claude_text(runner.until_done())


def dumps(obj) -> str:
    return json.dumps(obj, default=str)
