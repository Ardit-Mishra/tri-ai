# GitHub repository settings

These are set by hand on GitHub, not by any file in the repository. Nothing here has been applied
yet. They make the repository read well when its link is shared or found in search.

## About box

Open the repository page and click the gear next to **About**.

- **Description:** `Multi-agent build system: free AI models do the work, a verification command decides if it's accepted.`
- **Website:** `https://tri-ai.arditmishra.com`
- **Topics** (lowercase, hyphenated, at most 20):
  `multi-agent`, `llm-agents`, `ai-agents`, `agent-orchestration`, `verification`, `ollama`,
  `free-llm`, `telegram-bot`, `python`, `sqlite`
- Leave **Releases**, **Packages** and **Deployments** unticked unless they hold something.

## Social preview

This is the image shown when the repository link is pasted into LinkedIn, Slack or X.

1. Go to **Settings → General → Social preview**.
2. Click **Edit → Upload an image** and choose `docs/img/social-preview.png`
   (1280×640 PNG, under 1 MB, exported from `docs/img/hero-dark.svg`).
3. Paste the repository link into a LinkedIn or Slack draft to confirm the card shows it.

To regenerate the README illustrations after an edit, run `python docs/img/build_art.py` from the
repository root; it rewrites every `*-dark.svg` and `*-light.svg` in `docs/img/`. The social
preview PNG is a 1280×640 browser screenshot of `hero-dark.svg` and has to be re-exported by hand.
