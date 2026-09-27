# Private Cortex Sources

Cortex has two deliberately separate modes:

- The public demonstration renders synthetic data only.
- The private Cortex reads metadata-only indexes held by the operator. It does
  not copy file contents into the browser, public repository, or demo host.

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

The resulting JSON has stable opaque item IDs, relative paths, size, modified
time, kind, and source provenance. It does not contain file contents or local
absolute paths. It is private operational metadata and must never be committed
to the public repository.

## Transport and access

The source agent intentionally does not upload anything. The next private
deployment phase will use a device-authenticated channel to collect an index
from each approved machine and keep the browser surface behind authentication.
Until that exists, indexes remain local to the device that created them.

Google Drive should remain an archive and optional source adapter, not a live
SQLite database. Phone application-private data requires per-app exports or
their official APIs; an unrooted Android device cannot expose every app's
private sandbox to another application.
