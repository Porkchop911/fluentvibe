"""Worktable — the root of a tecanlab protocol.

Collects IR steps, owns the pipetting heads + gripper, exposes sim-time
values for the simulator to consume. Snapshots are populated when
`simulate()` is called (lazy — the twin is *not* mutated by author method
calls; it is reconstructed by the Simulator from the IR list).
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator, Optional, Union

from .ir.schema import (
    AddLabwareStep, CommentStep, ConditionalStep, ExecuteApplicationStep,
    ExportVariableStep, GenericStep, Group, ImportVariableStep, LoopStep,
    Protocol, QueryVariableStep, RemoveLabwareStep, ScriptGroupStep,
    SetLocationStep, SetVariableStep, StartTimerStep, Step, UserPromptStep,
    WaitForTimerStep, WaitStep,
)
from .labware.base import Labware

if TYPE_CHECKING:
    from .simulator.snapshots import Snapshot
    from .simulator.report import SimulationReport


class Worktable:
    """The Tecan worktable.

    Authoring API:
        - `place(labware, location, position)` — add labware to a slot.
        - `group(name)` — start a new protocol group (Setup / Transfer / …).
        - `set_sim_value(name, value)` — provide a concrete value the
          simulator should use for a runtime variable.
        - `simulate()` — replay the IR through the Simulator, populating
          `snapshots`.
        - `compile(out_path)` — render `.xscr` via the vendored renderer.
    """

    def __init__(self, *, name: str = "Untitled Protocol", comment: str = "") -> None:
        self.name: str = name
        self.comment: str = comment

        # Slot map: (location, position) → list[Labware] bottom→top.
        # Tracked at authoring time so `gripper.move(onto=...)` and
        # `place(...)`'s occupied-slot check have something to work with.
        self.slot_map: dict[tuple[str, int], list[Labware]] = {}

        # Optional valid-slot whitelist set by `from_workspace`. When non-empty,
        # `place()` raises `InvalidSlotError` for slots outside the set.
        self.valid_slots: Optional[set[tuple[str, int]]] = None
        self.workspace_name: Optional[str] = None
        self.workspace_guid: Optional[str] = None

        # Groups + steps collected as the author calls methods.
        self._groups: list[Group] = []
        self._active_group: Optional[Group] = None
        self.protocol_variables: dict[str, Union[float, int, str]] = {}

        # Sim-time values — required for any runtime variable the simulator
        # must resolve (loop counts, conditional predicates, imports).
        self.sim_values: dict[str, Any] = {}

        # Stack of step-list targets. While a `with wt.loop(...)` / `with
        # wt.conditional(...)` block is active, _emit() appends to the
        # topmost list (the loop/conditional body); otherwise it appends
        # to the active group.
        self._emit_target_stack: list[list[Step]] = []

        # Filled by `simulate()`.
        self.snapshots: list["Snapshot"] = []
        self.simulation_report: Optional["SimulationReport"] = None

        # Devices.
        from .heads import LiHa, MCA96Head
        from .gripper import Gripper
        self.mca96: MCA96Head = MCA96Head(self)
        self.liha: LiHa = LiHa(self)
        self.gripper: Gripper = Gripper(self)

    # ── Workspace loader ────────────────────────────────────────────

    @classmethod
    def from_workspace(
        cls,
        name: str,
        *,
        workspace_guid: Optional[str] = None,
        auto_place: bool = True,
        protocol_name: str = "",
        comment: str = "",
    ) -> "Worktable":
        """Build a Worktable from a FluentControl workspace (`.xwsp`).

        - Registers the valid `(location, position)` slots from the workspace.
          `place()` raises `InvalidSlotError` for slots outside this set.
        - If `auto_place=True` (default), each occupant the workspace already
          has placed is instantiated via the catalog's category-to-class
          dispatch and placed on its slot.

        The catalog index must be built; raises if not.
        """
        from .catalog.catalog import (
            index_exists, resolve_by_name, resolve_workspace_by_guid,
            resolve_workspace_by_name,
        )
        from .catalog.xcmp import load_xwsp
        from .labware import CATEGORY_TO_CLASS
        from .simulator.invariants import MissingSimValueError

        if not index_exists():
            raise MissingSimValueError(
                "Catalog index is not built; cannot resolve workspace. "
                "Run `tecanlab catalog refresh` first."
            )

        ws_by_guid = resolve_workspace_by_guid(workspace_guid) if workspace_guid else None
        ws_by_name = resolve_workspace_by_name(name) if name else None
        ws_entry = ws_by_guid or ws_by_name
        if ws_by_guid and ws_by_name:
            guid_path = ws_by_guid.file_path.resolve()
            name_path = ws_by_name.file_path.resolve()
            if guid_path == name_path:
                ws_entry = ws_by_guid
            else:
                raise ValueError(
                    "Workspace reference is ambiguous: "
                    f"GUID {workspace_guid!r} resolved to {ws_by_guid.name!r} at {guid_path!s}, "
                    f"but name {name!r} resolved to {ws_by_name.name!r} at {name_path!s}."
                )
        if ws_entry is None:
            if workspace_guid and name:
                raise ValueError(
                    "Workspace reference could not be resolved from the local catalog index: "
                    f"name={name!r}, guid={workspace_guid!r}. "
                    "Run `tecanlab catalog refresh` if the workspace is installed locally."
                )
            lookup = workspace_guid or name
            raise ValueError(f"Workspace {lookup!r} not found in catalog index")

        ws = load_xwsp(ws_entry.file_path)

        wt = cls(name=protocol_name or name, comment=comment)
        wt.workspace_name = ws.name
        wt.workspace_guid = ws.guid

        def _workspace_position(site_path: tuple[int, ...]) -> int:
            if len(site_path) >= 3 and site_path[-1] == 0:
                return site_path[-2] + 1
            return site_path[-1] + 1

        slot_position_by_site: dict[tuple[tuple[int, ...], str], int] = {}
        slot_counts_by_location: dict[str, int] = {}
        seen_sites: set[tuple[tuple[int, ...], str]] = set()
        generic_site_paths: list[tuple[int, ...]] = []
        seen_generic_site_paths: set[tuple[int, ...]] = set()

        def _occupant_position(occ: Any) -> int:
            site_path = getattr(occ, "site_path", None)
            location_name = getattr(occ, "base_location_identifier", None)
            if site_path and location_name:
                mapped = slot_position_by_site.get((site_path, location_name))
                if mapped is not None:
                    return mapped
            if site_path:
                return _workspace_position(site_path)
            return int(getattr(occ, "site_index", 0)) + 1
        # Build the valid-slots whitelist from EVERY visited site (occupied or
        # not). XWSP site indices are 0-based; FluentControl positions are
        # 1-based — translate at the boundary.
        default_loc = ws.location_names[0] if ws.location_names else "Site"
        valid: set[tuple[str, int]] = set()
        for site_path, base_loc in ws.available_sites:
            if not site_path:
                continue
            if ws.name == "780_Empty" and site_path not in seen_generic_site_paths:
                seen_generic_site_paths.add(site_path)
                generic_site_paths.append(site_path)
            if not base_loc:
                continue
            site_key = (site_path, base_loc)
            if site_key in seen_sites:
                continue
            seen_sites.add(site_key)
            position = slot_position_by_site.get(site_key)
            if position is None:
                position = slot_counts_by_location.get(base_loc, 0) + 1
                slot_counts_by_location[base_loc] = position
                slot_position_by_site[site_key] = position
            valid.add((base_loc, position))
        if ws.name == "780_Empty":
            for position, _ in enumerate(generic_site_paths, start=1):
                valid.add(("Site", position))
        wt.valid_slots = valid

        if auto_place:
            wt.group("Worktable Setup")
            for occ in ws.occupants:
                catalog_entry = resolve_by_name(occ.catalog_name)
                loc = occ.base_location_identifier or default_loc
                position = _occupant_position(occ)
                if catalog_entry is None:
                    raise ValueError(
                        f"Workspace {ws.name!r} requires occupant {occ.catalog_name!r} "
                        f"at {(loc, position)!r}, but that catalog name is not installed "
                        "in the local tecanlab catalog index."
                    )
                cls_for_category = CATEGORY_TO_CLASS.get(
                    catalog_entry.category, CATEGORY_TO_CLASS["fixed_deck"]
                )
                position = _occupant_position(occ)
                auto_label = f"{occ.catalog_name}@{position}"
                lw = cls_for_category(auto_label, catalog=occ.catalog_name)
                loc = occ.base_location_identifier or default_loc
                wt.place(lw, loc, position)

        return wt

    # ── Authoring API ───────────────────────────────────────────────

    def group(self, name: str) -> None:
        """Start a new step group (e.g. 'Setup', 'Transfer')."""
        self._active_group = Group(name=name, steps=[])
        self._groups.append(self._active_group)

    @contextmanager
    def nested_group(self, name: str) -> Iterator[ScriptGroupStep]:
        """Emit a nested FluentControl script group."""
        group_step = ScriptGroupStep(name=name, steps=[])
        self._emit_target_stack.append(group_step.steps)
        try:
            yield group_step
        finally:
            self._emit_target_stack.pop()
            self._emit(group_step)

    def declare_variable(
        self, name: str, default: Union[float, int, str]
    ) -> None:
        """Declare a FluentControl protocol variable with a default value."""
        self.protocol_variables[name] = default

    def set_sim_value(self, name: str, value: Any) -> None:
        """Provide a concrete value the simulator uses for runtime variable
        references (loop counts, conditional predicates, imports)."""
        self.sim_values[name] = value

    def set_variable(self, name: str, value: Union[float, int, str]) -> None:
        self._emit(SetVariableStep(variable_name=name, value=value))

    def wait(self, duration_seconds: Union[int, float, str]) -> None:
        self._emit(WaitStep(duration_seconds=duration_seconds))

    def add_comment(self, text: str) -> None:
        self._emit(CommentStep(comment=text))

    def user_prompt(self, prompt: str, *, timeout: int = 0) -> None:
        self._emit(UserPromptStep(prompt=prompt, timeout=timeout))

    def start_timer(self, timer: int = 1) -> None:
        self._emit(StartTimerStep(timer=timer))

    def wait_for_timer(self, timer: int, duration_seconds: Union[int, float, str]) -> None:
        self._emit(WaitForTimerStep(timer=timer, duration_seconds=duration_seconds))

    def export_variables(
        self,
        variables: list[str],
        export_file: str,
        *,
        write_header: bool = False,
        replace_existing_file: bool = False,
        export_strings_with_quotes: bool = False,
        delimiter_code: int = 59,
    ) -> None:
        self._emit(ExportVariableStep(
            variables=variables,
            export_file=export_file,
            write_header=write_header,
            replace_existing_file=replace_existing_file,
            export_strings_with_quotes=export_strings_with_quotes,
            delimiter_code=delimiter_code,
        ))

    def import_variables(
        self,
        variables: list[str],
        import_file: str,
        *,
        read_line: bool = False,
        line: int = 1,
        start_in_column: bool = False,
        column: int = 1,
        has_header: bool = False,
        delimiter_code: int = 59,
    ) -> None:
        self._emit(ImportVariableStep(
            variables=variables,
            import_file=import_file,
            read_line=read_line,
            line=line,
            start_in_column=start_in_column,
            column=column,
            has_header=has_header,
            delimiter_code=delimiter_code,
        ))

    def query_variable(
        self,
        variable_name: str,
        query_prompt: str,
        *,
        limit_range: bool = False,
    ) -> None:
        self._emit(QueryVariableStep(
            variable_name=variable_name,
            query_prompt=query_prompt,
            limit_range=limit_range,
        ))

    def execute_application(
        self,
        application: str,
        *,
        arguments: str = "",
        wait: bool = True,
        store_return: bool = False,
        variable: str = "",
    ) -> None:
        self._emit(ExecuteApplicationStep(
            application=application,
            arguments=arguments,
            wait=wait,
            store_return=store_return,
            variable=variable,
        ))

    def set_location(
        self,
        labware: Union[Labware, str],
        location: str,
        site: int,
        *,
        rotation: int = 0,
    ) -> None:
        label = labware.label if isinstance(labware, Labware) else labware
        self._emit(SetLocationStep(
            labware=label,
            location=location,
            site=site,
            rotation=rotation,
        ))

    def generic_step(self, step_type: str, **parameters: Any) -> None:
        """Emit a recognized but not yet modeled FluentControl command."""
        self._emit(GenericStep(step_type=step_type, parameters=parameters))

    def raw_xml_step(self, step_type: str, raw_xml: str) -> None:
        """Emit an opaque FluentControl command preserved from a decompiled script."""
        self._emit(GenericStep(step_type=step_type, parameters={"raw_xml": raw_xml}))

    @contextmanager
    def loop(
        self,
        *,
        times: Union[int, str],
        name: str = "Loop",
    ) -> Iterator[LoopStep]:
        """Emit a `LoopStep` whose body is everything authored inside the
        `with` block.

        ``times`` is either a literal `int` (loop runs that many times) or a
        `str` naming a runtime variable. The variable's value at simulation
        time is resolved through `set_sim_value(name, value)`.
        """
        loop_step = LoopStep(
            name=name,
            iterations=times if isinstance(times, int) else 1,
            loop_variable=times if isinstance(times, str) else None,
            number_of_loops=times,
            steps=[],
        )
        self._emit_target_stack.append(loop_step.steps)
        try:
            yield loop_step
        finally:
            self._emit_target_stack.pop()
            self._emit(loop_step)

    @contextmanager
    def conditional(
        self,
        *,
        left: str,
        op: str,
        right: Union[int, float, str, bool],
        right_is_variable: bool = False,
        name: str = "If",
    ) -> Iterator[ConditionalStep]:
        """Emit a `ConditionalStep` whose then-branch is everything authored
        inside the `with` block.

        ``left`` is a runtime variable name; ``op`` is one of the supported
        comparators (``==``, ``!=``, ``<``, ``<=``, ``>``, ``>=``); ``right``
        is a literal or, with ``right_is_variable=True``, another variable
        name. else-branches are not authored via this context manager in
        v1.1; populate ``cond_step.else_steps`` directly if needed.
        """
        cond_step = ConditionalStep(
            name=name,
            left_variable=left,
            operator=op,
            right_value=right,
            right_is_variable=right_is_variable,
            then_steps=[],
            else_steps=[],
        )
        self._emit_target_stack.append(cond_step.then_steps)
        try:
            yield cond_step
        finally:
            self._emit_target_stack.pop()
            self._emit(cond_step)

    @contextmanager
    def else_branch(self, conditional: ConditionalStep) -> Iterator[ConditionalStep]:
        """Append authored steps to an existing conditional's else branch."""
        self._emit_target_stack.append(conditional.else_steps)
        try:
            yield conditional
        finally:
            self._emit_target_stack.pop()

    def place(
        self,
        labware: Labware,
        location: str,
        position: int,
        *,
        allow_occupied: bool = False,
    ) -> Labware:
        """Place labware on the worktable at (location, position).

        Stacking is performed via `gripper.move(onto=...)`, not via place();
        place() refuses an occupied slot at authoring time so accidental
        double-placement surfaces immediately. If the worktable was built via
        `from_workspace`, only slots in `self.valid_slots` are accepted.
        """
        slot = (location, position)
        if self.valid_slots is not None and slot not in self.valid_slots:
            from .simulator.invariants import InvalidSlotError
            raise InvalidSlotError(
                f"Slot {slot!r} is not on workspace {self.workspace_name!r}. "
                f"Valid examples: {sorted(self.valid_slots)[:5]}…"
            )
        if slot in self.slot_map and self.slot_map[slot] and not allow_occupied:
            occupied_by = self.slot_map[slot][-1]
            raise ValueError(
                f"Slot {slot} already occupied by {occupied_by.label!r}. "
                f"Use `gripper.move({labware.label!r}, onto={occupied_by.label!r})` to stack."
            )
        stack = self.slot_map.setdefault(slot, [])
        stack.append(labware)
        labware.slot = slot
        labware.stack_below = list(stack[:-1])
        self._register_child_valid_slots(labware)
        self._emit(AddLabwareStep(
            labware_type=labware.catalog_name,
            label=labware.label,
            location=location,
            position=position,
        ))
        return labware

    def remove(self, labware: Union[Labware, str]) -> None:
        """Remove labware from the worktable."""
        if isinstance(labware, str):
            self._emit(RemoveLabwareStep(labware_name=labware))
            return
        if labware.slot is None:
            raise ValueError(f"Cannot remove {labware.label!r}: not on worktable")
        stack = self.slot_map.get(labware.slot, [])
        if labware in stack:
            stack.remove(labware)
            if not stack:
                del self.slot_map[labware.slot]
        labware.slot = None
        labware.stack_below = []
        self._emit(RemoveLabwareStep(labware_name=labware.label))

    # ── Compile / simulate ──────────────────────────────────────────

    def to_protocol(self) -> Protocol:
        """Build a Protocol IR from the collected steps."""
        protocol = Protocol(
            name=self.name,
            comment=self.comment,
            variables=list(self.protocol_variables.keys()),
            variable_defaults=dict(self.protocol_variables),
            groups=[Group(name=g.name, steps=list(g.steps)) for g in self._groups],
            worktable_guid=self.workspace_guid,
            worktable_name=self.workspace_name,
        )
        protocol.assign_line_numbers()
        return protocol

    def compile(self, out_path: Union[str, Path]) -> Path:
        """Render the protocol to a `.xscr` file at `out_path`."""
        from .compiler import render_protocol
        from .catalog import rewrite_checksum_in_place

        self._require_bound_workspace()
        protocol = self.to_protocol()
        xml = render_protocol(protocol)
        path = Path(out_path)
        path.write_text(xml, encoding="utf-8")
        rewrite_checksum_in_place(path)
        return path

    def simulate(
        self,
        *,
        fail_on_opaque: bool = False,
        min_coverage: Optional[float] = None,
        strict: bool = False,
    ) -> None:
        """Replay the IR through the Simulator, populating `self.snapshots`."""
        from .simulator import Simulator

        sim = Simulator(self)
        sim.run(
            fail_on_opaque=fail_on_opaque,
            min_coverage=min_coverage,
            strict=strict,
        )

    # ── Internal helpers ────────────────────────────────────────────

    def _emit(self, step: Step) -> None:
        if self._emit_target_stack:
            self._emit_target_stack[-1].append(step)
            return
        if self._active_group is None:
            self._active_group = Group(name="Steps", steps=[])
            self._groups.append(self._active_group)
        self._active_group.steps.append(step)

    def _register_child_valid_slots(self, labware: Labware) -> None:
        """Extend valid workspace slots from a placed carrier's child sites."""
        if self.valid_slots is None or getattr(labware, "category", None) != "fixed_deck":
            return

        from .catalog.catalog import resolve_by_name
        from .catalog.xcmp import load_component_site_location_names

        entry = resolve_by_name(labware.catalog_name)
        if entry is None:
            return

        child_locations = load_component_site_location_names(entry.file_path)
        if not child_locations:
            return

        next_position_by_location: dict[str, int] = {}
        for name in child_locations:
            if not name:
                continue
            if name not in next_position_by_location:
                next_position_by_location[name] = max(
                    (pos for loc, pos in self.valid_slots if loc == name),
                    default=0,
                )
            next_position_by_location[name] += 1
            self.valid_slots.add((name, next_position_by_location[name]))

    def _find_unique_labware(self, cls: type) -> Labware:
        matches = [lw for stack in self.slot_map.values() for lw in stack
                   if isinstance(lw, cls)]
        if not matches:
            raise ValueError(f"No {cls.__name__} on the worktable")
        if len(matches) > 1:
            labels = [lw.label for lw in matches]
            raise ValueError(
                f"Multiple {cls.__name__} on the worktable ({labels}); "
                f"pass one explicitly"
            )
        return matches[0]

    def labware_by_label(self, label: str) -> Labware:
        for stack in self.slot_map.values():
            for lw in stack:
                if lw.label == label:
                    return lw
        raise KeyError(f"No labware with label {label!r} on the worktable")

    def _require_bound_workspace(self) -> None:
        if self.workspace_name and self.workspace_guid:
            return
        raise ValueError(
            "Worktable is not bound to a specific FluentControl workspace. "
            "Build it with Worktable.from_workspace(...) before compiling."
        )
