# Making fluentvibe Accessible to Users

## Executive Recommendation

The first easy-access path for fluentvibe should be an installer-style local
application that launches the existing workspace web app behind the scenes.
The user should not need to clone the repository, create a virtual
environment, or run `pip install -e .` just to try the project.

The current app command remains the implementation backbone:

```powershell
fluentvibe workspace-app
```

But the desired user experience should be closer to:

1. Download an installer or zip bundle.
2. Install or unpack fluentvibe.
3. Launch "fluentvibe" from the Start menu, desktop shortcut, or bundled
   launcher.
4. The launcher starts the local server on `127.0.0.1` and opens the browser.

This should be framed as a local workstation workflow. The initial installer
does not need to certify fluentvibe for unattended production instrument use,
but it should be suitable for users who expect application-style access rather
than developer-style setup.

The recommended near-term posture is:

- Primary interface: installer-launched local app, backed by the existing web
  UI at `http://127.0.0.1:8765`.
- Installation style: Windows installer or portable zip bundle.
- Safety stance: offline-first exploration.
- Deployment stance: advanced, guarded, and clearly separated from normal
  app startup.
- Secondary interfaces: CLI commands for power users and the VS Code extension
  for authoring.
- Deferred interface: hosted web app.

Developer fallback:

```powershell
git clone <repo-url>
cd fluentvibe
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[lsp]"
fluentvibe workspace-app
```

Installer-style target:

```text
Install fluentvibe -> Launch fluentvibe -> browser opens http://127.0.0.1:8765
```

CLI fallback:

```powershell
fluentvibe simulate examples/simple_transfer.py
fluentvibe compile examples/simple_transfer.py -o build\simple_transfer.xscr
fluentvibe decompile path\to\script.xscr -o build\decompiled.py
```

This plan intentionally avoids a hosted service in the first accessibility
pass. Hosted access would reduce friction, but it is a poor fit for local
FluentControl installs, lab data, and instrument-adjacent workflows.

This is a deliberate pivot from a source-checkout reviewer workflow. The
earlier safety argument against desktop-style packaging still matters: an
installed app can imply readiness that fluentvibe has not earned for live
instrument operation. The reason to prefer installer-style access anyway is
that "easy to access" should mean application-style launch for the intended
user, not developer setup. The mitigation is to start with a portable local
bundle, keep the app loopback-only by default, make deploy actions advanced and
guarded, and preserve the editable install path only for developers.

## Current Project Shape

fluentvibe is currently a Python package configured by `pyproject.toml`. It
registers a console command named `fluentvibe` and exposes several workflows:

- `compile`: render a Python-authored protocol to `.xscr`.
- `simulate`: run the protocol simulator and print a summary.
- `check`: analyze a protocol for build and simulator diagnostics.
- `decompile`: recover fluentvibe-style Python from supported `.xscr` files.
- `catalog`: refresh, inspect, and search the FluentControl-backed catalog.
- `author` / `chat`: run model-assisted protocol authoring.
- `workspace-app`: start the local browser UI.
- `deploy`: copy a compiled `.xscr` into the FluentControl UserSpecific
  datastore with guardrails.

The web app in `fluentvibe/workspace_app/` is the strongest existing candidate
for a user-facing entry point. It is a local single-page app served by a Python
HTTP server. It groups setup, authoring, Code Lab, decompile, catalog, and
FluentControl deployment workflows behind one browser tab. An installer should
wrap this existing app rather than replace it.

There is also a VS Code extension under `editors/vscode/`. It is valuable for
authoring because it surfaces diagnostics, completion, signature help, hover,
quick fixes, and optional model-backed inline edits. It should be treated as an
optional enhancement rather than the first access path because it adds Node,
VS Code, and Python interpreter configuration on top of the base install.

The project documentation already makes important safety claims:

- The project is source-visible for technical review.
- It is not a production release.
- Some features require a licensed local FluentControl installation.
- Generated `.xscr` files must be reviewed and validated before instrument use.
- The project is not affiliated with, endorsed by, or licensed by Tecan.

Those constraints should remain visible in any accessibility work. Ease of
access should not blur the line between review, simulation, generated file
inspection, and live instrument use.

## Target User and Success Criteria

The first easy-access path should target users who expect application-style
access:

- They may not be comfortable with Python packaging.
- They should not need to clone the repository.
- They should be able to launch fluentvibe from the desktop or Start menu.
- They may or may not have a local FluentControl installation.
- They are evaluating design, domain fit, simulator behavior, generated XML,
  and workflow usefulness.
- They may eventually want to deploy generated files, but deployment should not
  be part of the first-run path.
- They need a safe, deterministic path that does not depend on live hardware.

Success means a user can:

