"""Deterministic Python code generation for authoring specs."""

from __future__ import annotations

from .models import (
    ConditionalStep,
    HeadAction,
    LabwareBinding,
    LiquidAction,
    LoopStep,
    MoveLabwareAction,
    ProtocolSpec,
    TransferAction,
)


class ProtocolCodeGenerator:
    def render(self, spec: ProtocolSpec) -> str:
        needs_layer = any(
            len(seed.layers) > 1 or any(layer.pinned_when_magnetized for layer in seed.layers)
            for seed in spec.seeded_labware
        )
        imports = [
            "Worktable",
            "Reagent",
            "Plate96",
            "Plate384",
            "Trough25mL",
            "Waste",
            "MCA100Box",
            "MCA200Box",
            "MCA500Box",
            "FCA200Box",
            "FCA1000Box",
            "MagnetRack",
        ]
        if needs_layer:
            imports.append("Layer")
        used_classes = {labware.python_class for labware in spec.labware}
        ordered_imports = [name for name in imports if name in used_classes or name in {"Worktable", "Reagent", "Layer"}]
        ordered_imports = [name for name in ordered_imports if name != "Layer" or needs_layer]

        out: list[str] = []
        out.append('"""Generated fluentvibe protocol from a free-text authoring prompt."""')
        out.append("")
        out.append(f"from fluentvibe import {', '.join(ordered_imports)}")
        out.append("")
        out.append("")
        out.append("def build_worktable() -> Worktable:")
        out.append("    wt = Worktable.from_workspace(")
        out.append(f"        {spec.workspace.name!r},")
        out.append(f"        workspace_guid={spec.workspace.guid!r},")
        out.append("        auto_place=False,")
        out.append(f"        protocol_name={spec.protocol_name!r},")
        out.append(f"        comment={spec.intent!r},")
        out.append("    )")
        out.append("")

        for variable in spec.variables:
            out.append(f"    wt.declare_variable({variable.name!r}, {variable.default!r})")
            out.append(f"    wt.set_sim_value({variable.name!r}, {variable.sim_value!r})")
        if not spec.variables:
            out.append("    wt.declare_variable('RunId', 'authored_run')")
            out.append("    wt.set_sim_value('RunId', 'authored_run')")
        out.append("")

        reagent_names = sorted(
            {
                layer.reagent_name
                for seeded in spec.seeded_labware
                for layer in seeded.layers
            }
        )
        reagent_vars = {name: self._python_name(name) for name in reagent_names}
        for reagent_name in reagent_names:
            var_name = reagent_vars[reagent_name]
            if any(layer.reagent_name == reagent_name and layer.pinned_when_magnetized for seeded in spec.seeded_labware for layer in seeded.layers):
                out.append(f'    {var_name} = Reagent({reagent_name!r}, role="bead_carrier")')
            else:
                out.append(f"    {var_name} = Reagent({reagent_name!r})")
        if reagent_names:
            out.append("")

        var_names: dict[str, str] = {}
        for group in spec.groups:
            if group.name not in {"Setup", "Labware Placement"}:
                continue
            out.append("    wt.group('Labware Placement')")
            for labware in spec.labware:
                var_name = self._python_name(labware.label)
                var_names[labware.label] = var_name
                out.append(self._render_place(labware, var_name))
            if spec.labware:
                out.append("")
            for seeded in spec.seeded_labware:
                if seeded.mode == "fill_all":
                    layer = seeded.layers[0]
                    out.append(
                        f"    {var_names[seeded.labware_label]}.fill_all({reagent_vars[layer.reagent_name]}, {layer.volume_ul!r})"
                    )
                elif seeded.mode == "fill_all_layers":
                    first = seeded.layers[0]
                    out.append(
                        f"    {var_names[seeded.labware_label]}.fill_all({reagent_vars[first.reagent_name]}, {first.volume_ul!r})"
                    )
                    out.append(
                        f"    for _well in {var_names[seeded.labware_label]}.wells.values():"
                    )
                    for layer in seeded.layers[1:]:
                        out.append(
                            f"        _well.layers.append(Layer(reagent={reagent_vars[layer.reagent_name]}, volume_ul={layer.volume_ul!r}))"
                        )
            if spec.seeded_labware:
                out.append("")

        for group in spec.groups:
            if group.name in {"Setup", "Labware Placement", "Variables"}:
                continue
            out.append(f"    wt.group({group.name!r})")
            out.append("    head = wt.mca96")
            for step in group.steps:
                out.extend(self._render_step(step, var_names, indent="    "))
            out.append("")

        out.append("    return wt")
        out.append("")
        return "\n".join(out)

    def _render_place(self, labware: LabwareBinding, var_name: str) -> str:
        return (
            f"    {var_name} = wt.place("
            f"{labware.python_class}({labware.label!r}, catalog={labware.catalog_name!r}), "
            f"{labware.location!r}, {labware.position!r})"
        )

    def _render_step(self, step, var_names: dict[str, str], *, indent: str) -> list[str]:
        if isinstance(step, HeadAction):
            if step.action == "mount_adapter":
                return [f"{indent}head.mount_adapter()"]
            if step.action == "drop_adapter":
                return [f"{indent}head.drop_adapter()"]
            if step.action == "pick_up":
                return [f"{indent}head.pick_up({var_names[step.labware_label or '']})"]
            if step.action == "return_tips":
                return [f"{indent}head.return_tips({var_names[step.labware_label or '']})"]
        if isinstance(step, TransferAction):
            src = var_names[step.source_label]
            dst = var_names[step.destination_label]
            return [
                f"{indent}head.aspirate({src}, {step.volume_ul!r}, liquid_class={step.liquid_class!r})",
                f"{indent}head.dispense({dst}, {step.volume_ul!r}, liquid_class={step.liquid_class!r})",
            ]
        if isinstance(step, LiquidAction):
            target = var_names[step.labware_label]
            if step.action == "mix":
                return [
                    f"{indent}head.mix({target}, {step.volume_ul!r}, cycles={step.cycles!r}, liquid_class={step.liquid_class!r})"
                ]
            if step.action == "empty_tips":
                return [f"{indent}head.empty_tips({target}, {step.volume_ul!r}, liquid_class={step.liquid_class!r})"]
        if isinstance(step, MoveLabwareAction):
            label = var_names[step.labware_label]
            if step.onto_label:
                target = var_names[step.onto_label]
                return [f"{indent}wt.gripper.move({label}, onto={target})"]
            return [f"{indent}wt.gripper.move({label}, to=({step.location!r}, {step.position!r}))"]
        if isinstance(step, LoopStep):
            lines = [f"{indent}with wt.loop(times={step.iterations!r}, name={step.name!r}):"]
            for child in step.steps:
                lines.extend(self._render_step(child, var_names, indent=indent + "    "))
            return lines
        if isinstance(step, ConditionalStep):
            lines = [
                f"{indent}with wt.conditional(left={step.left_variable!r}, op={step.operator!r}, right={step.right_value!r}, name={step.name!r}):"
            ]
            for child in step.steps:
                lines.extend(self._render_step(child, var_names, indent=indent + "    "))
            return lines
        raise ValueError(f"Unsupported spec step: {type(step).__name__}")

    def _python_name(self, value: str) -> str:
        out = []
        for ch in value:
            out.append(ch.lower() if ch.isalnum() else "_")
        candidate = "".join(out).strip("_")
        while "__" in candidate:
            candidate = candidate.replace("__", "_")
        if not candidate:
            return "item"
        if candidate[0].isdigit():
            return f"item_{candidate}"
        return candidate
