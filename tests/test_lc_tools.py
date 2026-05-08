"""Unit tests for the LangChain `StructuredTool` wrappers.

The wrappers must be byte-identical in behavior to direct `AuthoringToolRegistry`
dispatch — same return dicts, same `registry.calls` log, same error categories.
"""

from __future__ import annotations

import pytest
from pathlib import Path

from tecanlab.authoring.lc_tools import (
    _args_model_for_tool,
    _python_type_for,
    make_lc_tools,
)
from tecanlab.authoring.tools import AuthoringToolRegistry, tool_definitions


# ── JSON-schema → Python type mapping ────────────────────────────────

class TestPythonTypeFor:
    def test_string_maps_to_str(self):
        assert _python_type_for({"type": "string"}) is str

    def test_number_maps_to_float(self):
        assert _python_type_for({"type": "number"}) is float

    def test_integer_maps_to_int(self):
        assert _python_type_for({"type": "integer"}) is int

    def test_boolean_maps_to_bool(self):
        assert _python_type_for({"type": "boolean"}) is bool

    def test_array_of_strings(self):
        assert _python_type_for({"type": "array", "items": {"type": "string"}}) == list[str]

    def test_array_of_objects_becomes_list_of_dicts(self):
        result = _python_type_for({"type": "array", "items": {"type": "object"}})
        assert result == list[dict[str, str | int | float | bool | None]] or "list[dict" in str(result)

    def test_object_maps_to_dict(self):
        result = _python_type_for({"type": "object"})
        assert "dict" in str(result)


# ── Args-model autogeneration ────────────────────────────────────────

class TestArgsModelForTool:
    def test_required_field_has_no_default(self):
        defs = {t["function"]["name"]: t for t in tool_definitions()}
        model = _args_model_for_tool(defs["lookup_api"])
        # `object_or_class` is required — instantiating without it must error.
        with pytest.raises(Exception):
            model()

    def test_optional_field_defaults_to_none(self):
        defs = {t["function"]["name"]: t for t in tool_definitions()}
        model = _args_model_for_tool(defs["lookup_workspace"])
        # `name_or_guid` is optional — model() without args should succeed.
        instance = model()
        assert instance.name_or_guid is None

    def test_array_field_accepts_list(self):
        defs = {t["function"]["name"]: t for t in tool_definitions()}
        model = _args_model_for_tool(defs["ask_user"])
        instance = model(question="What volume?", axes=["target_volume_ul"])
        assert instance.axes == ["target_volume_ul"]

    def test_extra_fields_ignored(self):
        """LM may emit extra keys that aren't in the schema — must not crash."""
        defs = {t["function"]["name"]: t for t in tool_definitions()}
        model = _args_model_for_tool(defs["lookup_api"])
        instance = model(object_or_class="Worktable", surprise_field="ignored")
        assert instance.object_or_class == "Worktable"
        assert not hasattr(instance, "surprise_field")


# ── make_lc_tools end-to-end behavior ────────────────────────────────

@pytest.fixture
def registry(tmp_path: Path) -> AuthoringToolRegistry:
    return AuthoringToolRegistry(output_dir=tmp_path / "lc_tools_test")


class TestMakeLcTools:
    def test_returns_one_tool_per_definition(self, registry: AuthoringToolRegistry):
        tools = make_lc_tools(registry)
        defs = tool_definitions()
        assert len(tools) == len(defs)

    def test_tool_names_match_definitions(self, registry: AuthoringToolRegistry):
        tools = make_lc_tools(registry)
        tool_names = {t.name for t in tools}
        def_names = {t["function"]["name"] for t in tool_definitions()}
        assert tool_names == def_names

    def test_invocation_routes_through_dispatch(self, registry: AuthoringToolRegistry):
        tools = {t.name: t for t in make_lc_tools(registry)}
        result = tools["lookup_api"].invoke({"object_or_class": "Worktable"})
        # The registry knows about "Worktable" — should be ok=True.
        assert result["ok"] is True
        # And the call should be logged in registry.calls (logging parity).
        assert len(registry.calls) == 1
        assert registry.calls[0]["name"] == "lookup_api"
        assert registry.calls[0]["arguments"] == {"object_or_class": "Worktable"}

    def test_invocation_matches_direct_dispatch(self, registry: AuthoringToolRegistry):
        """Wrapper must produce the same dict as a direct `dispatch()` call."""
        # Use a fresh registry for the direct path so call logs don't bleed.
        from copy import deepcopy
        direct = deepcopy(registry)

        wrapped = {t.name: t for t in make_lc_tools(registry)}
        wrapped_result = wrapped["ask_user"].invoke({"question": "What volume?"})
        direct_result = direct.dispatch("ask_user", {"question": "What volume?"})
        assert wrapped_result == direct_result

    def test_unknown_arg_does_not_crash(self, registry: AuthoringToolRegistry):
        """LangChain will reject extra keys at the schema layer; the wrapper
        relies on `extra='ignore'` to keep model-supplied junk from breaking
        dispatch."""
        tools = {t.name: t for t in make_lc_tools(registry)}
        # Should not raise; extra field stripped before dispatch.
        result = tools["lookup_api"].invoke({
            "object_or_class": "Worktable",
            "extraneous": "should be dropped",
        })
        assert result["ok"] is True

    def test_optional_args_can_be_omitted(self, registry: AuthoringToolRegistry):
        tools = {t.name: t for t in make_lc_tools(registry)}
        # ask_user has only `question` required; `axes` is optional.
        result = tools["ask_user"].invoke({"question": "Which plate?"})
        assert result["ok"] is True
        assert result["status"] == "needs_user"

    def test_required_arg_missing_errors_at_schema(self, registry: AuthoringToolRegistry):
        tools = {t.name: t for t in make_lc_tools(registry)}
        with pytest.raises(Exception):
            tools["lookup_api"].invoke({})