- Install or unpack fluentvibe without using `pip`.
- Launch the app with a shortcut or bundled executable.
- Open the local app in a browser.
- Exercise at least one offline-safe example.
- Run `simulate` and `compile` against `examples/simple_transfer.py`.
- Understand which features need FluentControl.
- Understand which features need a model endpoint.
- Avoid instrument deployment unless they deliberately choose the advanced path.
- Use CLI commands only if they choose to.

The accessibility goal is not to hide all complexity. The goal is to present
the existing complexity in a safe, navigable order.

## Feature Availability Matrix

| Feature | Works Without FluentControl | Requires FluentControl | Requires LM Endpoint | Recommended for First-Run Users |
|---|---:|---:|---:|---:|
| Workspace app shell | Yes | No | No | Yes |
| Code Lab simulate/compile for bundled examples | Yes | No | No | Yes |
| Decompile `.xscr` | Yes | No | No | Yes |
| Catalog refresh/search | No | Yes | No | Optional |
| Author/chat with model-assisted generation | Limited without workspace profile | Optional for grounded profiles | Yes | Optional |
| Deploy to FluentControl | No | Yes | No | Advanced only |
| VS Code diagnostics | Yes | Optional for catalog-backed completion | No | Optional |

The important distinction is that the workspace app can load without
FluentControl or an LM endpoint, but not every tab can perform useful work in
that environment. The first-run path should direct users to workflows that are
safe and likely to work before exposing advanced integrations.

## Approach Comparison

### Approach A: Installer-Launched Local Web App

Package fluentvibe as a local app that launches `fluentvibe workspace-app`,
starts a loopback server, and opens the browser.

Pros:

- Builds on the app that already exists.
- Avoids requiring users to know Python packaging.
- Gives users a browser UI with desktop-style launch.
- Keeps lab and FluentControl operations local to the user's machine.
- Can expose setup, authoring, Code Lab, decompile, catalog, and deploy from
  one place.
- Reuses the same Python entry points as the CLI.
- Fits future lab workstation deployment better than an editable install.

Cons:

- Requires packaging work.
- Must decide between installer and portable bundle.
- Must handle app startup, port conflicts, logs, and shutdown behavior.
- Some tabs depend on FluentControl or an LM endpoint.
- The single-file frontend can become harder to maintain as it grows.
- The app has no authentication if bound beyond localhost.
- A browser UI can over-signal product maturity.
- Users might click into deploy-oriented tabs before understanding the safety
  model.

Verdict:

This is the recommended first path. It gives the largest usability improvement
while still preserving the local-machine architecture. The package should make
startup easy, but the app must remain direct about safety and deployment
boundaries.

### Approach B: CLI Package First

Make command-line workflows the primary access path.

Pros:

- Simple to document.
- Easy to test.
- Low packaging complexity.
- Good fit for technical users and developers.
- Natural fit for CI and reproducible examples.
- Less likely to imply production readiness.

Cons:

- Less approachable for users evaluating the end-to-end workflow.
- Forces users to discover command order and relationships.
- Does not showcase the workspace app.
- Makes catalog/profile/deploy workflows feel fragmented.
- Gives less insight into future operator ergonomics.

Verdict:

The CLI should remain the backbone and fallback. It should not be the primary
access path for users who expect installer-style access.

### Approach C: VS Code Extension First

Make authoring inside VS Code the main user experience.

Pros:

- Strong fit for Python-based protocol authoring.
- Provides live diagnostics and repair hints.
- Supports completion, hover, signature help, and quick fixes.
- Makes fluentvibe feel integrated into a real authoring environment.
- Useful for users focused on developer experience.

Cons:

- Adds Node/npm and VS Code extension setup.
- Requires configuring the Python interpreter used by the extension.
- Focuses on authoring more than decompile, catalog, workspace setup, and
  deployment.
- More moving parts for a first user experience.
- Optional model-backed inline edit introduces another dependency.

Verdict:

Make this an optional follow-up path. It is valuable, but it is not the simplest
first access route.

### Approach D: Editable Source Checkout

Ask users to clone the repository and install it with `pip install -e .`.

Pros:

- Already works today.
- Best for contributors.
- Easy to debug.
- Keeps source visible.
- Avoids packaging complexity.

Cons:

- Requires Python knowledge.
- Requires command-line setup.
- Creates friction before the user sees the app.
- Feels like a developer workflow, not a user workflow.
- Makes support dependent on the user's local Python environment.

Verdict:

Keep as the developer fallback, but do not present it as the primary easy-access
path.

### Approach E: Hosted Web App

Deploy fluentvibe as a hosted web service.

Pros:

- Very low friction for demos.
- Easy to share with users.
- No local Python setup for synthetic examples.
- Could support guided tours and sample protocols.

Cons:

