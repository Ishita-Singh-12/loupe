"""LangChain callback adapter with explicit run-ID parenting (safe for concurrent runs)."""
import threading
from langchain_core.callbacks import BaseCallbackHandler
from .core import Span
from .instrumentation import record

class LoupeCallbackHandler(BaseCallbackHandler):
    raise_error = False
    def __init__(self, client=None):
        self.client = client
        self.runs = {}
        self.lock = threading.RLock()

    def _start(self, run_id, parent_run_id, name, kind, attributes=None):
        with self.lock:
            parent = self.runs.get(str(parent_run_id)) if parent_run_id else None
            self.runs[str(run_id)] = Span(name, kind, attributes, parent=parent, client=self.client)

    def _end(self, run_id, error=None, response=None):
        with self.lock:
            span = self.runs.pop(str(run_id), None)
        if span:
            if response:
                try:
                    output = response.llm_output or {}
                    usage = output.get("token_usage") or output.get("usage")
                    record(span, {"usage": usage, "model": output.get("model_name")}, "langchain")
                    # Newer LangChain chat results expose per-message usage_metadata.
                    if not usage:
                        for row in response.generations[:1]:
                            for generation in row[:1]:
                                metadata = getattr(getattr(generation, "message", None), "usage_metadata", None)
                                if metadata:
                                    record(span, {"usage": metadata}, "langchain")
                except Exception:
                    pass
            span.end(error)

    def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id=None, **kwargs):
        self._start(run_id, parent_run_id, kwargs.get("name") or (serialized or {}).get("name", "chain"), "INTERNAL")

    def on_llm_start(self, serialized, prompts, *, run_id, parent_run_id=None, **kwargs):
        params = kwargs.get("invocation_params", {})
        model = params.get("model") or params.get("model_name") or "unknown"
        self._start(run_id, parent_run_id, "chat " + model, "CLIENT", {
            "gen_ai.operation.name": "chat", "gen_ai.request.model": model,
            "gen_ai.provider.name": params.get("_type", "unknown")})

    def on_chat_model_start(self, serialized, messages, **kwargs):
        self.on_llm_start(serialized, [], **kwargs)

    def on_tool_start(self, serialized, input_str, *, run_id, parent_run_id=None, **kwargs):
        name = (serialized or {}).get("name", "tool")
        self._start(run_id, parent_run_id, "execute_tool " + name, "INTERNAL", {
            "gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": name})

    def on_chain_end(self, outputs, *, run_id, **kwargs): self._end(run_id)
    def on_tool_end(self, output, *, run_id, **kwargs): self._end(run_id)
    def on_llm_end(self, response, *, run_id, **kwargs): self._end(run_id, response=response)
    def on_chain_error(self, error, *, run_id, **kwargs): self._end(run_id, error)
    def on_tool_error(self, error, *, run_id, **kwargs): self._end(run_id, error)
    def on_llm_error(self, error, *, run_id, **kwargs): self._end(run_id, error)
