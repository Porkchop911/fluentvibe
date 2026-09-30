import json

from fluentvibe.authoring.request_parts import split_request_document
from fluentvibe.authoring.requirements import extract_requirements

WASH = "5. Wash 2–3 times with a 1X B&W Buffer."
BIND = "3. Incubate for 15 min at room temperature using gentle rotation."
DOCUMENT = WASH + "\n" + BIND


def test_copied_steps_are_source_but_overrides_remain_instructions():
    request = "Use the FCA for ethanol.\n\n" + WASH + "\n" + BIND + "\n\nUse 4 washes instead."
    parts = split_request_document(request, DOCUMENT)
    assert parts.document_excerpts == (WASH, BIND)
    assert parts.instructions == "Use the FCA for ethanol.\nUse 4 washes instead."


def test_soft_wraps_and_typography_do_not_change_provenance():
    parts = split_request_document(BIND.replace("room temperature", "room\ntemperature"), DOCUMENT)
    assert not parts.instructions
    assert len(parts.document_excerpts) == 1


def test_no_document_or_no_match_preserves_request_exactly():
    request = "Please use the FCA.\n\nDo not incubate for 15 min.\n"
    assert split_request_document(request, None).instructions == request
    assert split_request_document(request, DOCUMENT).instructions == request


def test_modified_source_clause_is_never_silently_removed():
    for request in (BIND.replace("15", "10"), "Do not " + BIND, "Use the FCA: " + BIND):
        assert split_request_document(request, DOCUMENT).document_excerpts == ()


def test_short_common_phrase_and_decimal_value_remain_instructions():
    request = "Mix well. Use 1.0 M NaOH."
    parts = split_request_document(request, "Mix well. Use 1.0 M NaOH.")
    assert parts.instructions == request


def test_max_abbreviation_does_not_make_a_source_time_a_user_instruction():
    text = "Short oligonucleotides (<30 bases) require max. 10 min."
    parts = split_request_document("Automate DNA.\n\n" + text, text)
    assert parts.instructions == "Automate DNA."
    assert parts.document_excerpts == (text,)


def test_short_bulleted_source_recipe_is_source():
    text = "• Antibody/protein applications: PBS, pH 7.4."
    assert split_request_document(text, text).document_excerpts == (text,)


def test_extractor_routes_source_and_preserves_checkable_document_waits():
    class Client:
        def complete(self, *, messages, tools):
            prompt = messages[-1]["content"]
            instructions = prompt.split("PROTOCOL DOCUMENT:")[0]
            assert "Use the FCA" in instructions
            assert "Wash 2" not in instructions
            assert "PASTED DOCUMENT EXCERPTS" in prompt
            data = {"requirements": [
                {"id": "wash", "text": WASH, "kind": "unchecked"},
                {"id": "head", "text": "Use the FCA", "kind": "head_for_reagent", "params": {"head": "fca"}},
                {"id": "wait", "text": BIND, "kind": "wait_between", "params": {"min_seconds": 900}},
                {"id": "override", "text": "Use 4 washes instead.", "kind": "unchecked"},
            ], "dispositions": [{"clause": WASH, "disposition": "requirement"}]}
            return {"tool_calls": [{"function": {"name": "submit_requirements", "arguments": json.dumps(data)}}]}

    request = "Use the FCA.\n\n" + DOCUMENT + "\n\nUse 4 washes instead."
    requirements, dispositions = extract_requirements(Client(), request, DOCUMENT)
    assert [r.id for r in requirements] == ["head", "wait", "override"]
    assert len(dispositions) == 2
    assert all(d["disposition"] == "document" for d in dispositions)
