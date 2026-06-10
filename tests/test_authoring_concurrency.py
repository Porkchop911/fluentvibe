"""Tests for the concurrency phases in fluentvibe.authoring.

Covers:
- Parallel tool dispatch preserves message ordering even when futures
  complete out of order.
- Speculative compile reuses precomputed result instead of re-dispatching.
- GroundingPrefetcher populates the cache and dispatch consumes it.
- Subagent prefetch tolerates raising callables without breaking the
  main flow.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from fluentvibe.authoring.category_agents import (
    DEFAULT_CATEGORIES,
    CategoryAgent,
    _select,
    run_category_agents,
)
from fluentvibe.authoring.graph import (
    AuthoringConcurrencyConfig,
    GraphState,
    build_authoring_graph,
)
from fluentvibe.authoring.grounding_coordinator import GroundingCoordinator
from fluentvibe.authoring.prefetch import (
    GroundingPrefetcher,
    PrefetchConfig,
    _extract_search_terms,
    _parse_subagent_json,
)
from fluentvibe.authoring.tools import (
    AuthoringToolRegistry,
    _freeze_arguments,
)

# ── helpers ─────────────────────────────────────────────────────────


def _make_registry(tmp_path: Path) -> AuthoringToolRegistry:
    reg = AuthoringToolRegistry(
        output_dir=tmp_path / "out",
        workspace_name="SAT_Fluent_780_Rev3",
    )
    return reg


def _ai_with_tool_calls(calls: list[dict[str, Any]]) -> AIMessage:
    """Build an AIMessage carrying the LangChain-shaped tool_calls."""
    return AIMessage(content="", tool_calls=calls)


# ── Phase A: parallel dispatch correctness ──────────────────────────


class TestParallelDispatchOrdering:
    def test_message_order_preserved_when_futures_complete_out_of_order(
        self, tmp_path: Path
    ) -> None:
        registry = _make_registry(tmp_path)

        # Stub three parallel-safe tools so we control completion order
        # while keeping the catalog/disk paths out of the way.
        slow_started = threading.Event()
        original_pure = registry._dispatch_pure

        def slow_pure(name: str, payload: dict[str, Any]) -> dict[str, Any]:
            if name == "lookup_workspace":
                # Hold this one back so the second one finishes first.
                slow_started.set()
                time.sleep(0.15)
                return {"ok": True, "tool": "lookup_workspace"}
            if name == "search_labware":
                # Wait for the slow one to be in flight, then return fast.
                slow_started.wait(timeout=2.0)
                return {"ok": True, "tool": "search_labware"}
            if name == "lookup_rules":
                return {"ok": True, "tool": "lookup_rules"}
            return original_pure(name, payload)

        # Build a tool-call batch with three parallel-safe calls.
        tool_calls = [
            {"name": "lookup_workspace", "args": {"name_or_guid": "X"}, "id": "a"},
            {"name": "search_labware", "args": {"query": "plate"}, "id": "b"},
            {"name": "lookup_rules", "args": {}, "id": "c"},
        ]
        responses = [_ai_with_tool_calls(tool_calls), AIMessage(content="done")]
        client = FakeMessagesListChatModel(responses=responses)

        with patch.object(registry, "_dispatch_pure", side_effect=slow_pure):
            graph = build_authoring_graph(
                registry=registry,
                client=client,
                output_dir=registry.output_dir,
                retry_budget=1,
                concurrency=AuthoringConcurrencyConfig(
                    parallel_tool_dispatch=True,
                    speculative_compile=False,
                    prefetch_deterministic=False,
                    prefetch_subagent=False,
                ),
            )
            state = GraphState(
                messages=[SystemMessage(content="s"), HumanMessage(content="p")],
                iterations=0,
                tool_call_count=0,
                best_code=None,
                last_validation=None,
                result=None,
                prompt="p",
            )
            graph.invoke(state)

        # The registry's call log records them in the original emission
        # order, regardless of which thread finished first.
        names = [c["name"] for c in registry.calls]
        # Filter to just the three we care about (the graph may also emit
        # workflow-init or grounding-nudge calls; those are unrelated).
        observed = [n for n in names if n in {"lookup_workspace", "search_labware", "lookup_rules"}]
        assert observed == ["lookup_workspace", "search_labware", "lookup_rules"]

    def test_serial_run_gives_same_call_order(self, tmp_path: Path) -> None:
        # Sanity: with parallel dispatch off, the order is identical.
        registry = _make_registry(tmp_path)

        def stub_pure(name: str, payload: dict[str, Any]) -> dict[str, Any]:
            return {"ok": True, "tool": name}

        tool_calls = [
            {"name": "lookup_rules", "args": {}, "id": "a"},
            {"name": "lookup_workspace", "args": {"name_or_guid": "X"}, "id": "b"},
        ]
        responses = [_ai_with_tool_calls(tool_calls), AIMessage(content="done")]
        client = FakeMessagesListChatModel(responses=responses)

        with patch.object(registry, "_dispatch_pure", side_effect=stub_pure):
            graph = build_authoring_graph(
                registry=registry,
                client=client,
                output_dir=registry.output_dir,
                retry_budget=1,
                concurrency=AuthoringConcurrencyConfig.all_off(),
            )
            state = GraphState(
                messages=[SystemMessage(content="s"), HumanMessage(content="p")],
                iterations=0,
                tool_call_count=0,
                best_code=None,
                last_validation=None,
                result=None,
                prompt="p",
            )
            graph.invoke(state)

        observed = [
            c["name"] for c in registry.calls
            if c["name"] in {"lookup_rules", "lookup_workspace"}
        ]
        assert observed == ["lookup_rules", "lookup_workspace"]


# ── Phase C: prefetch cache ─────────────────────────────────────────


class TestPrefetchCache:
    def test_cache_store_and_lookup_round_trip(self, tmp_path: Path) -> None:
        registry = _make_registry(tmp_path)
        registry.cache_store(
            "lookup_workspace", {"name_or_guid": "X"}, {"ok": True, "ws": "X"}
        )
        hit = registry.cache_lookup("lookup_workspace", {"name_or_guid": "X"})
        assert hit == {"ok": True, "ws": "X"}

    def test_cache_ignores_non_parallel_safe_tools(self, tmp_path: Path) -> None:
        registry = _make_registry(tmp_path)
        registry.cache_store(
            "simulate_python_draft", {"source": "x"}, {"ok": True}
        )
        assert registry.cache_lookup(
            "simulate_python_draft", {"source": "x"}
        ) is None

    def test_cache_key_normalization(self, tmp_path: Path) -> None:
        registry = _make_registry(tmp_path)
        registry.cache_store(
            "search_labware", {"query": "plate", "limit": 3}, {"ok": True}
        )
        # Same args, different dict order — should still hit.
        hit = registry.cache_lookup(
            "search_labware", {"limit": 3, "query": "plate"}
        )
        assert hit == {"ok": True}

    def test_prefetcher_populates_cache(self, tmp_path: Path) -> None:
        registry = _make_registry(tmp_path)
        seen: list[tuple[str, dict[str, Any]]] = []

        def fake_pure(name: str, payload: dict[str, Any]) -> dict[str, Any]:
            seen.append((name, payload))
            return {"ok": True, "tool": name, "args": payload}

        with patch.object(registry, "_dispatch_pure", side_effect=fake_pure):
            prefetcher = GroundingPrefetcher(
                registry=registry,
                config=PrefetchConfig(deterministic=True, subagent=False, pool_size=2),
            )
            prefetcher.start("transfer water from a trough into a 96-well plate")
            # Wait for the worker pool to drain.
            for fut in prefetcher._futures:
                fut.result(timeout=2.0)
            prefetcher.shutdown()

        names = sorted({n for n, _ in seen})
        # At minimum we expect a workspace lookup, lookup_rules, and at
        # least one search_labware (keyword "trough" or "plate").
        assert "lookup_workspace" in names
        assert "lookup_rules" in names
        assert "search_labware" in names
        # Cache should reflect all of them.
        assert registry.cache_lookup("lookup_workspace", {"name_or_guid": "SAT_Fluent_780_Rev3"})
        assert registry.cache_lookup("lookup_rules", {})


# ── Phase C2: subagent fault tolerance ──────────────────────────────


class TestSubagentPrefetch:
    def test_subagent_exception_is_swallowed(self, tmp_path: Path) -> None:
        registry = _make_registry(tmp_path)

        def boom(prompt: str, timeout_s: float) -> Any:
            raise RuntimeError("simulated subagent crash")

        prefetcher = GroundingPrefetcher(
            registry=registry,
            config=PrefetchConfig(deterministic=False, subagent=True, pool_size=2),
            subagent_invoke=boom,
        )
        prefetcher.start("any prompt")
        # No exception should escape; cache should remain empty.
        for fut in prefetcher._futures:
            fut.result(timeout=2.0)  # _run_subagent swallows internally
        prefetcher.shutdown()
        assert registry._grounding_cache == {}

    def test_subagent_returning_non_list_is_ignored(self, tmp_path: Path) -> None:
        registry = _make_registry(tmp_path)

        def garbage(prompt: str, timeout_s: float) -> Any:
            return {"not": "a list"}

        prefetcher = GroundingPrefetcher(
            registry=registry,
            config=PrefetchConfig(deterministic=False, subagent=True, pool_size=2),
            subagent_invoke=garbage,
        )
        prefetcher.start("any prompt")
        for fut in prefetcher._futures:
            fut.result(timeout=2.0)
        prefetcher.shutdown()
        assert registry._grounding_cache == {}

    def test_subagent_plan_populates_cache(self, tmp_path: Path) -> None:
        registry = _make_registry(tmp_path)

        def plan(prompt: str, timeout_s: float) -> Any:
            return [
                {"tool": "lookup_workspace", "arguments": {"name_or_guid": "WS"}},
                {"tool": "evil_tool", "arguments": {}},  # not parallel-safe; ignored
                {"tool": "search_labware", "arguments": {"query": "trough"}},
            ]

        def fake_pure(name: str, payload: dict[str, Any]) -> dict[str, Any]:
            return {"ok": True, "tool": name, "echo": payload}

        with patch.object(registry, "_dispatch_pure", side_effect=fake_pure):
            prefetcher = GroundingPrefetcher(
                registry=registry,
                config=PrefetchConfig(deterministic=False, subagent=True, pool_size=2),
                subagent_invoke=plan,
            )
            prefetcher.start("doesn't matter")
            for fut in prefetcher._futures:
                fut.result(timeout=2.0)
            prefetcher.shutdown()

        # Both whitelisted tools should land in the cache; "evil_tool" must not.
        assert registry.cache_lookup(
            "lookup_workspace", {"name_or_guid": "WS"}
        ) is not None
        assert registry.cache_lookup(
            "search_labware", {"query": "trough"}
        ) is not None
        assert ("evil_tool", _freeze_arguments({})) not in registry._grounding_cache


# ── helpers: keyword extraction + JSON parsing ──────────────────────


class TestKeywordExtraction:
    def test_no_prompt(self) -> None:
        assert _extract_search_terms("") == []

    def test_dedupes_matching_keywords(self) -> None:
        terms = _extract_search_terms("trough and reservoir")
        # Both keywords map to overlapping seeds; dedup keeps each once.
        assert len(terms) == len(set(terms))
        assert "100ml Trough" in terms

    def test_unrelated_prompt_returns_empty(self) -> None:
        assert _extract_search_terms("hello world") == []


class TestSubagentJsonParse:
    def test_plain_json_list(self) -> None:
        out = _parse_subagent_json('[{"tool": "lookup_workspace", "arguments": {}}]')
        assert out == [{"tool": "lookup_workspace", "arguments": {}}]

    def test_fenced_json(self) -> None:
        text = "```json\n[{\"tool\": \"lookup_rules\", \"arguments\": {}}]\n```"
        out = _parse_subagent_json(text)
        assert out == [{"tool": "lookup_rules", "arguments": {}}]

    def test_json_embedded_in_prose(self) -> None:
        text = 'Here you go: [{"tool": "lookup_rules", "arguments": {}}]. Done.'
        out = _parse_subagent_json(text)
        assert out == [{"tool": "lookup_rules", "arguments": {}}]

    def test_empty_or_invalid_returns_empty_list(self) -> None:
        assert _parse_subagent_json("") == []
        assert _parse_subagent_json("not json at all") == []


# ── category agents ──────────────────────────────────────────────────


class TestCategorySelection:
    def test_always_on_categories_always_selected(self) -> None:
        selected = _select("", DEFAULT_CATEGORIES)
        names = {c.name for c in selected}
        assert "workspace" in names
        assert "rules" in names

    def test_keyword_triggers_select_relevant_category(self) -> None:
        selected = _select(
            "transfer 20 uL water from a trough into a 96-well plate",
            DEFAULT_CATEGORIES,
        )
        names = {c.name for c in selected}
        assert "troughs" in names
        assert "plates" in names
        # Magnet shouldn't fire for a plain transfer.
        assert "magnets" not in names

    def test_unrelated_prompt_only_always_on(self) -> None:
        selected = _select("hello there", DEFAULT_CATEGORIES)
        # liquid_classes triggers on "transfer/aspirate/etc." which aren't
        # present in "hello there", so only the always_on subset fires.
        names = {c.name for c in selected}
        always_on = {c.name for c in DEFAULT_CATEGORIES if c.always_on}
        assert names == always_on


class TestCategoryAgentRunner:
    def test_runner_populates_cache_for_each_agent(self, tmp_path: Path) -> None:
        registry = _make_registry(tmp_path)

        # Stub bound LM: returns one tool_call per agent.
        class _FakeBound:
            def __init__(self, tool_name: str) -> None:
                self.tool_name = tool_name

            def invoke(self, messages: list[Any]) -> AIMessage:
                return AIMessage(content="", tool_calls=[
                    {"name": self.tool_name, "args": {"q": "x"}, "id": "1"},
                ])

        # Stub client: returns a different bound based on the toolset.
        class _FakeClient:
            def bind_tools(self, tools: list[Any]) -> _FakeBound:
                names = [getattr(t, "name", "?") for t in tools]
                # The category agent's first allowed tool dictates which
                # tool_call we'll return.
                return _FakeBound(names[0])

        # Stub _dispatch_pure so we don't hit the real catalog.
        from concurrent.futures import ThreadPoolExecutor
        from unittest.mock import patch

        def fake_pure(name: str, payload: dict[str, Any]) -> dict[str, Any]:
            return {"ok": True, "tool": name, "echo": payload}

        # Use a tiny custom set of categories so the test is deterministic.
        cats = (
            CategoryAgent(
                name="t_workspace",
                description="ws",
                keyword_triggers=(),
                allowed_tools=("lookup_workspace",),
                system_prompt_extra="ws",
                always_on=True,
            ),
            CategoryAgent(
                name="t_rules",
                description="rules",
                keyword_triggers=(),
                allowed_tools=("lookup_rules",),
                system_prompt_extra="rules",
                always_on=True,
            ),
            CategoryAgent(
                name="t_plates",
                description="plates",
                keyword_triggers=("plate",),
                allowed_tools=("search_labware",),
                system_prompt_extra="plates",
            ),
        )

        with patch.object(registry, "_dispatch_pure", side_effect=fake_pure):
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = run_category_agents(
                    prompt="please transfer to a 96-well plate",
                    registry=registry,
                    client=_FakeClient(),
                    pool=pool,
                    categories=cats,
                    timeout_s=5.0,
                )

        names_run = {r.category for r in results}
        assert names_run == {"t_workspace", "t_rules", "t_plates"}
        assert all(r.ok for r in results)
        assert all(r.tool_calls == 1 for r in results)
        assert all(r.cache_writes == 1 for r in results)
        # Cache should hold the three lookups.
        assert registry.cache_lookup("lookup_workspace", {"q": "x"}) is not None
        assert registry.cache_lookup("lookup_rules", {"q": "x"}) is not None
        assert registry.cache_lookup("search_labware", {"q": "x"}) is not None

    def test_runner_skips_off_script_tool_calls(self, tmp_path: Path) -> None:
        """If the model emits a tool not in `allowed_tools`, drop it."""
        registry = _make_registry(tmp_path)

        class _FakeBound:
            def invoke(self, messages: list[Any]) -> AIMessage:
                return AIMessage(content="", tool_calls=[
                    {"name": "compile_and_simulate", "args": {"source": "x"}, "id": "1"},
                    {"name": "lookup_workspace", "args": {"name_or_guid": "ws"}, "id": "2"},
                ])

        class _FakeClient:
            def bind_tools(self, tools: list[Any]) -> _FakeBound:
                return _FakeBound()

        from concurrent.futures import ThreadPoolExecutor
        from unittest.mock import patch

        def fake_pure(name: str, payload: dict[str, Any]) -> dict[str, Any]:
            return {"ok": True, "tool": name}

        cats = (
            CategoryAgent(
                name="t_ws",
                description="ws",
                keyword_triggers=(),
                allowed_tools=("lookup_workspace",),
                system_prompt_extra="ws",
                always_on=True,
            ),
        )

        with patch.object(registry, "_dispatch_pure", side_effect=fake_pure):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = run_category_agents(
                    prompt="anything",
                    registry=registry,
                    client=_FakeClient(),
                    pool=pool,
                    categories=cats,
                )

        assert results[0].cache_writes == 1
        assert registry.cache_lookup("lookup_workspace", {"name_or_guid": "ws"})
        # The off-script `compile_and_simulate` was filtered out.
        assert registry.cache_lookup("compile_and_simulate", {"source": "x"}) is None

    def test_runner_tolerates_invoke_failure(self, tmp_path: Path) -> None:
        registry = _make_registry(tmp_path)

        class _Crash:
            def invoke(self, messages: list[Any]) -> AIMessage:
                raise RuntimeError("simulated")

        class _FakeClient:
            def bind_tools(self, tools: list[Any]) -> _Crash:
                return _Crash()

        from concurrent.futures import ThreadPoolExecutor

        cats = (
            CategoryAgent(
                name="boom",
                description="b",
                keyword_triggers=(),
                allowed_tools=("lookup_workspace",),
                system_prompt_extra="b",
                always_on=True,
            ),
        )
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = run_category_agents(
                prompt="x",
                registry=registry,
                client=_FakeClient(),
                pool=pool,
                categories=cats,
            )
        assert results[0].ok is False
        assert "simulated" in (results[0].error or "")
        # Cache stays empty.
        assert registry._grounding_cache == {}

    def test_runner_rejects_non_parallel_safe_tools(self, tmp_path: Path) -> None:
        registry = _make_registry(tmp_path)

        class _FakeBound:
            def invoke(self, messages: list[Any]) -> AIMessage:
                return AIMessage(content="", tool_calls=[])

        class _FakeClient:
            def bind_tools(self, tools: list[Any]) -> _FakeBound:
                return _FakeBound()

        from concurrent.futures import ThreadPoolExecutor

        cats = (
            CategoryAgent(
                name="bad",
                description="bad",
                keyword_triggers=(),
                allowed_tools=("compile_and_simulate",),  # not parallel-safe
                system_prompt_extra="b",
                always_on=True,
            ),
        )
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = run_category_agents(
                prompt="x",
                registry=registry,
                client=_FakeClient(),
                pool=pool,
                categories=cats,
            )
        assert results[0].ok is False
        assert "non-parallel-safe" in (results[0].error or "")


# ── ground_in_parallel tool ─────────────────────────────────────────


class TestGroundInParallelTool:
    def test_returns_unavailable_without_client(self, tmp_path: Path) -> None:
        registry = _make_registry(tmp_path)
        result = registry.dispatch(
            "ground_in_parallel",
            {"categories": ["plates", "magnets"]},
        )
        assert result["ok"] is False
        assert result["category"] == "subagent_unavailable"

    def test_validates_empty_categories(self, tmp_path: Path) -> None:
        registry = _make_registry(tmp_path)
        registry.configure_subagent_client(client=object())
        result = registry.dispatch("ground_in_parallel", {"categories": []})
        assert result["ok"] is False
        assert result["category"] == "bad_tool_arguments"

    def test_unknown_categories_are_reported(self, tmp_path: Path) -> None:
        registry = _make_registry(tmp_path)
        registry.configure_subagent_client(client=object())
        result = registry.dispatch(
            "ground_in_parallel",
            {"categories": ["not_a_real_category", "also_fake"]},
        )
        assert result["ok"] is False
        # No real categories matched.
        assert "category" in result and result["category"] == "bad_tool_arguments"

    def test_runs_requested_categories_ignoring_keyword_filter(
        self, tmp_path: Path
    ) -> None:
        """The tool should run named categories even if their keywords
        don't appear in the user prompt — the LM has decided they apply."""
        registry = _make_registry(tmp_path)
        registry.current_prompt = "completely unrelated text"

        class _FakeBound:
            def invoke(self, messages: list[Any]) -> AIMessage:
                return AIMessage(content="", tool_calls=[
                    {"name": "search_labware", "args": {"query": "magnet"}, "id": "1"},
                ])

        class _FakeClient:
            def bind_tools(self, tools: list[Any]) -> _FakeBound:
                return _FakeBound()

        from unittest.mock import patch

        def fake_pure(name: str, payload: dict[str, Any]) -> dict[str, Any]:
            return {"ok": True, "tool": name, "echo": payload}

        registry.configure_subagent_client(_FakeClient(), pool_size=4, timeout_s=5.0)

        with patch.object(registry, "_dispatch_pure", side_effect=fake_pure):
            # Note "magnets" wouldn't normally fire on this prompt; the
            # tool path should run it regardless.
            result = registry.ground_in_parallel(categories=["magnets"])

        assert result["ok"] is True
        assert "magnets" in result["categories_run"]
        assert result["cache_writes"] == 1
        assert registry.cache_lookup("search_labware", {"query": "magnet"})


