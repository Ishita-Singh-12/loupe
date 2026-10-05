"""Opt-in, idempotent client patches. Streaming is passthrough in v0.1."""
import functools
import inspect
from .core import Span

def value(obj, key, default=None):
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)

def record(span, response, provider):
    usage = value(response, "usage")
    model = value(response, "model")
    if model:
        span.set_attribute("gen_ai.response.model", model)
    if value(response, "id"):
        span.set_attribute("gen_ai.response.id", value(response, "id"))
    if usage:
        for key, aliases in {"input_tokens": ("input_tokens", "prompt_tokens"),
                             "output_tokens": ("output_tokens", "completion_tokens")}.items():
            for alias in aliases:
                n = value(usage, alias)
                if n is not None:
                    span.set_attribute("gen_ai.usage." + key, n)
                    break
        for key in ("cache_read_input_tokens", "cache_creation_input_tokens"):
            n = value(usage, key)
            if n:
                dest = "cache_read" if key.startswith("cache_read") else "cache_write"
                span.set_attribute("gen_ai.usage." + dest + ".input_tokens", n)
        details = value(usage, "prompt_tokens_details") or value(usage, "input_tokens_details")
        cached = value(details, "cached_tokens", 0)
        if cached:
            span.set_attribute("gen_ai.usage.cache_read.input_tokens", cached)

def wrap(original, provider):
    if getattr(original, "_loupe_patched", False):
        return original
    def span_for(kwargs):
        model = kwargs.get("model", "unknown")
        return Span("chat " + model, "CLIENT", {"gen_ai.operation.name": "chat",
            "gen_ai.provider.name": provider, "gen_ai.request.model": model})
    if inspect.iscoroutinefunction(original):
        @functools.wraps(original)
        async def patched(*args, **kwargs):
            if kwargs.get("stream"):
                return await original(*args, **kwargs)
            with span_for(kwargs) as span:
                response = await original(*args, **kwargs)
                try:
                    record(span, response, provider)
                except Exception:
                    pass
                return response
    else:
        @functools.wraps(original)
        def patched(*args, **kwargs):
            if kwargs.get("stream"):
                return original(*args, **kwargs)
            with span_for(kwargs) as span:
                response = original(*args, **kwargs)
                try:
                    record(span, response, provider)
                except Exception:
                    pass
                return response
    patched._loupe_patched = True
    return patched

def patch_openai():
    """Patch sync/async Chat Completions and Responses create methods. Returns undo()."""
    from openai.resources.chat.completions import Completions, AsyncCompletions
    from openai.resources.responses import Responses, AsyncResponses
    targets = [Completions, AsyncCompletions, Responses, AsyncResponses]
    return _patch(targets, "openai")

def patch_anthropic():
    """Patch sync/async Messages.create. Does not instrument streaming helpers."""
    from anthropic.resources.messages import Messages, AsyncMessages
    return _patch([Messages, AsyncMessages], "anthropic")

def _patch(targets, provider):
    changed = []
    for cls in targets:
        original = cls.create
        if not getattr(original, "_loupe_patched", False):
            replacement = wrap(original, provider)
            cls.create = replacement
            changed.append((cls, original, replacement))
    def undo():
        for cls, original, replacement in changed:
            if cls.create is replacement:
                cls.create = original
    return undo
