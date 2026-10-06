from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import pytest

from fluentvibe.authoring.grounding import load_current_worktable_snapshot
from fluentvibe.catalog.catalog import index_exists
from fluentvibe.workspace_app import service


def _workspace_name() -> str:
    workspaces = service.list_workspaces()["workspaces"]
    assert workspaces
    for item in workspaces:
        if item["name"] == "SAT_Fluent_780_Rev3":
            return item["name"]
    return workspaces[0]["name"]


def test_workspace_app_lists_workspaces() -> None:
    if not index_exists():
        return
    payload = service.list_workspaces()
    assert payload["ok"] is True
    assert payload["workspaces"]
    assert {"name", "guid"} <= set(payload["workspaces"][0])


def test_workspace_app_lists_configurations() -> None:
    if not index_exists():
        return
    payload = service.list_configurations()
    assert payload["ok"] is True
    assert payload["configurations"]
    assert any(item["name"] == "TECAN,FLUENT,2203009762" for item in payload["configurations"])
    detail = service.configuration_detail(payload["preferred_guid"])
    assert detail["ok"] is True
    assert detail["configuration"]["device_count"] > 0


def test_workspace_detail_exposes_slots_and_roles() -> None:
    if not index_exists():
        return
    payload = service.workspace_detail(name=_workspace_name())
    assert payload["ok"] is True
    assert payload["workspace"]["name"]
    assert payload["positions"]
    assert payload["valid_slots"]
    assert payload["deck_coordinates"]
    assert payload["workspace_source"]["sha256"]
    assert payload["workspace_source"]["base_worktable_component_name"]
    assert any(row.get("x_mm") is not None and row.get("y_mm") is not None for row in payload["deck_coordinates"])
    by_path = {row["site_path"]: row for row in payload["deck_coordinates"]}
    if {"3/0/0", "3/1/0", "15/0/0"} <= set(by_path):
        assert by_path["3/0/0"]["coordinate_source"] == "workspace_arrangement_template"
        assert by_path["3/0/0"]["x_mm"] < by_path["15/0/0"]["x_mm"]
        assert by_path["3/0/0"]["y_mm"] != by_path["3/1/0"]["y_mm"]
    assert payload["deck_blocks"]
    assert "plate" in {item["name"] for item in payload["common_labware_categories"]}
    assert payload["default_common_labware"]


def test_save_profile_writes_generation_artifacts(tmp_path: Path) -> None:
    if not index_exists():
        return
    detail = service.workspace_detail(name=_workspace_name())
    common_labware = [
        cfg for cfg in detail["default_common_labware"]
        if cfg.get("catalog_name")
    ][:3]
    saved = service.save_profile(
        {
            "profile_name": "test profile",
            "configuration": {"guid": service.list_configurations()["preferred_guid"]},
            "workspace": detail["workspace"],
            "workspace_source": detail["workspace_source"],
            "common_labware": common_labware,
            "liquid_class": detail["liquid_class"],
        },
        base_dir=tmp_path,
    )
    assert saved["ok"] is True
    paths = saved["paths"]
    profile = json.loads(Path(paths["profile_json"]).read_text(encoding="utf-8"))
    assert profile["schema_version"] == service.PROFILE_SCHEMA_VERSION
    assert profile["configuration"]
    assert profile["workspace_source"]["sha256"] == detail["workspace_source"]["sha256"]
    assert profile["common_labware"]
    assert Path(paths["generation_yaml"]).exists()
    snapshot = load_current_worktable_snapshot(Path(paths["current_worktable"]))
    assert snapshot is not None
    assert snapshot["workspace"]["guid"] == detail["workspace"]["guid"]

    # The profile also emits a --lab-scope skills deck skill, driven by this
    # workspace, so authoring can target this deck instead of the shipped one.
    from fluentvibe.authoring.lab_skills import _parse_skill

    deck_path = Path(paths["deck_skill"])
    assert deck_path.exists()
    skill = _parse_skill(deck_path)
    assert skill is not None
    assert skill.axis == "deck"
    assert skill.always_on is True
    body = deck_path.read_text(encoding="utf-8")
    # binds to THIS workspace, not a hardcoded default
    assert detail["workspace"]["guid"] in body
    assert detail["workspace"]["name"] in body

    # generation.profile.yaml carries data-driven deck_rules for this workspace
    # (trough family derived from valid slots + the FC-universal guard flags).
    import yaml

    gen_profile = yaml.safe_load(Path(paths["generation_yaml"]).read_text(encoding="utf-8"))
    deck_rules = gen_profile["deck_rules"][detail["workspace"]["name"]]
    assert deck_rules["require_fca_tipbox"] is True
    assert deck_rules["check_mix_section"] is True
    assert all(loc.startswith("WS_") for loc in deck_rules["trough_locations"])