class TestOrchestratorGrounding:
    def test_category_agents_receive_full_multiturn_context(
        self, tmp_path: Path
    ) -> None:
        registry = _make_registry(tmp_path)
        registry.set_authoring_context(
            original_prompt="Author an AMPure cleanup for 96 samples.",
            latest_user_text="20 uL",
            user_history_text=(
                "Author an AMPure cleanup for 96 samples.\n"
                "20 uL"
            ),
        )
        seen_prompts: list[str] = []

        class _FakeBound:
            def invoke(self, messages: list[Any]) -> AIMessage:
                seen_prompts.append(str(messages[-1].content))
                return AIMessage(content="", tool_calls=[
                    {"name": "search_labware", "args": {"query": "magnet"}, "id": "1"},
                ])

        class _FakeClient:
            def bind_tools(self, tools: list[Any]) -> _FakeBound:
                return _FakeBound()

        with patch.object(
            registry,
            "_dispatch_pure",
            return_value={"ok": True, "matches": []},
        ):
            result = GroundingCoordinator(
                registry=registry,
                client=_FakeClient(),
                pool_size=2,
                timeout_s=5.0,
            ).run(registry.authoring_context(), categories=["magnets"])

        assert result["ok"] is True
        assert seen_prompts
        assert "AMPure cleanup" in seen_prompts[0]
        assert "20 uL" in seen_prompts[0]

    def test_graph_auto_grounding_runs_after_declare_intent(
        self, tmp_path: Path
    ) -> None:
        registry = _make_registry(tmp_path)
        registry.set_authoring_context(
            original_prompt="Transfer 20 uL from source to destination.",
            latest_user_text="Transfer 20 uL from source to destination.",
            user_history_text="Transfer 20 uL from source to destination.",
        )

        class _MainClient:
            def __init__(self) -> None:
                self.calls = 0

            def bind_tools(self, tools: list[Any]) -> "_MainClient":
                return self

            def invoke(self, messages: list[Any]) -> AIMessage:
                self.calls += 1
                if self.calls == 1:
                    return AIMessage(content="", tool_calls=[{
                        "name": "declare_intent",
                        "args": {
                            "target_volume_ul": 20.0,
                            "source_label": "SourcePlate",
                            "destination_label": "DestPlate",
                            "destination_wells": ["A1"],
                            "liquid_class": "Water Free Single",
                        },
                        "id": "intent",
                    }])
                return AIMessage(content="done", tool_calls=[])

        class _GroundingBound:
            def invoke(self, messages: list[Any]) -> AIMessage:
                return AIMessage(content="", tool_calls=[
                    {"name": "lookup_liquid_class", "args": {"name": "Water Free Single"}, "id": "lc"},
                ])

        class _GroundingClient:
            def bind_tools(self, tools: list[Any]) -> _GroundingBound:
                return _GroundingBound()

        registry.configure_subagent_client(_GroundingClient(), pool_size=2, timeout_s=5.0)

        def fake_pure(name: str, payload: dict[str, Any]) -> dict[str, Any]:
            if name == "lookup_liquid_class":
                return {"ok": True, "liquid_class": {"name": payload["name"]}}
            return AuthoringToolRegistry._dispatch_pure(registry, name, payload)

        graph = build_authoring_graph(
            registry=registry,
            client=_MainClient(),
            output_dir=tmp_path / "out",
            retry_budget=1,
            concurrency=AuthoringConcurrencyConfig(
                parallel_tool_dispatch=False,
                speculative_compile=False,
                prefetch_deterministic=False,
                orchestrator_grounding=True,
                prefetch_subagent=False,
            ),
        )
        with patch.object(registry, "_dispatch_pure", side_effect=fake_pure):
            graph.invoke({
                "messages": [
                    SystemMessage(content="system"),
                    HumanMessage(content="Transfer 20 uL from source to destination."),
                ],
                "iterations": 0,
                "tool_call_count": 0,
                "best_code": None,
                "last_validation": None,
                "current_group_index": 0,
                "last_accepted_source_hash": None,
                "result": None,
                "prompt": registry.current_prompt,
            })

        assert registry.cache_lookup(
            "lookup_liquid_class",
            {"name": "Water Free Single"},
        )

    def test_ground_in_parallel_allowed_before_object_approval(
        self, tmp_path: Path
    ) -> None:
        registry = _make_registry(tmp_path)
        registry.set_authoring_context(
            original_prompt="Author an AMPure cleanup for 96 samples.",
            latest_user_text="Author an AMPure cleanup for 96 samples.",
            user_history_text="Author an AMPure cleanup for 96 samples.",
        )

        class _MainClient:
            def __init__(self) -> None:
                self.calls = 0

            def bind_tools(self, tools: list[Any]) -> "_MainClient":
                return self

            def invoke(self, messages: list[Any]) -> AIMessage:
                self.calls += 1
                if self.calls == 1:
                    return AIMessage(content="", tool_calls=[{
                        "name": "ground_in_parallel",
                        "args": {"categories": ["magnets"]},
                        "id": "ground",
                    }])
                return AIMessage(content="What volume?", tool_calls=[])

        class _GroundingBound:
            def invoke(self, messages: list[Any]) -> AIMessage:
                return AIMessage(content="", tool_calls=[
                    {"name": "search_labware", "args": {"query": "magnet"}, "id": "magnet"},
                ])

        class _GroundingClient:
            def bind_tools(self, tools: list[Any]) -> _GroundingBound:
                return _GroundingBound()

        registry.configure_subagent_client(_GroundingClient(), pool_size=2, timeout_s=5.0)
        graph = build_authoring_graph(
            registry=registry,
            client=_MainClient(),
            output_dir=tmp_path / "out",
            retry_budget=1,
            concurrency=AuthoringConcurrencyConfig(
                parallel_tool_dispatch=False,
                speculative_compile=False,
                prefetch_deterministic=False,
                orchestrator_grounding=False,
                prefetch_subagent=False,
            ),
        )
        graph.invoke({
            "messages": [
                SystemMessage(content="system"),
                HumanMessage(content="Author an AMPure cleanup for 96 samples."),
            ],
            "iterations": 0,
            "tool_call_count": 0,
            "best_code": None,
            "last_validation": None,
            "current_group_index": 0,
            "last_accepted_source_hash": None,
            "result": None,
            "prompt": registry.current_prompt,
        })

        assert [call["name"] for call in registry.calls] == ["ground_in_parallel"]
        assert registry.cache_lookup("search_labware", {"query": "magnet"})


