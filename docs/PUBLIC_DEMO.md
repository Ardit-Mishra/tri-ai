# Reproduce the Public Demo

This guide reproduces the sealed Kaya Cortex demo without installing Hermes, starting
agents, connecting model providers, or reading a local Tri-AI runtime. It is
the appropriate path for a recruiter, reviewer, or contributor who wants to
inspect the system model safely.

## What you need

- Git
- Python 3.11 or newer

The demo uses only Python's standard library. It does not need an API key, an
Ollama model, Telegram, a database, or any Tri-AI credentials.

## Run it locally

```powershell
git clone https://github.com/Ardit-Mishra/tri-ai.git
cd tri-ai
.\scripts\run_public_demo.ps1
```

Open `http://127.0.0.1:3018`. The Kaya header should read `Demo data` and
`Sealed demo`; the brain and details should show seven synthetic source
regions and four example tasks. Press `Ctrl+C` in the terminal to stop it.

To use a different loopback port:

```powershell
.\scripts\run_public_demo.ps1 -Port 8080
```

## Verify the boundary

With the local demo running, these checks should succeed:

```powershell
$demo = Invoke-RestMethod http://127.0.0.1:3018/api/snapshot
$demo.demo                 # True
$demo.sealed               # True
$demo.file_graph.synthetic # True
$demo.tasks.Count          # 4

(Invoke-WebRequest http://127.0.0.1:3018/artifact/demo_build/0 -SkipHttpErrorCheck).StatusCode
# 404
```

The artifact check is intentional: the public server exposes only the HTML
and the same embedded synthetic snapshot at `/api/snapshot`. It does not import
the private dashboard, read a runtime database, or expose file, artifact,
credential, model-provider, or agent-execution routes. Its content security
policy also forbids network connections from the page.

## Deploy the same sealed demo to Render

The repository contains a Render Blueprint in [`render.yaml`](../render.yaml).
It starts the dashboard with `--demo`, which fixes the server to its prebuilt
scenario and refuses the artifact surface.

1. Fork this repository, then sign in to [Render](https://render.com/).
2. Choose **New +** then **Blueprint** and select the fork.
3. Review the detected `tri-ai-demo` service. It needs no environment
   variables or secrets.
4. Create the service and wait for `/api/snapshot` to pass its health check.
5. Open the assigned `onrender.com` URL and repeat the boundary checks above.

`autoDeploy` is disabled on purpose. Review a public commit before manually
deploying it; a portfolio demo should remain deliberate and inspectable.

## Run the release checks

The public release test confirms that the deployment manifest stays demo-only
and that private operator materials cannot enter Git:

```powershell
python -m unittest tests.test_public_release_boundary tests.test_public_cortex_demo
```

For the complete local runtime and its Hermes-backed test suite, see
[`SETUP.md`](SETUP.md). That is deliberately a separate path from this public
demo.