def test_list_and_load_profiles_roundtrip(tmp_path: Path) -> None:
    # Hermetic: write two synthetic saved profiles, then list + load them.
    for pname, wsname, guid in [
        ("alpha", "Deck A", "11111111-1111-1111-1111-111111111111"),
        ("beta", "Deck B", "22222222-2222-2222-2222-222222222222"),
    ]:
        root = tmp_path / pname
        root.mkdir()
        (root / "workspace_profile.json").write_text(
            json.dumps({
                "profile_name": pname,
                "workspace": {"name": wsname, "guid": guid},
                "configuration": {"guid": "cfg-1", "name": "cfg-1"},
                "common_labware": [
                    {"catalog_name": "Foo Plate", "label": "Foo",
                     "preferred_location": "Nest61mm_Pos", "preferred_position": 1},
                ],
                "liquid_class": {"name": "Water Free Single"},
            }),
            encoding="utf-8",
        )

    listing = service.list_profiles(base_dir=tmp_path)
    assert listing["ok"] is True
    names = {p["profile_name"] for p in listing["profiles"]}
    assert names == {"alpha", "beta"}

    loaded = service.load_profile("alpha", base_dir=tmp_path)
    assert loaded["profile_name"] == "alpha"
    assert loaded["workspace"] == {"name": "Deck A", "guid": "11111111-1111-1111-1111-111111111111"}
    assert loaded["configuration"]["guid"] == "cfg-1"
    assert loaded["liquid_class"] == "Water Free Single"
    assert loaded["common_labware"][0]["preferred_location"] == "Nest61mm_Pos"

    loaded_by_path = service.load_profile(str(tmp_path / "alpha"))
    assert loaded_by_path["profile_name"] == "alpha"
    assert loaded_by_path["workspace"]["guid"] == "11111111-1111-1111-1111-111111111111"


def test_list_profiles_empty_dir_is_ok(tmp_path: Path) -> None:
    assert service.list_profiles(base_dir=tmp_path / "nope") == {"ok": True, "profiles": []}


def test_load_profile_missing_raises(tmp_path: Path) -> None:
    try:
        service.load_profile("ghost", base_dir=tmp_path)
    except ValueError as exc:
        assert "not found" in str(exc).lower()
    else:
        raise AssertionError("loading a missing profile should raise")


def _wait_job(job_id: str) -> dict:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        job = service.job_status(job_id)["job"]
        if job["status"] in {"success", "failure"}:
            return job
        time.sleep(0.05)
    raise AssertionError(f"job did not finish: {job_id}")


def test_workbench_job_unknown_kind_raises() -> None:
    try:
        service.submit_job("nope", {})
    except ValueError as exc:
        assert "Unknown job kind" in str(exc)
    else:
        raise AssertionError("unknown job kind should raise")


def test_workbench_authoring_session_job_finishes_without_model_call() -> None:
    created = service.submit_job("authoring-session", {"retry_budget": 1})
    job = _wait_job(created["job"]["id"])
    # Session creation is local and should not contact the model endpoint.
    assert job["status"] == "success"
    assert job["result"]["ok"] is True
    assert job["result"]["session_id"]


