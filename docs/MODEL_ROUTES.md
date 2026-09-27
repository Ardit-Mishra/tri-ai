# Model Routes

Tri-AI accepts a graph node's `model_route` only when an operator-owned route
registry resolves it to an exact Hermes model and provider. Graph input never
carries raw model or provider strings.

Copy `config/model-routes.example.json` outside the repository and set
`admitted` to `true` only after a real Hermes one-shot writes a usage record
whose `model` exactly matches the configured model. The registry contains no
endpoint, API key, or other credential; Hermes configuration remains the owner
of those values.

Example graph node:

```json
{
  "node_key": "implement",
  "title": "Implement the feature",
  "prompt": "Work in the assigned workspace.",
  "model_route": "omniroute-gpt-5-4-nano"
}
```

Run the planner with the registry explicitly:

```powershell
python src/planner.py --graph graph.json --board C:\\path\\to\\board.db --model-routes C:\\path\\to\\model-routes.json
```

An unknown or unadmitted route rejects the full graph before any task is
created. At runtime, a served model that differs from the requested pin is an
environment failure: Tri-AI restores the workspace and does not verify the
task's work.
