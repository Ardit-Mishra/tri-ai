# Private Cortex Sources

Cortex has two deliberately separate modes:

- The public demonstration renders synthetic data only.
- The private Cortex reads metadata-only indexes held by the operator. It does
  not copy file contents into the browser, public repository, or demo host.

This means a Render deployment can demonstrate the product and receive vetted
code releases, but it is never the route through which a personal Drive or a
device index becomes visible. The live personal view runs separately, with the
operator's private source directory.

## Current source states

An `indexed` source has a locally generated metadata index. A `pending` source
has been registered but contains no received metadata yet. An `unavailable`
source could not be read. Cortex renders all indexed items as a dense GPU field
and keeps source clusters clickable; it does not ship every file name and path
to the browser at initial load.

The initial source inventory can include Drive, desktop, laptop, phone,
Obsidian, repositories, deployment records, and opted-in Claude/Codex exports.
Listing one of these in the interface is not evidence that it is connected.
Only the state and node count are evidence.

## Start the live private view

On the machine holding the private index directory, start the loopback-only
operator dashboard:

```powershell
.\scripts\run_private_cortex.ps1 -Port 3026
```

The first launch creates `$HOME\.tri-ai\private-sources\sources.json`. It
registers Desktop, Laptop, Google Drive, Phone, Obsidian, GitHub,
Vercel/Render, Claude/Codex, local Ollama, OmniRoute, and FreeLLMAPI as
authorized regions. Registration is visual only: every region begins
`pending`, and only changes to `indexed` after its approved source agent has
written metadata.

The dashboard's event stream checks the index signature on each snapshot. A
source update changes the scene revision automatically; no browser refresh or
Render deployment is required.

Each full index is accompanied by a small local `.summary.json` sidecar. The
sidecar contains source IDs, counts, and folder counts only. Cortex uses it to
start a large private field quickly without reading file names, paths, or file
contents into the dashboard process. The source agent writes the matching
sidecar automatically after every successful index rebuild.

## Indexing one device

On the device that owns the folders, run the source agent with only the roots
you intend to authorize:

```powershell
$env:PYTHONPATH = "C:\path\to\tri-ai\src"
python -m dashboard.private_index_agent `
  --output "$HOME\.tri-ai\private-sources\desktop.json" `
  --root "Desktop=C:\Users\you\Desktop" `
  --root "Documents=C:\Users\you\Documents"
```

For an always-updating small source on that same device, use the PowerShell
wrapper. Large profile or application sources should use a much slower cadence
or an operating-system scheduled task; a five-minute full walk is not an
incremental watcher.

```powershell
.\scripts\watch_private_cortex_source.ps1 `
  -OutputName desktop.json `
  -Root "Desktop=C:\Users\you\Desktop","Documents=C:\Users\you\Documents" `
  -EverySeconds 21600
```

The resulting JSON has stable opaque item IDs, relative paths, size, modified
time, kind, and source provenance. It does not contain file contents or local
absolute paths. It is private operational metadata and must never be committed
to the public repository.

## Indexing a desktop profile

For a fuller machine map, authorize the user profile and the application
configuration roots as one Desktop region. The profile pass skips `AppData` so
it is not duplicated; the application passes retain useful project and tool
metadata while excluding caches, sessions, and credential stores. The exclusion
switch matches directory names anywhere below an approved root, not an absolute
path. Adjust the list to fit the machine, but keep secret and runtime state out
of an index that may later be transported between your own devices.

```powershell
$env:PYTHONPATH = "C:\path\to\tri-ai\src"
python -m dashboard.private_index_agent `
  --output "$HOME\.tri-ai\private-sources\desktop.json" `
  --root "desktop-profile=$HOME" `
  --root "desktop-local-apps=$env:LOCALAPPDATA" `
  --root "desktop-roaming-apps=$env:APPDATA" `
  --exclude-directory AppData `
  --exclude-directory .ssh `
  --exclude-directory .aws `
  --exclude-directory .azure `
  --exclude-directory .gnupg `
  --exclude-directory .tri-ai `
  --exclude-directory .codex `
  --exclude-directory .claude `
  --exclude-directory node_modules `
  --exclude-directory .git `
  --exclude-directory Temp `
  --exclude-directory Cache `
  --exclude-directory "Code Cache" `
  --exclude-directory GPUCache `
  --exclude-directory CrashDumps `
  --exclude-directory Cookies `
  --exclude-directory Credentials `
  --exclude-directory TokenBroker `
  --exclude-directory Vault
```

This produces one dense Desktop cluster from several privately indexed roots.
Cortex keeps the individual entries inside the private index, renders a
bounded representative field, and only exposes aggregate source information
until a future authenticated expansion is implemented.

## Transport and access

The source agent intentionally does not upload anything. To view the real
dashboard from a phone, keep the server loopback-only and publish it through a
device-authenticated private channel such as Tailscale Serve. That setup is a
separate operator action because it changes network reachability; it must not
be replaced with a public Render route. A future remote collector will fetch
only an authenticated machine's metadata index, never contents or credentials.

With the Tailscale app signed into the same tailnet on both devices, enable
the private proxy only after the loopback dashboard is healthy:

```powershell
.\scripts\serve_private_cortex_tailscale.ps1 -Enable
```

The command prints a `https://<device>.<tailnet>.ts.net/` address. It is
available only to authenticated tailnet devices. This project deliberately
does not use `tailscale funnel` for Cortex; Funnel would make it public.
Disable the proxy later with:

```powershell
.\scripts\serve_private_cortex_tailscale.ps1 -Disable
```

Google Drive should remain an archive and optional source adapter, not a live
SQLite database. Phone application-private data requires per-app exports or
their official APIs; an unrooted Android device cannot expose every app's
private sandbox to another application.