def test_strata_authoring_session_uses_selected_local_instance(tmp_path: Path, monkeypatch) -> None:
    from fluentvibe import authoring

    settings = {}

    class FakeSession:
        def __init__(self, **kwargs):
            settings.update(kwargs)

    monkeypatch.setattr(authoring, "PromptAuthoringSession", FakeSession)
    monkeypatch.setattr(service, "WORKBENCH_BASE_DIR", tmp_path)
    result = service._job_authoring_session({"model_server": "strata"})

    assert settings["endpoint"] == service.STRATA_CHAT_ENDPOINT
    assert settings["model"] == service.STRATA_MODEL
    assert result["endpoint"] == service.STRATA_CHAT_ENDPOINT
    assert result["model"] == service.STRATA_MODEL
    with pytest.raises(ValueError, match="Unknown model server"):
        service._job_authoring_session({"model_server": "other"})


def test_workbench_simulate_source_job_reports_structured_result(tmp_path: Path) -> None:
    source = """
from fluentvibe import Worktable

def build_worktable():
    return Worktable(name="empty")
"""
    created = service.submit_job("simulate-source", {
        "source": source,
        "strict": False,
        "output_dir": str(tmp_path / "sim"),
    })
    job = _wait_job(created["job"]["id"])
    assert job["status"] == "success"
    assert "validation" in job["result"]
    assert isinstance(job["result"]["tool_calls"], list)


def test_workbench_decompile_missing_file_is_structured_failure() -> None:
    created = service.submit_job("decompile-xscr", {"xscr_path": "does-not-exist.xscr"})
    job = _wait_job(created["job"]["id"])
    assert job["status"] == "failure"
    assert "not found" in job["error"]["message"].lower()


