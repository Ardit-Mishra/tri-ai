# Reproduce the Public Demo

This guide reproduces the public visual demo without installing Hermes, starting
agents, connecting model providers, or reading a local Tri-AI runtime. It is
the appropriate path for a recruiter, reviewer, or contributor who wants to
inspect the system model safely.

## What you need

- Git
- Python 3.11 or newer
- An internet connection only to install the small dashboard dependency

The demo does not need an API key, an Ollama model, Telegram, a database, or
any Tri-AI credentials.

## Run it locally

```powershell
git clone https://github.com/Ardit-Mishra/tri-ai.git
cd tri-ai
python -m pip install -r requirements-dashboard.txt
.\scripts\run_public_demo.ps1
```

Open `http://127.0.0.1:3018`. The header should read
`DEMONSTRATION DATA // NO LIVE WORK` and the page should show four example
tasks. Press `Ctrl+C` in the terminal to stop it.

To use a different loopback port:

```powershell
.\scripts\run_public_demo.ps1 -Port 8080
```

## Verify the boundary

With the local demo running, these checks should succeed:

```powershell
$demo = Invoke-RestMethod http://127.0.0.1:3018/api/snapshot
$demo.demo                 # True
$demo.tasks.Count          # 4

(Invoke-WebRequest http://127.0.0.1:3018/artifact/demo_build/0 -SkipHttpErrorCheck).StatusCode
# 404
```

The artifact check is intentional: a public demonstration must not become a
path to task artifacts. The dashboard has no action routes, credentials,
runtime database, model-provider calls, or agent-execution endpoint.

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

`autoDeploy` is disabled in the Blueprint on purpose. Review a commit before
manually deploying it; a portfolio demo should remain deliberate and
inspectable.

## Run the release checks

The public release test confirms that the deployment manifest stays demo-only
and that private operator materials cannot enter Git:

```powershell
$env:PYTHONPATH = 'src'
python -m unittest tests.test_public_release_boundary tests.test_dashboard_web
```

For the complete local runtime and its Hermes-backed test suite, see
[`SETUP.md`](SETUP.md). That is deliberately a separate path from this public
demo.
