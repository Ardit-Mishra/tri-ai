# Every model the router lists, called once

**2026-09-30 — 574 models across 13 providers. 20 of them can actually be used.**

Raw rows: [`model-sweep-2026-09-30.json`](model-sweep-2026-09-30.json). Every number below
is derivable from that file; nothing here is a figure you have to take on trust.

The question that produced this was not "how many models are there." It was:

> I have a feeling that the models you have access to, even the highest Claude and ChatGPT
> ones — I feel like they're paid. I don't think you actually have access to them.

That was correct, and the cost of not having checked was already on the board: three agent
roles had been pinned to `auto/best-coding` for **29 recorded runs** on the strength of its
name alone.

## The result

| | count |
|---|---|
| models listed by the router | **574** |
| answered a one-line prompt at all | 45 |
| answered **as the model that was asked for** | 23 |
| of those, concrete ids rather than aliases | **20** |
| refused | 517 |
| HTTP 200 with empty content | 12 |

**96% of the catalogue is unreachable.** A listing is a claim about what a provider sells,
not a statement about what this account may call.

## Substitution is the interesting failure, not refusal

A refusal is honest: you asked, you were told no. The dangerous rows are the ones that
return **HTTP 200 with someone else's output**.

All 22 `auto/*` semantic aliases answered. Not one of them served what its name implies:

| asked for | actually served |
|---|---|
| `auto/claude-opus` | `google/gemma-4-31b-it` |
| `auto/claude-sonnet` | `google/gemma-4-31b-it` |
| `auto/reasoning` | `google/gemma-4-31b-it` |
| `auto/pro-coding` | `google/gemma-4-31b-it` |
| `auto/coding:pro` | `google/gemma-4-31b-it` |
| `auto/zai` | `mistral-code-latest` |
| `auto/minimax` | `mistral-code-latest` |

Sixteen distinct alias names, two distinct models behind them. An alias is a routing
preference the gateway is free to ignore, and it ignores them all. So the route registry
admits **no `auto/*` route at all**, including the ones whose served model happens to
share a word with the alias.

## A pin is not a model — the three layers

Each layer is invisible to the one above it, and each one failed independently during
this sweep:

1. **Listed but not entitled.** 517 of 574. The catalogue lists what the provider sells.
2. **Answered as something else.** 22 of the 45 that answered. HTTP 200 proves a response
   arrived, not that it came from the model named in the request.
3. **The pin was silently dropped.** `hermes -m qwen2.5-coder:14b` is routed to the
   gateway, which answers `400 Unable to determine provider`; Hermes then falls through to
   its first fallback and writes **`auto/smart`** into the usage file the ledger reads. The
   request is lost, the run succeeds, and the ledger records a model that was never asked
   for.

So admission to [`config/model-routes.tri-ai.json`](../config/model-routes.tri-ai.json)
requires all three to hold: it answered, the **response** named the model requested, and
`hermes -m <id> --usage-file` recorded that same id.

Consequence worth stating plainly: **ledger model attribution before 2026-09-30 is not
evidence.** Every run before this sweep was routed through an alias.

## The context-window number is not the output number

The second question behind this sweep was about advertised limits — a million-token
context next to a far smaller cap on what the model may actually produce.

**221 models advertise a context window of 1,000,000 tokens or more. For 163 of them the
maximum output is under a quarter of that.** The most common pairing in the catalogue is a
1M+ context with a **128,000**-token output ceiling; 46 more cap at **64,000**.

| advertised max output | models claiming ≥1M context |
|---|---|
| 128,000 | 87 |
| 64,000 | 46 |
| 1,048,576 | 33 |
| 65,536 | 23 |
| not stated | 23 |

A context window is how much the model may *read*. Planning a long generation against that
figure is planning against the wrong one — and the sweep shows the two differ by an order
of magnitude across most of the catalogue.

## Why the refusals were grouped

The error message *is* the diagnosis. Five causes account for all 517:

| count | cause |
|---|---|
| 109 | `400` — not in the active live catalogue for that provider |
| 105 | `500` — a bridge sandbox misconfiguration, identical for every model behind it |
| 49 | `466` — the Copilot CLI version is no longer supported |
| 44 | `404` — the provider function does not exist |
| 26 | `503` — the Codex app-server transport has no url or token configured |

Four of the five are one fix each, not 517 separate problems. Counting them as "517
failures" would have hidden that; grouping by message is what made it legible.

## Reproducing this

The sweep calls each id once with a fixed one-line prompt and records the id, the provider,
the advertised limits, the latency, the status and — critically — the **served** model name
from the response body. No credential appears in the output; provider error text is
redacted for filesystem paths (`<home>`) before it is published here.
