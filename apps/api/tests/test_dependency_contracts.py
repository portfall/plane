# Copyright (c) 2026 Portfall contributors
# SPDX-License-Identifier: AGPL-3.0-only

"""Exercise the dependency boundaries without contacting external services."""

import gzip
import json
import os
from pathlib import Path
from queue import Queue
import shlex
import socket
import subprocess
from threading import Thread
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import URLError
from urllib.request import urlopen

import pytest


@pytest.fixture
def local_service():
    requests = Queue()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            if self.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
            requests.put((self.path, json.loads(body)))
            response = json.dumps({
                "id": "synthetic-completion",
                "object": "chat.completion",
                "created": 0,
                "model": "synthetic-model",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "Local reply"},
                             "finish_reason": "stop"}],
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_plane_llm_completion_with_current_sdk(local_service, monkeypatch):
    from plane.app.views.external.base import get_llm_response

    url, requests = local_service
    monkeypatch.setenv("OPENAI_BASE_URL", url + "/v1")
    assert get_llm_response("Summarize", "Synthetic input", "synthetic-key", "synthetic-model", "openai") == (
        "Local reply", None,
    )
    path, body = requests.get(timeout=5)
    assert path == "/v1/chat/completions"
    assert body["model"] == "synthetic-model"
    assert body["messages"] == [{"role": "user", "content": "Summarize\nSynthetic input"}]


def test_plane_analytics_capture_with_current_sdk(local_service, monkeypatch, settings):
    from plane.bgtasks.event_tracking_task import track_event

    url, requests = local_service
    settings.SKIP_ENV_VAR = False
    monkeypatch.setenv("POSTHOG_API_KEY", "synthetic-key")
    monkeypatch.setenv("POSTHOG_HOST", url)
    track_event.run("synthetic-user", "dependency-contract", "synthetic-workspace", {"synthetic": True})
    path, body = requests.get(timeout=10)
    assert path.rstrip("/") == "/batch"
    event = body["batch"][0]
    assert event["event"] == "dependency-contract"
    assert event["distinct_id"] == "synthetic-user"
    assert event["properties"]["synthetic"] is True


def test_api_entrypoint_gunicorn_serves_plane_asgi(tmp_path):
    """Start the literal entrypoint command, including its selected worker class."""
    api = Path(__file__).resolve().parents[1]
    command = next(line.removeprefix("exec ") for line in
                   (api / "bin/docker-entrypoint-api.sh").read_text().splitlines()
                   if line.startswith("exec gunicorn "))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {**os.environ, "GUNICORN_WORKERS": "1", "PORT": str(port),
           "DJANGO_SETTINGS_MODULE": "plane.settings.production", "SCOUT_MONITOR": "false"}
    for key in ("GUNICORN_WORKERS", "PORT"):
        command = command.replace(f"${key}", env[key]).replace(f"${{{key}:-8000}}", env[key])
    log = tmp_path / "gunicorn.log"
    with log.open("w") as output:
        process = subprocess.Popen(shlex.split(command), cwd=api, env=env,
                                   stdout=output, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                assert process.poll() is None, log.read_text()
                try:
                    with urlopen(f"http://127.0.0.1:{port}/", timeout=1) as response:
                        assert response.status == 200
                        assert json.load(response) == {"status": "OK"}
                        break
                except (URLError, TimeoutError):
                    time.sleep(0.1)
            else:
                pytest.fail("Plane ASGI did not answer: " + log.read_text())
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    assert process.returncode == 0, log.read_text()


@pytest.mark.skipif(os.environ.get("PLANE_DEPENDENCY_BROKER_TESTS") != "1",
                    reason="Requires an explicitly supplied synthetic AMQP/Valkey fixture")
@pytest.mark.django_db(transaction=True)
def test_celery_control_events_and_database_scheduler():
    from celery.contrib.testing.worker import start_worker
    from django_celery_beat.schedulers import DatabaseScheduler
    from plane.celery import app
    from plane.settings.redis import redis_instance
    from uuid import uuid4

    redis = redis_instance()
    key = f"dependency-contract:{uuid4().hex}"
    events = []
    original_schedule = app.conf.beat_schedule

    @app.task(name="dependency.contract.record", ignore_result=True)
    def record(value):
        redis_instance().rpush(key, value)

    app.conf.beat_schedule = {
        "dependency-contract-repeat": {
            "task": record.name, "schedule": 1.0, "args": ["repeat"], "options": {"queue": key},
        },
    }
    try:
        # Use the real default event/control settings. A broker that rejects
        # their queue declarations must fail this test, not silently lose them.
        with app.connection() as connection:
            receiver = app.events.Receiver(connection, handlers={"*": events.append})
            with receiver.consumer_context() as (event_connection, _, _):
                with start_worker(app, pool="solo", concurrency=1, perform_ping_check=False,
                                  shutdown_timeout=20, loglevel="WARNING", task_events=True, without_heartbeat=False, queues=[key]):
                    replies = app.control.ping(timeout=5)
                    assert any(reply.get("ok") == "pong" for worker in replies for reply in worker.values())
                    record.apply_async(args=["direct"], queue=key)
                    scheduler = DatabaseScheduler(app=app)
                    try:
                        deadline = time.monotonic() + 20
                        while time.monotonic() < deadline:
                            scheduler.tick()
                            try:
                                event_connection.drain_events(timeout=0.2)
                            except socket.timeout:
                                pass
                            values = redis.lrange(key, 0, -1)
                            if (b"direct" in values and values.count(b"repeat") >= 2
                                    and any(event.get("type") == "task-succeeded" for event in events)):
                                break
                        else:
                            pytest.fail(f"Worker/scheduler effects={values!r}; events={sorted({e.get('type') for e in events})!r}")
                    finally:
                        scheduler.close()
                    assert values.count(b"direct") == 1
    finally:
        app.conf.beat_schedule = original_schedule
        redis.delete(key)
        app.tasks.pop(record.name, None)
