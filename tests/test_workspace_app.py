from __future__ import annotations

import base64
import json
import time
from pathlib import Path

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
            "workspace_modules": [{"name": "spri_cleanup", "approved": True}],
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
    assert Path(paths["modules_manifest"]).exists()
    assert Path(paths["modules_dir"], "workspace_modules.py").exists()
    manifest = service.yaml.safe_load(Path(paths["modules_manifest"]).read_text(encoding="utf-8"))
    assert manifest["modules"][0]["name"] == "spri_cleanup"
    assert manifest["modules"][0]["approved"] is True
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
    assert "Profile labware class contract" in body
    for item in common_labware:
        if item.get("catalog_name") and item.get("python_class"):
            assert f"| `{item['catalog_name']}` | `{item['python_class']}` |" in body

    # generation.profile.yaml carries data-driven deck_rules for this workspace
    # (trough family derived from valid slots + the FC-universal guard flags).
    import yaml

    gen_profile = yaml.safe_load(Path(paths["generation_yaml"]).read_text(encoding="utf-8"))
    deck_rules = gen_profile["deck_rules"][detail["workspace"]["name"]]
    assert deck_rules["require_fca_tipbox"] is True
    assert deck_rules["check_mix_section"] is True
    assert all(loc.startswith("WS_") for loc in deck_rules["trough_locations"])


def test_propose_workspace_modules_offers_spri_for_matching_setup() -> None:
    result = service.propose_workspace_modules({
        "prompt": "We do AMPure bead cleanup here.",
        "common_labware": [
            {"category": "magnet_rack", "catalog_name": "24 Magnet Plate"},
            {"category": "tip_box", "catalog_name": "MCA96, 100ul, Box", "python_class": "MCA100Box"},
        ],
    })

    assert result["ok"] is True
    assert result["modules"]
    proposal = result["modules"][0]
    assert proposal["name"] == "spri_cleanup"
    assert proposal["approved"] is False
    assert "source_preview" in proposal


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


def test_workspace_app_capabilities_expose_attachment_support() -> None:
    caps = service.capabilities()
    assert caps["ok"] is True
    assert caps["workspace_app"]["api_version"] >= 2
    assert caps["attachments"]["enabled"] is True
    assert ".pdf" in caps["attachments"]["supported_extensions"]


def test_workbench_authoring_send_enriches_uploaded_text_attachment(tmp_path: Path) -> None:
    class FakeResult:
        def to_dict(self):
            return {"status": "clarification_required", "clarification_questions": []}

    class FakeSession:
        def __init__(self) -> None:
            self.output_dir = tmp_path / "authoring"
            self._user_turns = []
            self.sent: list[str] = []

        def send(self, text: str):
            self.sent.append(text)
            self._user_turns.append(text)
            return FakeResult()

    session = FakeSession()
    session_id = "test-session-attachments"
    with service._JOB_LOCK:
        service._SESSIONS[session_id] = session
    try:
        payload = {
            "session_id": session_id,
            "message": "Use this SOP.",
            "attachments": [
                {
                    "name": "sop.md",
                    "mime_type": "text/markdown",
                    "size": 19,
                    "content_base64": base64.b64encode(b"Transfer 20 uL.\n").decode("ascii"),
                }
            ],
        }
        result = service._job_authoring_send(payload)
    finally:
        with service._JOB_LOCK:
            service._SESSIONS.pop(session_id, None)

    assert result["ok"] is True
    assert result["attachments"][0]["name"] == "sop.md"
    assert result["attachments"][0]["extraction_method"] == "text"
    assert result["attachments"][0]["extracted_chars"] > 0
    assert Path(result["attachments"][0]["stored_path"]).exists()
    assert Path(result["attachments"][0]["extracted_text_path"]).exists()
    assert "Use this SOP." in session.sent[0]
    assert "Attached file: sop.md" in session.sent[0]
    assert "Transfer 20 uL" in session.sent[0]


def test_workbench_authoring_send_allows_attachment_without_message(tmp_path: Path) -> None:
    class FakeResult:
        def to_dict(self):
            return {"status": "clarification_required", "clarification_questions": []}

    class FakeSession:
        def __init__(self) -> None:
            self.output_dir = tmp_path / "authoring"
            self._user_turns: list[str] = []
            self.sent: list[str] = []

        def send(self, text: str):
            self.sent.append(text)
            self._user_turns.append(text)
            return FakeResult()

    session = FakeSession()
    session_id = "test-session-attachment-only"
    with service._JOB_LOCK:
        service._SESSIONS[session_id] = session
    try:
        result = service._job_authoring_send({
            "session_id": session_id,
            "message": "",
            "attachments": [
                {
                    "name": "protocol.txt",
                    "content_base64": base64.b64encode(b"Make a transfer protocol.").decode("ascii"),
                }
            ],
        })
    finally:
        with service._JOB_LOCK:
            service._SESSIONS.pop(session_id, None)

    assert result["ok"] is True
    assert "Please author a fluentvibe protocol" in session.sent[0]
    assert "Make a transfer protocol." in session.sent[0]


def test_workbench_authoring_send_rejects_pasted_pdf_path_without_upload(tmp_path: Path) -> None:
    class FakeSession:
        def __init__(self) -> None:
            self.output_dir = tmp_path / "authoring"
            self._user_turns: list[str] = []

        def send(self, text: str):  # pragma: no cover - should not be called
            raise AssertionError("session.send should not be called for pasted PDF paths")

    session_id = "test-session-pdf-path"
    with service._JOB_LOCK:
        service._SESSIONS[session_id] = FakeSession()
    try:
        try:
            service._job_authoring_send({
                "session_id": session_id,
                "message": r"C:\Users\Niko\Downloads\protocol.pdf",
            })
        except ValueError as exc:
            assert "Use Attach files" in str(exc)
        else:
            raise AssertionError("pasted PDF path should be rejected")
    finally:
        with service._JOB_LOCK:
            service._SESSIONS.pop(session_id, None)


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
