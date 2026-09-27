"""Stop: a running job's model stream is cut and the job ends as stopped."""

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from fluentvibe import cancel
from fluentvibe.authoring.lm_client import LMStudioChatClient


class _SlowStream(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        try:
            for i in range(600):   # a minute of reasoning, one token every 0.1 s
                chunk = '{"choices":[{"delta":{"reasoning_content":"thinking %d "}}]}' % i
                self.wfile.write(f"data: {chunk}\n\n".encode())
                self.wfile.flush()
                time.sleep(0.1)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def log_message(self, *args):
        pass


@pytest.fixture()
def slow_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _SlowStream)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/v1/chat/completions"
    server.shutdown()


def test_stop_cuts_a_streaming_model_request(slow_server):
    client = LMStudioChatClient(endpoint=slow_server, model="m", api_key="k", request_timeout_s=120)
    token = cancel.CancelToken()
    outcome = {}

    def job():
        with cancel.bound(token):
            try:
                client.complete(messages=[{"role": "user", "content": "hi"}], tools=[])
                outcome["result"] = "finished"
            except cancel.Cancelled:
                outcome["result"] = "stopped"

    worker = threading.Thread(target=job)
    worker.start()
    time.sleep(1.0)
    started = time.monotonic()
    token.cancel()
    worker.join(10)
    assert outcome.get("result") == "stopped" and time.monotonic() - started < 3


def test_web_job_stop_and_question_wait():
    from fluentvibe.workspace_app import service

    def waits_for_answer(payload):
        return service._job_ask(payload, ["How many samples?"], timeout_s=60)

    service._JOB_HANDLERS["test-wait"] = waits_for_answer
    try:
        job = service.submit_job("test-wait", {})["job"]
        for _ in range(50):
            if service.job_status(job["id"])["job"]["question"]:
                break
            time.sleep(0.05)
        service.cancel_job({"id": job["id"]})
        for _ in range(50):
            status = service.job_status(job["id"])["job"]
            if status["status"] == "stopped":
                break
            time.sleep(0.1)
        assert status["status"] == "stopped" and status["question"] is None
    finally:
        service._JOB_HANDLERS.pop("test-wait", None)