- Poor fit for local FluentControl installs and datastore operations.
- Raises protocol privacy and lab data concerns.
- Requires authentication, tenant isolation, storage policy, and audit logs.
- Cannot safely interact with a user's local FluentControl installation.
- Would need synthetic catalog/workspace data to be meaningful.
- Adds infrastructure work that does not validate the current local package.

Verdict:

Do not pursue for the current phase. A hosted synthetic demo could be useful
later, but it should not be confused with the real local workflow.

## Recommended Implementation Plan

### Phase 1: Define the Installer Contract

Define the installer-style behavior before choosing a packaging tool. The
contract should be independent of whether the first artifact is a `.msi`,
`.exe`, or portable zip.

The first installer-style artifact should:

- Install or unpack fluentvibe without requiring the user to run `pip`.
- Include a launcher named `fluentvibe` or `fluentvibe workspace`.
- Start the existing `workspace-app` server on `127.0.0.1`.
- Open the user's default browser to the selected local URL.
- Detect a port conflict and either choose another local port or report a
  clear error.
- Write logs to a predictable user-writable location.
- Keep deployment controls advanced and guarded.
- Preserve CLI access for power users.

This phase can be documented before implementation, but the target is a real
installable artifact, not just improved setup instructions.

### Phase 2: Build a Portable Bundle First

Start with a portable zip bundle rather than a full installer.

Recommended first artifact:

- A Windows zip containing Python runtime or a self-contained executable.
- A launcher script/exe that starts the workspace app.
- Packaged static assets and bundled fluentvibe package data.
- A README for users that says "unzip, run launcher".
- A developer README that explains how the bundle was built.

The portable bundle is the lowest-risk installer-style step because it avoids
registry writes, admin permissions, uninstall behavior, and code-signing
requirements while still removing Python setup from the user's first
experience.

### Phase 3: Add a Proper Windows Installer

After the portable bundle works, add an installer if users need Start menu
integration, file associations, or managed lab workstation deployment.

Installer behavior:

- Install to a normal per-user location by default.
- Add Start menu entry and optional desktop shortcut.
- Register an uninstaller.
- Avoid requiring admin rights unless a lab deployment explicitly needs them.
- Do not bind the app to non-localhost addresses.
- Do not silently enable FluentControl deployment.
- Include version metadata so support reports can identify the build.

The installer should be added only after the bundle proves the runtime shape.
This keeps packaging risk incremental.

### Phase 4: App-Level First-Run Mode

Add an explicit first-run mode to the workspace app.

Potential behavior:

- Default to localhost only.
- Show offline-safe tabs first.
- Mark FluentControl and LM-dependent tabs as requiring setup.
- Hide or gate deployment controls until the user explicitly opts in.
- Provide a visible status area for:
  - fluentvibe version.
  - Catalog index presence.
  - FluentControl install detection.
  - LM endpoint configuration.

This phase makes the installed app safer to explore because the UI itself
communicates what works locally, what requires extra setup, and what is
advanced.

### Phase 5: Documentation and Developer Fallback

Update the main docs after the installer-style path exists:

- Put the installer or portable bundle first in `README.md`.
- Keep editable install instructions under a developer heading.
- Keep `docs/deployment.md` as the advanced deployment reference.
- Document CLI commands as power-user and automation tools.
- Document FluentControl and LM endpoint requirements separately.

Developer fallback remains:

```powershell
python -m pip install -e ".[lsp]"
fluentvibe workspace-app
```

## Critical Self-Review

### Challenge: The Local Web App May Look Too Finished

A browser UI can make a project feel productized even when the README says it
is not production-ready. This is a real risk for fluentvibe because the domain
is safety-sensitive. An installed app raises that risk further: users may
reasonably infer that if it installs like an application, it is ready for
routine operational use.

Response:

The mitigation is not to avoid installer-style access. The mitigation is to
separate "easy to launch" from "validated for instrument operation." The
first-run UI should point to simulate, compile, inspect, and decompile before
deployment. Deployment should remain advanced, guarded, and documented
separately.

### Challenge: A Portable Bundle May Be Better Than an Installer

A full installer adds decisions about installation location, uninstall,
shortcuts, signing, updates, and admin rights. Those decisions can slow down
the first accessibility win.

Response:

Start with a portable bundle. It is installer-style from the user's
perspective because it removes Python setup and provides a launcher, but it is
less invasive than a full installer. Move to `.msi` or `.exe` installer only
after the bundle shape works.

### Challenge: Packaging Can Hide Useful Errors

Editable installs make errors visible in the user's shell. A packaged app can
fail silently if the launcher closes immediately, if a port is occupied, or if
package data is missing.

Response:

The launcher must keep failures visible. It should write logs, show a clear
error dialog or console message, and avoid swallowing server startup failures.
Packaging tests must verify that bundled assets, catalog files, and templates
are present.

### Challenge: Hosted Demo Would Remove All Setup Friction