def test_protocol_library_lists_and_decompiles_selected_datastore_script(tmp_path: Path, monkeypatch) -> None:
    source = Path(__file__).parent / "fixtures" / "decompiled_corpus" / "liha_selected_transfer.xscr"
    root = tmp_path / "UserSpecific"
    root.mkdir()
    script = root / "12345678-1234-1234-1234-123456789abc.xscr"
    shutil.copyfile(source, script)
    monkeypatch.setattr(service, "DEFAULT_PROTOCOL_DIR", root)
    monkeypatch.setattr(service, "WORKBENCH_BASE_DIR", tmp_path / "workbench")

    listing = service.list_protocols()
    assert listing["available"] is True
    assert len(listing["protocols"]) == 1
    item = listing["protocols"][0]
    assert item["id"] == script.stem
    assert service.list_protocols(query=item["name"])["protocols"]
    detail = service.protocol_detail(item["id"])
    assert detail["step_count"] and detail["groups"]

    result = service._job_decompile_xscr({"protocol_id": item["id"]})
    assert result["source"]
    assert Path(result["python_path"]).is_file()
    for invalid in ("../outside", "C:/outside", "", "missing"):
        try:
            service.protocol_detail(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid protocol ID accepted: {invalid!r}")


def test_object_explorer_exposes_catalog_classification() -> None:
    if not index_exists():
        return
    matches = service.search_objects("plate", kind="component", limit=10)["objects"]
    assert matches
    detail = service.object_detail(matches[0]["guid"], "component")
    assert detail["object"]["name"] == matches[0]["name"]
    assert "purpose_source" in detail["object"]
    assert service.search_objects("", kind="workspace", limit=3)["objects"]


def test_protocol_chat_requires_loopback_and_uses_selected_script(tmp_path: Path, monkeypatch) -> None:
    source = Path(__file__).parent / "fixtures" / "decompiled_corpus" / "liha_selected_transfer.xscr"
    root = tmp_path / "UserSpecific"
    root.mkdir()
    script = root / "12345678-1234-1234-1234-123456789abc.xscr"
    shutil.copyfile(source, script)
    monkeypatch.setattr(service, "DEFAULT_PROTOCOL_DIR", root)
    monkeypatch.setenv("FLUENTVIBE_LM_ENDPOINT", "http://192.168.0.126:1234/v1/chat/completions")
    assert service.protocol_chat_config()["available"] is False
    try:
        service._job_protocol_chat({"protocol_id": script.stem, "question": "What happens?"})
    except ValueError as exc:
        assert "loopback" in str(exc)
    else:
        raise AssertionError("LAN endpoint was accepted for protocol chat")

    monkeypatch.setenv("FLUENTVIBE_LM_ENDPOINT", "http://127.0.0.1:18020/v1/chat/completions")
    monkeypatch.setenv("FLUENTVIBE_LM_MODEL", "test-local-model")
    from fluentvibe.authoring import lm_client

    calls = []
    def fake_complete(self, *, messages, tools):
        calls.append((self.endpoint, messages, tools))
        return {"content": "It moves liquid with the LiHa."}
    monkeypatch.setattr(lm_client.LMStudioChatClient, "complete", fake_complete)
    result = service._job_protocol_chat({
        "protocol_id": script.stem, "modified_at": script.stat().st_mtime,
        "question": "What happens?",
    })
    assert result["answer"] == "It moves liquid with the LiHa."
    assert calls[0][0].startswith("http://127.0.0.1:")
    assert calls[0][1][-1] == {"role": "user", "content": "What happens?"}
    assert "Decompiled Python" in calls[0][1][0]["content"]
    assert calls[0][2] == []

    strata_config = service.protocol_chat_config("strata")
    assert strata_config["available"] is True
    assert strata_config["endpoint"] == service.STRATA_CHAT_ENDPOINT
    strata_result = service._job_protocol_chat({
        "model_server": "strata", "protocol_id": script.stem, "question": "What happens?",
    })
    assert strata_result["model"] == service.STRATA_MODEL
    assert calls[-1][0] == service.STRATA_CHAT_ENDPOINT
    with pytest.raises(ValueError, match="Unknown model server"):
        service.protocol_chat_config("other")


def test_saved_profile_is_listable_and_loadable(tmp_path: Path) -> None:
    # End-to-end through save_profile: save → list → load reflects selections.
    if not index_exists():
        return
    detail = service.workspace_detail(name=_workspace_name())
    common = [c for c in detail["default_common_labware"] if c.get("catalog_name")][:2]
    service.save_profile(
        {
            "profile_name": "editme",
            "workspace": detail["workspace"],
            "workspace_source": detail["workspace_source"],
            "common_labware": common,
            "liquid_class": detail["liquid_class"],
        },
        base_dir=tmp_path,
    )
    assert "editme" in {p["profile_name"] for p in service.list_profiles(base_dir=tmp_path)["profiles"]}
    loaded = service.load_profile("editme", base_dir=tmp_path)
    assert loaded["workspace"]["guid"] == detail["workspace"]["guid"]
    assert len(loaded["common_labware"]) == len(common)


def test_save_profile_rejects_invalid_slot(tmp_path: Path) -> None:
    if not index_exists():
        return
    detail = service.workspace_detail(name=_workspace_name())
    item = next(
        cfg for cfg in detail["default_common_labware"]
        if cfg.get("catalog_name")
    )
    bad_item = dict(item)
    bad_item["preferred_location"] = "NotARealLocation"
    bad_item["preferred_position"] = 999
    try:
        service.save_profile(
            {
                "profile_name": "bad",
                "workspace": detail["workspace"],
                "common_labware": [bad_item],
            },
            base_dir=tmp_path,
        )
    except ValueError as exc:
        assert "not valid" in str(exc)
    else:
        raise AssertionError("invalid slot was accepted")


def test_save_profile_rejects_stale_workspace_source(tmp_path: Path) -> None:
    if not index_exists():
        return
    detail = service.workspace_detail(name=_workspace_name())
    stale_source = dict(detail["workspace_source"])
    stale_source["sha256"] = "0" * 64
    try:
        service.save_profile(
            {
                "profile_name": "stale",
                "workspace": detail["workspace"],
                "workspace_source": stale_source,
                "common_labware": [],
            },
            base_dir=tmp_path,
        )
    except ValueError as exc:
        assert "Workspace file changed" in str(exc)
    else:
        raise AssertionError("stale workspace source was accepted")


@pytest.mark.parametrize("message", ["Use the attached protocol", ""])
def test_authoring_upload_reaches_model_and_is_saved(monkeypatch, tmp_path, message):
    import base64
    from types import SimpleNamespace

    received = []
    session = SimpleNamespace(
        output_dir=tmp_path, _user_turns=[],
        send=lambda text: received.append(text) or SimpleNamespace(to_dict=lambda: {"status": "success"}),
    )
    monkeypatch.setitem(service._SESSIONS, "attachment-test", session)
    result = service._job_authoring_send({
        "session_id": "attachment-test", "message": message,
        "attachments": [{"name": "protocol.txt", "content_base64": base64.b64encode(
            b"Mix 96 samples with 50 uL buffer.").decode()}],
    })
    assert "Mix 96 samples with 50 uL buffer." in received[0]
    assert "Attached file: protocol.txt" in received[0]
    if message:
        assert received[0].startswith(message)
    assert Path(result["attachments"][0]["stored_path"]).read_bytes() == b"Mix 96 samples with 50 uL buffer."


def test_authoring_invalid_upload_does_not_call_model(monkeypatch, tmp_path):
    from types import SimpleNamespace

    received = []
    monkeypatch.setitem(service._SESSIONS, "invalid-attachment-test", SimpleNamespace(
        output_dir=tmp_path, _user_turns=[], send=lambda text: received.append(text),
    ))
    with pytest.raises(ValueError, match="valid base64"):
        service._job_authoring_send({
            "session_id": "invalid-attachment-test", "message": "Read this",
            "attachments": [{"name": "protocol.txt", "content_base64": "!invalid!"}],
        })
    assert received == []


def test_document_upload_controls_are_inside_authoring_panel():
    from html.parser import HTMLParser

    class Panels(HTMLParser):
        def __init__(self):
            super().__init__()
            self.stack = []
            self.controls = {}

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            panel = attrs.get("id", "") if "panel" in attrs.get("class", "").split() else None
            if attrs.get("id") in {"authorDocuments", "clearAuthorDocuments", "authorDocumentList"}:
                self.controls[attrs["id"]] = next((p for _, p in reversed(self.stack) if p), None)
            if tag not in {"input", "br", "hr", "img", "meta", "link"}:
                self.stack.append((tag, panel))

        def handle_endtag(self, tag):
            for index in range(len(self.stack) - 1, -1, -1):
                if self.stack[index][0] == tag:
                    del self.stack[index:]
                    break

    parser = Panels()
    parser.feed((Path(service.__file__).parent / "static" / "index.html").read_text(encoding="utf-8"))
    assert parser.controls == {
        "authorDocuments": "tab-author",
        "clearAuthorDocuments": "tab-author",
        "authorDocumentList": "tab-author",
    }


def test_job_polling_recovers_without_resubmitting():
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for frontend polling test")
    html = (Path(service.__file__).parent / "static" / "index.html").read_text(encoding="utf-8")
    api_code = html.split("    async function api(path, options) {", 1)[1].split("    async function post(", 1)[0]
    poll_code = html.split("    async function pollJob(jobId, statusId, resultId, done) {", 1)[1].split("    const sleep =", 1)[0]
    script = "async function api(path, options) {" + api_code + "async function pollJob(jobId, statusId, resultId, done) {" + poll_code
    script += r"""
const assert = require('node:assert/strict');
let requests = [], statuses = [], completed = 0;
const setStatus = (id, text) => statuses.push(text);
const showJson = () => {};
const sleep = async () => {};
let replies = [new TypeError('Failed to fetch'), Object.assign(new Error('timeout'), {name:'TimeoutError'}),
  {ok:true, job:{status:'running',kind:'authoring-send'}},
  {ok:true, job:{status:'success',kind:'authoring-send',result:{ok:true}}}];
global.fetch = async (path, options) => {
  requests.push([path, options.method || 'GET']);
  const reply = replies.shift();
  if (reply instanceof Error) throw reply;
  return {ok:true, json:async () => reply};
};
(async () => {
  await pollJob('original-job', 'status', null, () => completed++);
  assert.equal(completed, 1);
  assert.equal(requests.length, 4);
  assert(requests.every(([path, method]) => path === '/api/job?id=original-job' && method === 'GET'));
  assert(statuses.some(text => text.includes('reconnecting')));
  requests = [];
  global.fetch = async () => {requests.push(1); return {ok:false,json:async()=>({ok:false,message:'Job not found'})};};
  await assert.rejects(pollJob('missing-job','status',null), /Job not found/);
  assert.equal(requests.length, 1);
})().catch(err => {console.error(err); process.exitCode = 1;});
"""
    result = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("value", [0, -1, 7201, float("inf"), float("nan")])
def test_authoring_timeout_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        service._authoring_timeout({"request_timeout_s": value})


def test_authoring_timeout_applies_to_reply(monkeypatch, tmp_path):
    from types import SimpleNamespace

    callback = lambda delta: None
    client = SimpleNamespace(request_timeout_s=240, progress_callback=None)
    def send(text):
        assert client.request_timeout_s == 1200
        assert client.progress_callback is callback
        return SimpleNamespace(to_dict=lambda: {"status": "success"})
    monkeypatch.setitem(service._SESSIONS, "timeout-test", SimpleNamespace(
        output_dir=tmp_path, _web_client=client, send=send,
    ))
    service._job_authoring_send({"session_id": "timeout-test", "message": "confirm",
        "request_timeout_s": 1200, "_progress_callback": callback})
    assert client.progress_callback is None


def test_authoring_stream_is_visible_before_completion(monkeypatch, tmp_path):
    import threading
    import urllib.request
    from types import SimpleNamespace
    from fluentvibe.authoring.lm_client import LMStudioChatClient

    first_chunk = threading.Event()
    release = threading.Event()
    class Response:
        headers = {"Content-Type": "text/event-stream"}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def close(self): pass
        def __iter__(self):
            yield b'data: {"choices":[{"delta":{"reasoning_content":"Checking volumes"}}]}\n'
            first_chunk.set()
            assert release.wait(5)
            yield b'data: {"choices":[{"delta":{"content":"Ready"},"finish_reason":"stop"}]}\n'
            yield b'data: [DONE]\n'
    def open_request(req, timeout):
        assert timeout == 1200
        return Response()
    monkeypatch.setattr(urllib.request, "urlopen", open_request)
    client = LMStudioChatClient(request_timeout_s=240)
    def send(text):
        result = client.complete(messages=[{"role":"user","content":text}], tools=[])
        return SimpleNamespace(to_dict=lambda: {"status":"success", "text":result["content"]})
    monkeypatch.setitem(service._SESSIONS, "stream-test", SimpleNamespace(
        output_dir=tmp_path, _web_client=client, send=send,
    ))
    created = service.submit_job("authoring-send", {
        "session_id":"stream-test", "message":"confirm", "request_timeout_s":1200,
    })
    job_id = created["job"]["id"]
    try:
        assert first_chunk.wait(5)
        running = service.job_status(job_id)["job"]
        assert running["status"] == "running"
        assert running["progress"]["thinking"] == "Checking volumes"
    finally:
        release.set()
    for _ in range(100):
        finished = service.job_status(job_id)["job"]
        if finished["status"] not in {"queued", "running"}: break
        time.sleep(0.01)
    assert finished["status"] == "success", finished
    assert finished["progress"]["content"] == "Ready"
    assert finished["progress"]["characters"] == len("Checking volumesReady")
