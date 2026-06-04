"""Worktable — the root of a fluentvibe protocol.

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
    ExecuteWorklistStep, ExportVariableStep, GenericStep, Group,
    ImportVariableStep, LegacyDriverMacroStep, LoadWorklistStep, LoopStep,
    Protocol, QueryVariableStep, RemoveLabwareStep, ScriptGroupStep,
    SetLocationStep, SetVariableStep, StartTimerStep, Step, UserPromptStep,
    WaitForTimerStep, WaitStep, WorklistColumnMapping, WorklistImportStep,
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
        self.file_references: list[str] = []

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
                "Run `fluentvibe catalog refresh` first."
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
                    "Run `fluentvibe catalog refresh` if the workspace is installed locally."
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
                        "in the local fluentvibe catalog index."
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

    # ------------------------------------------------------------------
    # Device driver macros (LegacyDriverMacro)
    # ------------------------------------------------------------------

    def legacy_driver_macro(
        self,
        name: str,
        module_name: str,
        execution_settings: Optional[str] = None,
    ) -> None:
        """Emit a raw FluentControl ``LegacyDriverMacro`` device command.

        Binds to an installed driver module by ``module_name`` (e.g.
        ``"SiLA-ODTC"``, ``"inhecoMTC"``). See the typed ``odtc_*`` helpers
        below for the Inheco underdeck ODTC. Note: emitting the command does
        not require the driver to be installed, but running it on hardware does.
        """
        self._emit(LegacyDriverMacroStep(
            name=name,
            module_name=module_name,
            execution_settings=execution_settings,
        ))

    # --- Inheco underdeck ODTC (driver module "SiLA-ODTC") ---

    def odtc_open_door(self) -> None:
        self.legacy_driver_macro("SiLA-ODTC_OpenDoor", "SiLA-ODTC")

    def odtc_close_door(self) -> None:
        self.legacy_driver_macro("SiLA-ODTC_CloseDoor", "SiLA-ODTC")

    def odtc_get_parameters(self) -> None:
        self.legacy_driver_macro("SiLA-ODTC_GetParameters", "SiLA-ODTC")

    def odtc_set_parameters(self, methods_xml_file: str) -> None:
        """Load an ODTC method file (e.g. ``"Annealing.xml"``)."""
        self.legacy_driver_macro(
            "SiLA-ODTC_SetParameters", "SiLA-ODTC",
            f"Parameter:MethodsXML:String:File:{methods_xml_file}",
        )

    def odtc_execute_method(self, method_name: str) -> None:
        """Run a loaded ODTC method by name (e.g. ``"Annealing"``)."""
        self.legacy_driver_macro("SiLA-ODTC_ExecuteMethod", "SiLA-ODTC", method_name)

    # --- Inheco MTC heated/cooled positions (driver module "inhecoMTC") ---

    def inheco_set_temperature(self, settings: Optional[str] = None) -> None:
        """Inheco MTC SetTemperature macro (used for ODTC pre-heat staging)."""
        self.legacy_driver_macro("inhecoMTC_SetTemperature", "inhecoMTC", settings)

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

    def worklist(
        self,
        source_path: Union[str, Path],
        *,
        gwl_path: Optional[Union[str, Path]] = None,
        liquid_class: Optional[str] = None,
        diti_type: str = "TOOLTYPE:LiHa.TecanDiTi/TOOLNAME:FCA, 1000ul SBS",
        selected_tips: Optional[Union[range, list[int], tuple[int, ...]]] = None,
        columns: Union[str, dict[str, str]] = "standard",
        start_line: int = 2,
        separator: str = ",",
        execute: bool = True,
        well_positions: Optional[str] = None,
        **load_options: Any,
    ) -> None:
        """Load a CSV or GWL worklist and execute it by default.

        CSV sources emit FluentControl's Convert CSV to GWL command first.
        Existing GWL sources are loaded directly.
        """
        from .worklists import (
            infer_csv_well_positions,
            infer_gwl_well_positions,
            normalize_columns,
        )

        source = Path(source_path)
        suffix = source.suffix.lower()
        if suffix not in {".csv", ".gwl"}:
            raise ValueError(f"Worklist source must be .csv or .gwl, got {source_path!r}")

        selected = list(selected_tips) if selected_tips is not None else list(range(8))
        if suffix == ".csv":
            target_gwl = Path(gwl_path) if gwl_path is not None else source.with_suffix(".gwl")
            if well_positions is None:
                if not source.exists():
                    raise ValueError("well_positions is required when CSV source cannot be inspected")
                well_positions = infer_csv_well_positions(
                    source,
                    columns=columns,
                    start_line=start_line,
                    separator=separator,
                )
            self.convert_csv_to_gwl(
                source,
                target_gwl,
                columns=columns,
                start_line=start_line,
                separator=separator,
            )
            load_path = target_gwl
        else:
            if gwl_path is not None:
                raise ValueError("gwl_path is only valid when source_path is a CSV file")
            if well_positions is None:
                if not source.exists():
                    raise ValueError("well_positions is required when GWL source cannot be inspected")
                well_positions = infer_gwl_well_positions(source)
            load_path = source

        self.load_worklist(
            load_path,
            liquid_class=liquid_class,
            diti_type=diti_type,
            selected_tips=selected,
            well_positions=well_positions,
            **load_options,
        )
        if execute:
            self.execute_worklist()

    def convert_csv_to_gwl(
        self,
        csv_path: Union[str, Path],
        gwl_path: Union[str, Path],
        *,
        columns: Union[str, dict[str, str]] = "standard",
        start_line: int = 1,
        stop_with_last_line: bool = True,
        stop_with_line: int = 1,
        separator: str = ",",
    ) -> None:
        from .worklists import normalize_columns

        mappings = [
            WorklistColumnMapping(column_name=name, column_index=index, gwl_index=gwl_index)
            for name, index, gwl_index in normalize_columns(columns)
        ]
        csv_text = str(csv_path)
        gwl_text = str(gwl_path)
        self._add_file_reference(csv_text)
        self._add_file_reference(gwl_text)
        self._emit(WorklistImportStep(
            csv_path=csv_text,
            gwl_path=gwl_text,
            start_line=start_line,
            stop_with_last_line=stop_with_last_line,
            stop_with_line=stop_with_line,
            separator=separator,
            columns=mappings,
        ))

    def load_worklist(
        self,
        gwl_path: Union[str, Path],
        *,
        liquid_class: Optional[str] = None,
        diti_type: str = "TOOLTYPE:LiHa.TecanDiTi/TOOLNAME:FCA, 1000ul SBS",
        selected_tips: Optional[Union[range, list[int], tuple[int, ...]]] = None,
        handle_missing_labware: str = "SkipWithoutWarning",
        skip_initial_wash: bool = False,
        waste_labware: str = "FCA Thru Deck Waste Chute_1",
        empty_tips_liquid_class: str = "Empty Tip",
        use_legacy_gwl_file_format: bool = False,
        ignore_filename_until_run: bool = True,
        device_alias: Optional[str] = None,
        well_positions: str = "numeric",
        dynamic_diti_table: str = "",
        dynamic_diti_handling: bool = False,
        airgap_speed: int = 70,
        airgap_volume: int = 10,
    ) -> None:
        gwl_text = str(gwl_path)
        self._add_file_reference(gwl_text)
        # The worklist LiquidClassName is rendered literally (no variable
        # reference like head.aspirate). Resolve a declared-variable name to its
        # value so the LIQUID_CLASS_* idiom doesn't leak the placeholder into FC.
        if liquid_class is not None and liquid_class in self.protocol_variables:
            liquid_class = str(self.protocol_variables[liquid_class])
        self._emit(LoadWorklistStep(
            gwl_path=gwl_text,
            liquid_class=liquid_class,
            diti_type=diti_type,
            selected_tips=list(selected_tips) if selected_tips is not None else list(range(8)),
            handle_missing_labware=handle_missing_labware,
            skip_initial_wash=skip_initial_wash,
            waste_labware=waste_labware,
            empty_tips_liquid_class=empty_tips_liquid_class,
            use_legacy_gwl_file_format=use_legacy_gwl_file_format,
            ignore_filename_until_run=ignore_filename_until_run,
            device_alias=device_alias,
            well_positions=well_positions,
            dynamic_diti_table=dynamic_diti_table,
            dynamic_diti_handling=dynamic_diti_handling,
            airgap_speed=airgap_speed,
            airgap_volume=airgap_volume,
        ))

    def execute_worklist(self, *, delete_gwl_scripts: bool = False) -> None:
        self._emit(ExecuteWorklistStep(delete_gwl_scripts=delete_gwl_scripts))

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
        loop_variable: str | None = None,
    ) -> Iterator[LoopStep]:
        """Emit a `LoopStep` whose body is everything authored inside the
        `with` block.

        ``times`` is either a literal `int` (loop runs that many times) or a
        `str` naming a runtime variable. The variable's value at simulation
        time is resolved through `set_sim_value(name, value)`. ``loop_variable``
        optionally names FluentControl's in-scope loop counter variable.
        """
        counter = loop_variable if loop_variable is not None else (
            times if isinstance(times, str) else None
        )
        loop_step = LoopStep(
            name=name,
            iterations=times if isinstance(times, int) else 1,
            loop_variable=counter,
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
        # Trough placement guard rail — data-driven from this deck's rules
        # (config `deck_rules`, keyed by workspace name; empirical per deck):
        #   - Troughs only reach on the configured `trough_locations`.
        #   - The configured tall catalog is too tall for standard tips (Z-Max
        #     unreachable) unless the trough is earmarked for ethanol/wash.
        rules = self._deck_rules()
        trough_locations = rules.get("trough_locations") or ()
        if trough_locations and getattr(labware, "category", None) == "trough":
            from .simulator.invariants import TroughPlacementError
            if not any(location.startswith(prefix) for prefix in trough_locations):
                raise TroughPlacementError(
                    f"Trough {labware.label!r} must be placed on a reachable "
                    f"trough site ({', '.join(trough_locations)}…) on "
                    f"{self.workspace_name}. Got {slot!r}."
                )
            tall_catalog = str(rules.get("tall_trough_catalog") or "").strip().lower()
            catalog = (labware.catalog_name or "").strip().lower()
            label_l = (labware.label or "").lower()
            if tall_catalog and catalog == tall_catalog:
                wash_marker = any(
                    m in label_l for m in (rules.get("wash_markers") or ())
                )
                if not wash_marker:
                    raise TroughPlacementError(
                        f"Trough {labware.label!r} uses the `{tall_catalog}` catalog "
                        f"but isn't marked for ethanol/wash use. Standard tips "
                        f"cannot reach its Z-Max. Use `catalog='25ml_short'` "
                        f"instead, or name the trough with an `Ethanol`/`Wash` "
                        f"marker if the run actually needs 96 × ≥200 µL wash "
                        f"capacity."
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
            file_references=list(self.file_references),
        )
        protocol.assign_line_numbers()
        return protocol

    def compile(self, out_path: Union[str, Path]) -> Path:
        """Render the protocol to a `.xscr` file at `out_path`."""
        from .compiler import render_protocol
        from .catalog import rewrite_checksum_in_place

        self._require_bound_workspace()
        self._validate_liha_tipbox_presence()
        self._validate_liha_tip_pickup()
        self._validate_mix_liquid_classes()
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

    def _deck_rules(self) -> dict:
        """Deck-physics guard rules for the bound workspace (may be empty).

        Resolved from, in order: an active workspace-app profile
        (``FLUENTVIBE_PROFILE_DIR``) whose workspace matches, then the shipped
        ``generation.yaml`` ``deck_rules`` map keyed by workspace name. A
        workspace with no rules is left permissive. Cached per workspace name
        so the per-``place()`` lookup stays cheap.
        """
        name = self.workspace_name or ""
        cached = getattr(self, "_deck_rules_cache", None)
        if cached is not None and cached[0] == name:
            return cached[1]
        rules: dict = {}
        if name:
            try:
                from .authoring.profile import profile_from_env
                profile = profile_from_env()
                if profile is not None and profile.workspace_name == name and profile.deck_rules:
                    rules = dict(profile.deck_rules)
            except Exception:
                rules = {}
            if not rules:
                try:
                    from .authoring.grounding import load_generation_config
                    cfg = (load_generation_config() or {}).get("deck_rules") or {}
                    entry = cfg.get(name)
                    if isinstance(entry, dict):
                        rules = dict(entry)
                except Exception:
                    rules = {}
        self._deck_rules_cache = (name, rules)
        return rules

    def _iter_all_steps(self) -> Iterator[Step]:
        """Yield every authored step, recursing into loop/conditional/group bodies."""
        def walk(steps: list[Step]) -> Iterator[Step]:
            for step in steps:
                yield step
                inner = getattr(step, "steps", None)
                if inner:
                    yield from walk(inner)
                then_branch = getattr(step, "then_steps", None)
                if then_branch:
                    yield from walk(then_branch)
                else_branch = getattr(step, "else_steps", None)
                if else_branch:
                    yield from walk(else_branch)
        for group in self._groups:
            yield from walk(group.steps)

    def _validate_liha_tipbox_presence(self) -> None:
        """Refuse to compile a LiHa/worklist protocol without an FCA tip box.

        FluentControl raises `No DiTi-Labware … found` + `Tip(s) are not
        mounted` at runtime when the LiHa is asked to pipette but the deck
        carries no compatible FCA tip box. The LM-authored protocols regress
        on this rule despite the skill spelling it out, so the DSL refuses
        the compile up front to feed the repair loop a concrete traceback.

        Gated on this deck's `require_fca_tipbox` rule (config `deck_rules`),
        so the empty/generic workspaces used in IR-fidelity tests — which carry
        no deck rules — don't inherit the tip-box assumption.
        """
        if not self._deck_rules().get("require_fca_tipbox"):
            return
        liha_step_prefixes = ("Liha", "Worklist", "LoadWorklist", "ExecuteWorklist")
        uses_liha = any(
            type(step).__name__.startswith(liha_step_prefixes)
            for step in self._iter_all_steps()
        )
        if not uses_liha:
            return
        from .labware.tipboxes import TipBox
        has_fca_box = any(
            isinstance(lw, TipBox) and "fca" in (lw.catalog_name or "").lower()
            for stack in self.slot_map.values()
            for lw in stack
        )
        if has_fca_box:
            return
        from .simulator.invariants import MissingFCATipBoxError
        raise MissingFCATipBoxError(
            "Protocol uses the LiHa head (or a worklist Load) but no FCA "
            "tip box is placed on the worktable. FluentControl will reject "
            "this at load time with `No DiTi-Labware … found`. "
            "Add: `wt.place(TipBox(\"FCA_Tips\", catalog=\"FCA, 1000ul SBS\"), "
            "\"Nest61mm_Pos\", 6)`."
        )

    def _validate_liha_tip_pickup(self) -> None:
        """Refuse to compile when the LiHa head picks up a non-FCA tip box.

        `_validate_liha_tipbox_presence` only checks that *an* FCA box is on
        the deck — an LM regression places one but still calls
        `wt.liha.get_tips(mca_box)`, which FluentControl rejects with
        `No DiTi-Labware MCA96 … found` + `Tip(s) are not mounted`. Resolve
        each LiHa tip-pickup to its placed labware and require an FCA catalog.

        Gated on this deck's `require_fca_tipbox` rule like the presence check.
        """
        if not self._deck_rules().get("require_fca_tipbox"):
            return
        placed = {
            lw.label: lw for stack in self.slot_map.values() for lw in stack
        }
        for step in self._iter_all_steps():
            if type(step).__name__ != "LihaGetTipsStep":
                continue
            name = getattr(step, "labware_name", None)
            if not name:
                continue
            lw = placed.get(name)
            if lw is None:
                continue  # missing labware is the presence check's job
            catalog = (getattr(lw, "catalog_name", "") or "")
            if "fca" not in catalog.lower():
                from .simulator.invariants import LihaTipMismatchError
                raise LihaTipMismatchError(
                    f"The LiHa head picks up tip box {name!r} (catalog "
                    f"{catalog!r}), which is not an FCA DiTi box. The LiHa/FCA "
                    f"arm can only mount FCA tips; an `MCA96 …` box belongs to "
                    f"the MCA head. Place and pick up an FCA box, e.g. "
                    f"`fca = wt.place(TipBox(\"FCA_Tips\", catalog=\"FCA, 200ul "
                    f"SBS\"), \"Nest61mm_Pos\", 6)` then `wt.liha.get_tips(fca)`."
                )

    def _validate_mix_liquid_classes(self) -> None:
        """Refuse to compile a Mix step whose liquid class has no Mix section.

        FluentControl rejects `head.mix(..., liquid_class="Water Free Single")`
        with `Liquid subclass section "Mix" is missing`. The class name is
        often a declared variable, so resolve against `protocol_variables`
        first, then check the `.xlqc` for an actual `Mix` micro-script section
        (data-driven — matches FC's own model). Unknown classes / no catalog
        index are left alone so this never blocks on missing data.

        Gated on this deck's `check_mix_section` rule (config `deck_rules`).
        """
        if not self._deck_rules().get("check_mix_section"):
            return
        try:
            from .catalog import index_exists, liquid_class_supports_section
            if not index_exists():
                return
        except Exception:
            return
        defaults = self.protocol_variables
        for step in self._iter_all_steps():
            if type(step).__name__ not in ("Mca384MixStep", "LihaMixStep"):
                continue
            lc = getattr(step, "liquid_class", None)
            if not lc:
                continue
            resolved = defaults.get(lc, lc)
            if not isinstance(resolved, str):
                continue
            if liquid_class_supports_section(resolved, "Mix") is False:
                from .simulator.invariants import LiquidClassSectionError
                raise LiquidClassSectionError(
                    f"Mix step uses liquid class {resolved!r}, which has no "
                    f"\"Mix\" micro-script section — FluentControl rejects it "
                    f"with `Liquid subclass section \"Mix\" is missing`. Use a "
                    f"Mix-capable class such as \"Water Mix\" for mixing steps "
                    f"(keep \"Water Free Single\" for plain transfers)."
                )

    def _emit(self, step: Step) -> None:
        if self._emit_target_stack:
            self._emit_target_stack[-1].append(step)
            return
        if self._active_group is None:
            self._active_group = Group(name="Steps", steps=[])
            self._groups.append(self._active_group)
        self._active_group.steps.append(step)

    def _add_file_reference(self, path: str) -> None:
        if path and path not in self.file_references:
            self.file_references.append(path)

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
