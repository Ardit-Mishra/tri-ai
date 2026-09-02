# Setup — building it from nothing

Order matters. Each step is verifiable on its own; do not move on until the check passes.

Placeholders: `<desktop>` is the always-on machine, `<laptop>` the travelling one. No real
hostnames, addresses or keys appear in this repository.

---

## 0. Prerequisites

| | where | why |
|---|---|---|
| Python 3.12+ | both | the queue runner |
| Node.js | laptop | the router (`npx`) |
| Git | both | everything |
| A GPU | desktop | training; optional but it is the reason the desktop exists |

---

## 1. Private network

Install [Tailscale](https://tailscale.com) on every machine and sign them into one tailnet. This
is what makes location irrelevant: stable private addressing, no port forwarding, nothing exposed
publicly.

```bash
tailscale status          # every node should be listed and online
```

Add an SSH alias on the laptop so later commands read cleanly. In `~/.ssh/config`:

```
Host desktop
    HostName <desktop-tailscale-name-or-ip>
    User <user>
```

```bash
ssh desktop 'hostname'    # must return the desktop's name
```

> The desktop's SSH shell is **PowerShell**. `&&`, `>nul` and bash conditionals fail there in
> confusing ways. Use `;`, `2>$null`, PowerShell syntax — or put complexity in a `.cmd` file.

---

## 2. Local inference

Install [Ollama](https://ollama.com) on both machines. Bind it so the other machine can reach it:

```
OLLAMA_HOST=0.0.0.0:11434
```

Pull models sized to the hardware. **Check VRAM before pulling anything large** — a model that
exceeds the card offloads to CPU and becomes a different order of magnitude slower, not slightly
slower.

```bash
nvidia-smi --query-gpu=name,memory.total --format=csv
ollama pull qwen3.5:4b            # small, honest, fits anywhere
ollama pull nomic-embed-text      # embeddings, $0
ollama list | tail -n +2 | wc -l  # count — never eyeball it
```

---

## 3. The router

```powershell
npx omniroute            # leave running in its own terminal
```

It serves an OpenAI-compatible API on `localhost:20128` with a dashboard at the same address. In
the dashboard:

1. **Add only free providers** — local Ollama, or a genuinely free tier. A metered per-token
   provider breaks the $0 rule.
2. **Generate an API key** for local clients.

```powershell
setx OMNIROUTE_KEY "sk-omni-..."     # then OPEN A NEW TERMINAL
```

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:20128/models   # expect 200
```

> Probe `/models`. The router **404s `/health`**, and `/` returns a 307 redirect to `/dashboard` —
> a naive health check reports it down when it is fine. This exact mistake produced a false
> "router is down" diagnosis once already.

Prefer its **semantic lanes** (`auto/best-free`, `auto/coding:free`, `auto/cheap`, `auto/offline`)
over hardcoded model names. Free hosted models get renamed and retired constantly; a lane survives
that, a pinned name does not.

---

## 4. Hermes

Install [Hermes Agent](https://hermes-agent.nousresearch.com) on both machines.

```bash
hermes --version
```

Configure the model by **writing the YAML config directly**.

> Do **not** use `hermes config set model <name>`. It writes a bare scalar and drops the sibling
> `provider` and `base_url` keys, after which Hermes silently routes to the wrong provider and
> returns 400s that look like model errors.

**Check the context window before choosing.** Hermes requires **≥ 64,000**, which disqualifies
several otherwise-good local models (`qwen3:14b` at 40,960; `qwen2.5-coder:14b` at 32,768) despite
their fitting comfortably in VRAM.

```bash
ollama show <model> | grep -i context
```

Then build the fallback chain so a lane never dead-ends — descend through *independent failure
domains*: hosted, then a dynamically-chosen hosted lane, then another machine, then this machine.

```bash
hermes fallback add auto/best-free --base-url http://localhost:20128/v1
hermes fallback add <local-model>  --base-url http://<desktop>:11434/v1
hermes fallback add <local-model>  --base-url http://localhost:11434/v1
hermes fallback list
```

**Then test it deliberately.** Stop the primary and confirm the next level answers. A fallback is
only exercised when the primary fails, so a broken one stays invisible: in this system a fallback
pointed at a retired model returning `410 Gone` for weeks, and everything looked healthy.

### Pick the local model by measurement

Do not choose on parameter count. Score candidates on one prompt containing several commands whose
answers you already know, **including one designed to fail**, then read what they report. On this
hardware the 4.7B model was the only one that reported the failure; two larger ones hid or
fabricated. Deploy the honest one, and never make a fabricator the fallback.

---

## 5. The chat gateway

On the desktop:

```bash
hermes gateway run
```

Register it as a scheduled task so it survives a reboot — otherwise it runs only because a process
happens to still be up:

```bash
schtasks /Create /TN "AgentGateway" /TR "C:\path\to\start-gateway.cmd" /SC ONLOGON /F /RL LIMITED
schtasks /Query  /TN "AgentGateway"      # confirm it is Ready or Running, not Disabled
```

---

## 6. `claude-free`

Copy `claude-free.ps1` to `$HOME` and add to `Documents\WindowsPowerShell\profile.ps1`:

```powershell
function claude-free { & "$HOME\claude-free.ps1" @args }
```

See [CONTINUITY.md](CONTINUITY.md). It requires the router running and `OMNIROUTE_KEY` set, and it
changes env vars in child scope only — your normal premium CLI is untouched.

---

## 7. The queue

```bash
cp src/run_queue.py  <somewhere>/
cp src/queue.example.jsonl <somewhere>/queue.jsonl
python run_queue.py --dry-run
```

Write real tasks per [OPERATING.md](OPERATING.md). The only rule that matters: if you cannot write
a command that proves the task worked, it does not belong in the queue.

---

## Verification checklist

Nothing is "set up" until every line passes:

```bash
tailscale status                                                   # all nodes online
ssh desktop 'hostname'                                             # reachable
ssh desktop 'nvidia-smi --query-gpu=name,memory.total --format=csv'
ollama list | tail -n +2 | wc -l                                   # models present
curl -s -o /dev/null -w "%{http_code}\n" localhost:20128/models     # 200
hermes --version
hermes fallback list                                               # chain NOT empty
python run_queue.py --dry-run                                      # tasks parse
ssh desktop 'schtasks /Query /TN "AgentGateway"'                   # not Disabled
```

Then run one real queued task end to end and read `ledger.jsonl`. Until a task has passed *and*
been reverted on a deliberate failure, the safety mechanism is untested.
