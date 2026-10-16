"""Open Deep Research launcher for Loupe.

Runs LangChain's Open Deep Research agent locally and records every run as a
Loupe trace on the hosted dashboard. Each question becomes one trace; every
model call, search and compression step becomes a span via LoupeCallbackHandler.

Usage:
    python odr/launcher.py            # local web UI at http://127.0.0.1:8788
    python odr/launcher.py --cli      # terminal mode
    python odr/launcher.py --check    # preflight spec check only

Keys are read from odr/.env (created on first run) or the environment:
    GOOGLE_API_KEY     Gemini API key (Google AI Studio)
    TAVILY_API_KEY     Tavily search API key
    LOUPE_INGEST_KEY   write key for the hosted Loupe backend
    LOUPE_ENDPOINT     optional, defaults to https://loupe-y3em.onrender.com
"""
import argparse
import asyncio
import getpass
import json
import os
import shutil
import sys
import threading
import time
import urllib.request
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ODR_DIR = Path(__file__).resolve().parent
ENV_FILE = ODR_DIR / ".env"
REPORTS_DIR = ODR_DIR / "reports"
DEFAULT_ENDPOINT = "https://loupe-y3em.onrender.com"
DEFAULT_MODEL = "google_genai:gemini-3.8-flash"
FALLBACK_MODEL = "google_genai:gemini-3.1-flash-lite"
MIN_PYTHON = (3, 10)
MIN_RAM_GB = 4
MIN_DISK_GB = 2
WEB_PORT = int(os.environ.get("LOUPE_ODR_PORT", "8788"))


# ---------------------------------------------------------------- settings

def load_env():
    """Read odr/.env into os.environ without overriding real env vars."""
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def save_env_value(key, value):
    lines = ENV_FILE.read_text().splitlines() if ENV_FILE.exists() else []
    replaced = False
    for i, line in enumerate(lines):
        if line.startswith(key + "="):
            lines[i] = f"{key}={value}"
            replaced = True
    if not replaced:
        lines.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(lines) + "\n")


def setting(name, default=None):
    return os.environ.get(name, default)


def require_keys():
    """Prompt once for missing keys and store them in odr/.env."""
    needed = {
        "GOOGLE_API_KEY": "Gemini API key (Google AI Studio)",
        "TAVILY_API_KEY": "Tavily API key",
        "LOUPE_INGEST_KEY": "Loupe ingest key",
    }
    missing = [k for k in needed if not setting(k)]
    if not missing:
        return
    print("\nFirst run setup. Keys are stored locally in odr/.env (git-ignored),")
    print("never printed and never sent anywhere except their own service.")
    for key in missing:
        value = getpass.getpass(f"Paste {needed[key]} ({key}): ").strip()
        if not value:
            raise SystemExit(f"{key} is required.")
        save_env_value(key, value)
        os.environ[key] = value
    print("Saved to odr/.env - you will not be asked again.\n")


# ---------------------------------------------------------------- preflight

def check_specs():
    """Print a spec report. Returns a list of hard failures."""
    failures = []

    def row(name, ok, detail, hard=False):
        mark = "PASS" if ok else ("FAIL" if hard else "WARN")
        print(f"  [{mark}] {name:<22} {detail}")
        if not ok and hard:
            failures.append(name)

    py = sys.version_info
    row("Python", py >= MIN_PYTHON, f"{py.major}.{py.minor}.{py.micro} (need {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+)", hard=True)
    row("CPU cores", True, f"{os.cpu_count() or '?'} cores (agent runs on the Gemini API, not locally)")

    try:
        import psutil
        ram = psutil.virtual_memory()
        row("Memory", ram.total >= MIN_RAM_GB * 1024**3, f"{ram.total / 1024**3:.1f} GB total, {ram.available / 1024**3:.1f} GB free")
    except ImportError:
        row("Memory", True, "psutil not installed, skipped")

    disk = shutil.disk_usage(ODR_DIR)
    row("Disk free", disk.free >= MIN_DISK_GB * 1024**3, f"{disk.free / 1024**3:.1f} GB free")

    endpoint = setting("LOUPE_ENDPOINT", DEFAULT_ENDPOINT)
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(endpoint + "/healthz", timeout=40) as resp:
                row("Loupe backend", resp.status == 200, f"{endpoint} reachable")
            break
        except Exception:
            if attempt == 2:
                row("Loupe backend", False, f"{endpoint} unreachable (free host may be asleep; retry)")
            else:
                time.sleep(3)

    for key in ("GOOGLE_API_KEY", "TAVILY_API_KEY", "LOUPE_INGEST_KEY"):
        row(key, bool(setting(key)), "set" if setting(key) else "missing", hard=True)
    return failures


# ---------------------------------------------------------------- research

