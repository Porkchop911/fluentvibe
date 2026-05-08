# Compile path

The compile path turns a Python-authored protocol into a `.xscr` XML file
that FluentControl can load. It's intentionally narrow:

```
[ Worktable.protocol_ir (list of Step objects) ]
       │  wt.to_protocol()
       ▼
[ Protocol IR — Pydantic model ]
       │  render_protocol(protocol)
       ▼
[ XML string ]
       │  rewrite_checksum_in_place(path)
       ▼
[ .xscr file ready for FluentControl ]
```

## IR — `tecanlab/ir/schema.py`

The IR is a tree of Pydantic models that descends from the earlier
project-owned fluentdsl implementation. The outer shape:

```python
class Protocol(BaseModel):
    name: str
    comment: str
    variables: list[str]
    variable_defaults: dict[str, float|int|str]
    groups: list[Group]
    worktable_guid: str | None
    worktable_name: str | None
    liquid_class: str | None
    device_alias: str | None

    def total_steps() -> int                    # walk loops/conditionals
    def assign_line_numbers() -> None           # depth-first numbering

class Group(BaseModel):
    name: str                                   # 'Setup' / 'Pipetting' / …
    steps: list[Step]
    line_number: int | None

# Step is a discriminated union (Pydantic 2):
Step = AddLabwareStep | RemoveLabwareStep
     | GetHeadAdapterStep | DropHeadAdapterStep
     | PickUpTipsStep | SetTipsBackStep
     | AspirateStep | DispenseStep
     | RgaTransferLabwareStep | CgaGetFingersStep | CgaDropFingersStep
     | Mca384MixStep | Mca384EmptyTipsStep
     | LihaAspirateStep | LihaDispenseStep | LihaMixStep
     | LihaGetTipsStep | LihaDropTipsStep | LihaEmptyTipsStep
     | WaitStep | SetVariableStep | CalculateVariableStep
     | CommentStep | UserPromptStep | StartTimerStep | WaitForTimerStep
     | ExportVariableStep | ImportVariableStep | QueryVariableStep
     | ExecuteApplicationStep | DelayStep | SetLocationStep | SubRoutineStep
     | LoopStep | ConditionalStep | GenericStep
```

The `StepType` enum on each step (`AddLabwareStep.step_type` etc.) drives
both the Pydantic discriminator and the renderer's command dispatch.

## `Worktable.to_protocol()`

`tecanlab/worktable.py:130`

```python
def to_protocol(self) -> Protocol:
    protocol = Protocol(
        name=self.name,
        comment=self.comment,
        variables=list(self.protocol_variables.keys()),
        variable_defaults=dict(self.protocol_variables),
        groups=[Group(name=g.name, steps=list(g.steps)) for g in self._groups],
    )
    protocol.assign_line_numbers()
    return protocol
```

Notes:

- The IR is a **fresh snapshot**. Mutating the worktable after `to_protocol()`
  doesn't affect the returned `Protocol`.
- Group steps are shallow-copied. Step mutation is rare; in practice they
  are append-only Pydantic instances.
- `assign_line_numbers()` walks groups + steps depth-first and assigns
  `line_number` 1, 2, 3… The renderer uses these for `<LineNumber>`
  elements.

## Renderer — `tecanlab/compiler/renderer.py`

The package-local renderer descends from the earlier project-owned fluentdsl
implementation. Asset paths point inside the package
(`renderer.py:134-138`, `renderer.py:177`).

### Entry point

```python
from tecanlab.compiler import render_protocol
xml = render_protocol(protocol)               # str
```

Or the lower-level class API:

```python
from tecanlab.compiler import Renderer
r = Renderer()                                # picks up _assets/{config,reference,templates}
xml = r.render(protocol)
```

`Renderer.__init__` accepts `config_path`, `reference_path`, `templates_path`
overrides if you need to point at custom assets.

### What the renderer reads at startup

`Renderer.__init__` (`renderer.py:120-146`):

| Asset | Purpose |
|---|---|
| `_assets/config/generation.yaml` | Generation config: device aliases, liquid-class default, worktable GUID/name, EVA adapter config. |
| `_assets/reference/commands.yaml` | Per-step XML command templates and parameter mappings. Indexed by command ID (`STEP_TO_COMMAND_ID` in `ir/schema.py:563`). |
| `_assets/reference/labware.yaml` | Per-labware metadata used at render time (well counts, category, functional group). |
| `_assets/templates/script_wrapper.xml` etc. | XML wrapping templates (script wrapper, group wrapper, loop, conditional, alternate). |

These are loaded once per `Renderer` instance.

### Render pipeline

`Renderer.render(protocol)` (`renderer.py:201`):

