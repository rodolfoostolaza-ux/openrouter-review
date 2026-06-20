# openrouter-review

A Claude Code skill that reviews code and text through [OpenRouter](https://openrouter.ai/), using a **self-refreshing ladder of free models** with a cheap-paid fallback, anti-hallucination quote verification, and direct fix implementation. Built as a free-first alternative to (or backup for) Codex review.

## What it does

1. **Reviews** your current git diff and/or specified files.
2. **Presents** numbered, structured findings (bugs, security issues, improvements), each tied to a verbatim quote from the input.
3. **Implements** confirmed fixes directly using Claude's Edit/Write tools.

## How it picks models

The free-model ladder is **not hardcoded**. On each run the script queries OpenRouter's `/api/v1/models` catalog, keeps the free models that are actually useful (filters by context window and code-friendly model family), and caches the result for a week. Ordering is **code-specialist and agile-first**: `qwen3-coder` leads, then capable MoE models, with small models pushed to the back and slow dense giants (e.g. a dense 405B) excluded outright. The hardcoded `FREE_MODELS` list is only a seed / safety net if the catalog query fails. No LLM is involved in the selection — it's a deterministic heuristic, zero extra tokens.

| Step | What runs | Cost |
|------|-----------|------|
| Free ladder | Auto-selected free models (e.g. `qwen3-coder:free`, `gpt-oss-120b:free`, …) | Free |
| Paid fallback | `deepseek/deepseek-v4-flash` (~$0.10/M in, $0.20/M out) | Cents per review |

Rate limits (HTTP 429) are handled smartly: the script honors a short `Retry-After`, otherwise it jumps to the next model immediately instead of sleeping against a daily cap that won't clear.

**Misbehaving models get quarantined.** If a model returns garbage (invalid JSON even after a corrective retry) or a hard HTTP 4xx, it's pulled from the ladder for a 24h cooldown and the event is appended to `openrouter_review.log`. A 429 does *not* quarantine — that's just a busy model, not a broken one. The quarantine self-heals when the cooldown expires.

## Modes

Choose at invocation time (`--model`):

- **`consensus`** (recommended) — two different models review independently; findings are labeled `confirmado x2` (high confidence) vs `baja confianza`.
- **`auto`** — free ladder, falling back to the cheap paid model if all free models fail.
- **`free`** — free only; if everything 429s, it fails and reports.
- **`paid`** — straight to the cheap paid model, no waiting.

## Anti-hallucination

Every finding must carry a verbatim `quote` copied from the reviewed content. The script normalizes whitespace and **discards any finding whose quote doesn't actually appear in the input** (reported on stderr). This kills the most common failure mode of cheap models: confidently inventing bugs in code that doesn't exist.

## Requirements

- [Claude Code](https://claude.ai/code)
- Python 3.8+ (no extra packages — stdlib only)
- An [OpenRouter](https://openrouter.ai/) API key

## Installation

```bash
# 1. Copy the Python script (and its tests)
mkdir -p ~/.claude/scripts
cp openrouter_review.py ~/.claude/scripts/

# 2. Install the skill
mkdir -p ~/.claude/skills/openrouter-review
cp SKILL.md ~/.claude/skills/openrouter-review/
```

Set your API key. The easiest way is to add it to `~/.claude/settings.json`:

```json
{
  "env": {
    "OPENROUTER_API_KEY": "your-key-here"
  }
}
```

Or set it per session:

```bash
export OPENROUTER_API_KEY="your-key-here"          # bash
$env:OPENROUTER_API_KEY = "your-key-here"           # PowerShell
```

## Usage

```
/openrouter-review                        # review current git diff
/openrouter-review path/to/notes.md       # review a file
/openrouter-review src/parser.py          # review specific code
```

Claude asks which mode to use, then presents findings and offers to apply fixes. It also runs automatically as a fallback when Codex review fails.

## Files

| File | Purpose |
|------|---------|
| `SKILL.md` | Skill definition — install to `~/.claude/skills/openrouter-review/` |
| `openrouter_review.py` | Python HTTP client for OpenRouter — install to `~/.claude/scripts/` |
| `test_openrouter_review.py` | Tests (no API key needed) — covers model-ranking and rate-limit logic |

A `openrouter_models_cache.json` file is created next to the script at runtime (the weekly model cache). It's local state — don't commit it.

## Running tests

```bash
python test_openrouter_review.py
```

## Why this exists

Codex is great but costs credits and sometimes isn't available. This skill gives you a free-first reviewer that auto-tracks OpenRouter's shifting free-model catalog and falls back to a cheap paid model — useful as a daily driver or a Codex backup.

## License

MIT