def research_config(model=None):
    model = model or setting("LOUPE_ODR_MODEL", DEFAULT_MODEL)
    return {
        "configurable": {
            "allow_clarification": False,
            "search_api": "tavily",
            "research_model": model,
            "summarization_model": model,
            "compression_model": model,
            "final_report_model": model,
            "max_concurrent_research_units": int(setting("LOUPE_ODR_MAX_CONCURRENCY", "2")),
            "max_researcher_iterations": int(setting("LOUPE_ODR_MAX_ITERATIONS", "6")),
        },
        "recursion_limit": 150,
    }


async def run_research(question, client):
    """Run one Open Deep Research question and export it as one Loupe trace."""
    from langchain_core.messages import HumanMessage
    from loupe.langchain import LoupeCallbackHandler
    from open_deep_research.deep_researcher import deep_researcher

    handler = LoupeCallbackHandler(client)
    config = research_config()
    config["callbacks"] = [handler]
    before = dict(exported=client.exported, dropped=client.dropped, errors=client.export_errors)

    try:
        result = await deep_researcher.ainvoke(
            {"messages": [HumanMessage(content=question)]}, config=config)
        used_model = config["configurable"]["research_model"]
    except Exception as exc:
        fallback = setting("LOUPE_ODR_FALLBACK_MODEL", FALLBACK_MODEL)
        message = str(exc).lower()
        if ("resource_exhausted" not in message and "rate" not in message and "429" not in message)                 or not fallback or fallback == config["configurable"]["research_model"]:
            raise
        print(f"Primary model hit its quota; retrying the run on {fallback} ...")
        config = research_config(fallback)
        config["callbacks"] = [handler]
        result = await deep_researcher.ainvoke(
            {"messages": [HumanMessage(content=question)]}, config=config)
        used_model = fallback
    print(f"Model used: {used_model}")
    report = result.get("final_report") or "(the agent ended without a final report)"

    ok = client.flush(timeout=60)
    spans = {
        "exported": client.exported - before["exported"],
        "dropped": client.dropped - before["dropped"],
        "export_errors": client.export_errors - before["errors"],
        "flush_ok": ok,
    }
    return report, spans


def save_report(question, report):
    REPORTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = REPORTS_DIR / f"{stamp}.md"
    path.write_text(f"# {question}\n\n{report}\n")
    return path


def make_client():
    from loupe import configure
    return configure(
        endpoint=setting("LOUPE_ENDPOINT", DEFAULT_ENDPOINT),
        api_key=setting("LOUPE_INGEST_KEY"),
        service_name="open-deep-research",
        flush_interval=1.0,
        timeout=30.0,
    )


# ---------------------------------------------------------------- web UI

PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>loupe - open deep research</title>
<style>
  body { background:#0b0b0d; color:#e6e6e9; font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;
         margin:0; display:flex; justify-content:center; }
  main { width:min(720px, 92vw); padding:48px 0 96px; }
  h1 { font-size:18px; font-weight:600; } h1 span { color:#8b8b93; font-weight:400; }
  textarea { width:100%; box-sizing:border-box; min-height:88px; background:#141417; color:#e6e6e9;
             border:1px solid #26262b; border-radius:8px; padding:12px; font:inherit; resize:vertical; }
  button { margin-top:12px; background:#e6e6e9; color:#0b0b0d; border:0; border-radius:8px;
           padding:10px 22px; font:inherit; font-weight:600; cursor:pointer; }
  button:disabled { opacity:.4; cursor:default; }
  #status { margin:18px 0 0; color:#8b8b93; white-space:pre-wrap; }
  #report { margin-top:18px; background:#141417; border:1px solid #26262b; border-radius:8px;
            padding:18px; white-space:pre-wrap; display:none; }
  a { color:#9ec1ff; }
</style></head><body><main>
<h1>loupe <span>/ open deep research</span></h1>
<textarea id="q" placeholder="Ask a research question..."></textarea><br>
<button id="send" onclick="send()">Send</button>
<div id="status"></div>
<div id="report"></div>
<script>
async function send() {
  const q = document.getElementById('q').value.trim();
  if (!q) return;
  const btn = document.getElementById('send');
  btn.disabled = true;
  document.getElementById('report').style.display = 'none';
  const st = document.getElementById('status');
  st.textContent = 'Researching - this usually takes a few minutes.\\nEvery step is being recorded as a loupe trace.';
  try {
    const res = await fetch('/research', {method:'POST', body: JSON.stringify({question:q}),
                                          headers:{'Content-Type':'application/json'}});
    const job = await res.json();
    if (!job.id) { st.textContent = job.error || 'Could not start.'; btn.disabled = false; return; }
    const poll = setInterval(async () => {
      const r = await (await fetch('/job?id=' + job.id)).json();
      if (r.status === 'running') return;
      clearInterval(poll);
      btn.disabled = false;
      if (r.status === 'done') {
        st.textContent = 'Done. ' + r.spans + '\\nTrace is live on the dashboard: ' + r.dashboard +
                         '\\nReport saved: ' + r.saved;
        const el = document.getElementById('report');
        el.textContent = r.report; el.style.display = 'block';
      } else {
        st.textContent = 'Run failed: ' + r.error + '\\n' + (r.spans || '');
      }
    }, 3000);
  } catch (e) { st.textContent = 'Error: ' + e; btn.disabled = false; }
}
</script></main></body></html>"""


class JobStore:
    def __init__(self):
        self.jobs = {}
        self.busy = threading.Lock()

    def start(self, question, client):
        if not self.busy.acquire(blocking=False):
            return None
        job_id = uuid.uuid4().hex[:12]
        self.jobs[job_id] = {"status": "running", "question": question}
        threading.Thread(target=self._run, args=(job_id, question, client), daemon=True).start()
        return job_id

    def _run(self, job_id, question, client):
        job = self.jobs[job_id]
        try:
            report, spans = asyncio.run(run_research(question, client))
            saved = save_report(question, report)
            job.update(status="done", report=report, saved=str(saved),
                       dashboard=setting("LOUPE_ENDPOINT", DEFAULT_ENDPOINT),
                       spans=(f"{spans['exported']} spans exported to loupe"
                              + ("" if spans["flush_ok"] and not spans["dropped"]
                                 else f" (flush_ok={spans['flush_ok']}, dropped={spans['dropped']}, export_errors={spans['export_errors']})")))
        except Exception as exc:
            client.flush(timeout=30)
            job.update(status="error", error=f"{type(exc).__name__}: {exc}")
        finally:
            self.busy.release()


def serve(client):
    store = JobStore()

    class Handler(BaseHTTPRequestHandler):
        def _send(self, body, code=200, ctype="text/html; charset=utf-8"):
            data = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path.startswith("/job"):
                from urllib.parse import urlparse, parse_qs
                job = store.jobs.get(parse_qs(urlparse(self.path).query).get("id", [""])[0])
                self._send(json.dumps(job or {"status": "error", "error": "unknown job"}),
                           ctype="application/json")
            else:
                self._send(PAGE)

        def do_POST(self):
            if not self.path.startswith("/research"):
                self._send("{}", code=404, ctype="application/json")
                return
            length = int(self.headers.get("Content-Length") or 0)
            try:
                question = json.loads(self.rfile.read(length) or b"{}").get("question", "").strip()
            except json.JSONDecodeError:
                question = ""
            if not question:
                self._send(json.dumps({"error": "empty question"}), ctype="application/json")
                return
            job_id = store.start(question, client)
            if job_id is None:
                self._send(json.dumps({"error": "a run is already in progress, wait for it to finish"}),
                           ctype="application/json")
                return
            self._send(json.dumps({"id": job_id}), ctype="application/json")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", WEB_PORT), Handler)
    print(f"\nOpen http://127.0.0.1:{WEB_PORT} - type a question, hit Send.")
    print("Each run shows up as a trace on " + setting("LOUPE_ENDPOINT", DEFAULT_ENDPOINT))
    print("Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


# ---------------------------------------------------------------- cli

def cli(client):
    print("\nTerminal mode. Type a research question, Enter to send, blank line to quit.")
    while True:
        try:
            question = input("\nresearch> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not question:
            break
        print("Researching (a few minutes)... every step is recorded as a loupe trace.")
        try:
            report, spans = asyncio.run(run_research(question, client))
        except Exception as exc:
            client.flush(timeout=30)
            print(f"Run failed: {type(exc).__name__}: {exc}")
            continue
        saved = save_report(question, report)
        print(f"Done: {spans['exported']} spans exported "
              f"(flush_ok={spans['flush_ok']}, dropped={spans['dropped']}).")
        print(f"Report saved to {saved}\n")
        print(report)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cli", action="store_true", help="terminal mode instead of the local web UI")
    parser.add_argument("--check", action="store_true", help="run the preflight spec check and exit")
    args = parser.parse_args()

    load_env()
    if not setting("GOOGLE_API_KEY") and setting("GEMINI_API_KEY"):
        os.environ["GOOGLE_API_KEY"] = setting("GEMINI_API_KEY")

    print("Preflight check:")
    if not args.check:
        require_keys()
    failures = check_specs()
    if failures:
        raise SystemExit("Preflight failed: " + ", ".join(failures))
    if args.check:
        print("Preflight OK.")
        return

    client = make_client()
    try:
        if args.cli:
            cli(client)
        else:
            serve(client)
    finally:
        client.flush(timeout=30)
        client.close()


if __name__ == "__main__":
    main()