1. **Reset state** for this render (clear adapter config, labware-types
   map, placement map).
2. **Magnet cover normalization** — `_normalize_for_magnet_cover_site` —
   adjusts gripper transfer destinations onto magnet cover sites if needed.
   Protocol-agnostic, no-op when the protocol doesn't include such a step.
3. **Labware name normalization** — `_normalize_labware_names` — fixes
   labware-type names that are close-but-not-exact matches against the
   labware reference.
4. **Variable scan** — collect `set_variable` step values into a map for
   resolving variable-named labware types (e.g. when `add()` was called
   with a variable reference).
5. **Assign line numbers** — `protocol.assign_line_numbers()`.
6. **Build the XML** — wrap the protocol in `script_wrapper.xml`; render
   each group via `script_group.xml`; render each step via the appropriate
   command template from `commands.yaml`.
7. **Special groups** — loops use `loop_group.xml`, conditionals use
   `conditional_group.xml` (with optional `alternate_group.xml` for the
   `else` branch).

The final XML is a single string. It includes a placeholder `<Checksum>`
element which the install-bundle bridge fills in (next step).

## Checksum rewrite

`tecanlab/catalog/fc_install.py:46`

```python
def rewrite_checksum_in_place(path) -> bool:
    core = shared_core()
    if core is None:
        return False
    payload = core.rewrite_checksum(path, in_place=True)
    return bool(payload.get("is_valid"))
```

`Worktable.compile()` calls this after writing the XML. It bridges to the
upstream `fluentcontrol_core` library which knows how FluentControl computes
its file checksum and rewrites the `<Checksum>` element in place. If
`fluentcontrol_core` isn't importable, the call is a silent no-op — the
file is still valid XML, just without a final checksum value (FluentControl
will recompute on first load).

## `Worktable.compile()`

`tecanlab/worktable.py:139`

```python
def compile(self, out_path: str | Path) -> Path:
    from .compiler import render_protocol
    from .catalog import rewrite_checksum_in_place

    protocol = self.to_protocol()
    xml = render_protocol(protocol)
    path = Path(out_path)
    path.write_text(xml, encoding="utf-8")
    rewrite_checksum_in_place(path)
    return path
```

End-to-end, given a built worktable:

```python
out = wt.compile("simple_transfer.xscr")
print(f"Wrote {out}")
```

The output is a `.xscr` file FluentControl can open directly.

## Parity guarantees

The rendering pipeline is deterministic with one exception: the renderer
generates a fresh `uuid.uuid4()` for the `WorkspaceDelta`'s `<Identifier>`
on every call (`renderer.py:378`). Two runs of the same input differ on
that one GUID.

The parity test (`tests/test_simple_transfer_parity.py:57`) normalizes
that GUID before byte-comparing two outputs. The test confirms that
identical IR renders to identical XML modulo the random GUID, between
tecanlab and the earlier fluentdsl implementation running on the same
protocol.

## Generated XML — annotated outline

```xml
<?xml version="1.0" encoding="utf-8"?>
<sd:VxData xmlns:sd="..." dataStoreVersion="4">
  <Payload>
    <ObjectName>Simple transfer</ObjectName>
    <Comment>...</Comment>
    <Reference><TypeId>WorktableWorkspace</TypeId>            ← GUID + name from config
              <Guid>...</Guid><ObjectName>...</ObjectName></Reference>
    <Reference><TypeId>LiquidClass</TypeId>...</Reference>    ← default liquid class
    <PayloadData>
      <Script>
        <Properties>
          <VxWorkspaceData>
            <BaseWorkspaceName>...</BaseWorkspaceName>
            <CameraView>...</CameraView>                       ← static
            <WorkspaceDeltas>                                  ← per-render random GUID
              <d2p1:string>&lt;VxWorkspaceDelta...
                &lt;Identifier&gt;<RANDOM>&lt;/Identifier&gt;
              </d2p1:string>
            </WorkspaceDeltas>
          </VxWorkspaceData>
          <VariableDeclarations>...</VariableDeclarations>     ← from protocol_variables
        </Properties>
        <ScriptModule>
          <Statements>                                          ← protocol groups + steps
            <Object Type="...">
              <AddLabwareDataV1>
                <LabwareType>96 Well Flat</LabwareType>
                <LabwareLable>SourcePlate</LabwareLable>
                <Location>Nest</Location>
                <Position>1</Position>
                ...
              </AddLabwareDataV1>
            </Object>
            ...
          </Statements>
        </ScriptModule>
      </Script>
    </PayloadData>
  </Payload>
  <Checksum>...</Checksum>                                      ← rewritten by fc bridge
</sd:VxData>
```
