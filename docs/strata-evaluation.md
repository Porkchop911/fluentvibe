# Strata evaluation

Run the local default model:

```powershell
python -m fluentvibe.cli strata-eval
```

Compare server-supported model IDs with repeated trials:

```powershell
python -m fluentvibe.cli strata-eval --model MODEL_ID_A --model MODEL_ID_B --repeats 3 --output build/strata_comparison.json
```

The server must be running and able to serve each requested ID. Use `--endpoint`
to select another OpenAI-compatible chat-completions endpoint. The default is
`http://127.0.0.1:8080/v1/chat/completions`, with model
`qwen3.8-flash-next-iq3_xxs` (the model advertised by the local server when
this evaluation was set up). Pass `--model` if the loaded model changes.

Three scenarios exercise catalog grounding, gripper API lookup, and rules plus
labware lookup through the real authoring graph. A scenario passes when the model
selects every required tool and every required tool has a successful dispatch.
Reports include required-tool recall, errors, full model/tool traces, cache metrics,
and model/tool latency. Temperature is zero; each request has a 120-second timeout
with no automatic retries. Each scenario gets a fresh registry. Results are saved
after each scenario so completed trials survive an interruption.

Exit code 0 means all scenarios passed; 1 means a failure or server error; 2 means
invalid arguments. This benchmark measures tool selection and dispatch, not the
correctness of generated protocols or the factual quality of the final answer.
The inherited lookup harness uses a pass-through validator, so its authoring
status must not be interpreted as protocol validation. Catalog results depend on
the local catalog; compare models against the same catalog and server conditions.