class TestRunCategoryAgentsTimeoutAccounting:
    def test_timeout_reports_real_elapsed_time(self, tmp_path: Path) -> None:
        registry = _make_registry(tmp_path)

        class _Slow:
            def invoke(self, messages: list[Any]) -> AIMessage:
                time.sleep(2.0)
                return AIMessage(content="", tool_calls=[])

        class _FakeClient:
            def bind_tools(self, tools: list[Any]) -> _Slow:
                return _Slow()

        from concurrent.futures import ThreadPoolExecutor

        cats = (
            CategoryAgent(
                name="t",
                description="t",
                keyword_triggers=(),
                allowed_tools=("lookup_workspace",),
                system_prompt_extra="t",
                always_on=True,
            ),
        )
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = run_category_agents(
                prompt="x",
                registry=registry,
                client=_FakeClient(),
                pool=pool,
                categories=cats,
                timeout_s=0.3,  # too short — agent will time out
            )
        # The result should be marked as timed-out and elapsed_s should
        # reflect ~the timeout, not 0.0s.
        assert len(results) == 1
        assert results[0].ok is False
        assert "timeout" in (results[0].error or "")
        assert results[0].elapsed_s >= 0.25


# ── speculative compile: reuse on success path ──────────────────────


class TestSpeculativeCompile:
    def test_speculative_future_consumed_when_sim_succeeds(
        self, tmp_path: Path
    ) -> None:
        """When simulate_python_draft succeeds and triggers the inline compile,
        the speculative future is consumed instead of dispatching afresh."""
        registry = _make_registry(tmp_path)

        compile_call_count = 0

        def fake_pure(name: str, payload: dict[str, Any]) -> dict[str, Any]:
            nonlocal compile_call_count
            if name == "compile_and_simulate":
                compile_call_count += 1
                return {
                    "ok": True,
                    "success": True,
                    "python_build_ok": True,
                    "compile_ok": True,
                    "strict_simulation_ok": True,
                    "python_path": str(tmp_path / "x.py"),
                    "xscr_path": str(tmp_path / "x.xscr"),
                    "attempt_index": 1,
                }
            if name == "simulate_python_draft":
                return {"ok": True, "stage": "strict_simulation"}
            return {"ok": True}

        # Pre-populate workflow + approvals so the simulate-success path
        # triggers compile rather than asking for more grounding.
        registry.declare_protocol_workflow(
            protocol_name="t",
            summary="t",
            variables=[{"name": "v", "default": "x", "sim_value": "x"}],
            labware=[{"label": "L"}],
            groups=[
                {"name": "Variables", "objective": "v"},
                {"name": "Labware Placement", "objective": "lp"},
            ],
        )
        registry.object_draft_approved = True
        registry.functional_group_plan_approved = True
        # Pre-seed grounding so simulate doesn't get blocked by the
        # missing-grounding nudge.
        for entry in [
            {"name": "lookup_workspace", "arguments": {}, "result": {"ok": True}},
            {"name": "search_labware", "arguments": {}, "result": {"ok": True}},
            {"name": "lookup_liquid_class", "arguments": {}, "result": {"ok": True}},
            {"name": "lookup_rules", "arguments": {}, "result": {"ok": True}},
        ]:
            registry.calls.append(entry)

        # A single simulate_python_draft tool call. The graph should:
        #   1. Speculative-launch compile_and_simulate.
        #   2. Run simulate (pre-empted by the patched _dispatch_pure).
        #   3. On success, advance the workflow index past Labware Placement.
        # Both Variables and Labware Placement need to be completed for the
        # final compile to fire, so we issue two simulate calls.
        responses = [
            _ai_with_tool_calls([
                {
                    "name": "simulate_python_draft",
                    "args": {"source": "def build_worktable():\n    return None\n"},
                    "id": "s1",
                },
            ]),
            _ai_with_tool_calls([
                {
                    "name": "simulate_python_draft",
                    "args": {"source": "def build_worktable():\n    return None\n"},
                    "id": "s2",
                },
            ]),
            AIMessage(content="ok"),
        ]
        client = FakeMessagesListChatModel(responses=responses)

        with patch.object(registry, "_dispatch_pure", side_effect=fake_pure):
            graph = build_authoring_graph(
                registry=registry,
                client=client,
                output_dir=registry.output_dir,
                retry_budget=2,
                concurrency=AuthoringConcurrencyConfig(
                    parallel_tool_dispatch=False,
                    speculative_compile=True,
                    prefetch_deterministic=False,
                    prefetch_subagent=False,
                ),
            )
            graph.invoke(GraphState(
                messages=[SystemMessage(content="s"), HumanMessage(content="p")],
                iterations=0,
                tool_call_count=0,
                best_code=None,
                last_validation=None,
                result=None,
                prompt="p",
            ))

        # Speculative compile should have run for each simulate call. The
        # exact count is implementation-detail; what matters is that the
        # success path didn't trigger an EXTRA inline compile beyond the
        # speculative one. Two simulates → two compiles, no more.
        assert compile_call_count == 2