A hosted demo could let a user click one link and explore fluentvibe
immediately. That is the lowest-friction path.

Response:

It would also be the least representative path. fluentvibe's meaningful
workflows are local: they touch protocol files, local catalog data, and
potentially FluentControl installations. A hosted demo would need synthetic
data and careful isolation. It may be useful later for marketing or education,
but it is not the right first access path for serious local workflow
evaluation.

### Challenge: The Plan May Underinvest in Users Without FluentControl

Many users may not have FluentControl. The workspace app's setup and catalog
features may look sparse or broken without it.

Response:

The documentation should explicitly set expectations. The offline-safe first-run
path should start with examples, simulation, compile, and decompile. A future
improvement would be synthetic workspace/catalog fixtures or a demo profile
that lets users explore more of the app without a real installation.

### Challenge: Model-Assisted Authoring Adds Too Much Variability

The authoring workflow depends on an LM endpoint and model behavior. This can
make first impressions inconsistent.

Response:

Model authoring should be optional in the first-run app. The default path
should rely on deterministic examples and local app actions. The LM path can be
documented as an advanced feature with its dependency clearly called out.

## AI Reviewer Checklist

Use this checklist when asking another AI or engineer to review the access
strategy:

- Does the plan match the actual repository structure?
- Does it correctly identify `fluentvibe workspace-app` as an existing command?
- Does it avoid implying production readiness?
- Does it separate offline review from instrument deployment?
- Does it identify FluentControl-dependent features?
- Does it identify LM-dependent features?
- Does it include deterministic commands?
- Does it avoid inventing infrastructure that does not exist?
- Does it explain why a portable bundle should come before a full installer?
- Does it explain why hosted deployment is deferred?
- Does it define acceptance criteria?
- Does it state what is deliberately out of scope?
- Does it keep safety warnings direct and visible?

## Testing and Acceptance Criteria

For plan-only implementation, validate that commands referenced in the plan
match existing CLI behavior.

Recommended check:

```powershell
python -m pytest tests/test_workspace_app.py tests/test_examples.py tests/test_simulator_cli.py -q
```

Manual acceptance criteria:

- This document exists at `docs/accessibility-plan.md`.
- It recommends an installer-launched local web app as the first access path.
- It includes a developer fallback.
- It includes a CLI fallback.
- It compares installer-launched local app, CLI, VS Code, editable checkout,
  and hosted approaches.
- It includes pros and cons for each approach.
- It contains a critical self-review section.
- It states that deployment is advanced and guarded.
- It warns against instrument use without normal lab validation.
- It identifies FluentControl and LM endpoint dependencies.
- It avoids claiming production readiness.

If later phases add a bundle or installer, acceptance should expand to include
a manual launch test using that future launcher artifact. `fluentvibe-launcher.exe`
is a placeholder name, not a file that exists in the repository today:

```powershell
fluentvibe-launcher.exe
```

Then open:

```text
http://127.0.0.1:8765
```

The app should load, and offline-safe workflows should be identifiable before
FluentControl or LM-dependent workflows.

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Users are blocked by Python setup | Provide a portable bundle or installer so Python setup is not the first-run path |
| Users lack FluentControl | Start with offline-safe examples and label FluentControl-backed features |
| Browser UI implies production maturity | Use direct safety language and keep deploy advanced-only |
| Hosted ideas distract from local workstation needs | Explicitly defer hosted deployment with rationale |
| Documentation becomes stale | Keep commands tied to existing CLI tests and update docs with CLI changes |
| Network binding exposes unauthenticated app | Recommend default localhost and warn about `--host 0.0.0.0` |
| LM authoring produces inconsistent first impressions | Treat model workflows as optional advanced features |
| Generated `.xscr` is mistaken for instrument-ready output | Repeat that generated files require normal FluentControl and lab validation |

## Future Paths

Future accessibility work can build on this plan once the installer-style path
is stable:

- Add synthetic demo workspace/catalog data for users without FluentControl.
- Add an app-level first-run mode that defaults to offline-safe workflows.
- Add visible dependency status checks in the workspace app.
- Publish a Python package after license and release posture are clear.
- Provide `pipx` or `uv tool` installation instructions.
- Build a signed installer for lab workstations.
- Add authentication before supporting non-localhost serving.
- Create a hosted synthetic demo that cannot touch real lab data or instruments.
- Add stricter deploy confirmations and audit logs if deployment becomes a
  supported operator workflow.

## Final Recommendation

The best near-term path is installer-launched local app first, CLI second,
VS Code optional, editable source checkout for developers, and hosted
distribution deferred.

This gives users a concrete, low-friction way to open fluentvibe without
requiring Python packaging knowledge. It uses the existing local web app,
avoids hosted infrastructure, and keeps the safety boundary clear: offline
exploration is encouraged; live instrument use remains advanced and guarded.
