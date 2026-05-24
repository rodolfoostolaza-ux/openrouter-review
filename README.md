# openrouter-review

A Claude Code skill that reviews code and text using [DeepSeek](https://deepseek.com/) via [OpenRouter](https://openrouter.ai/), with automatic model fallback and direct fix implementation.

## What it does

1. **Reviews** your current git diff and/or specified files using DeepSeek
2. **Presents** numbered, structured findings (bugs, security issues, improvements)
3. **Implements** confirmed fixes directly using Claude's Edit/Write tools

## Model cascade

| Step | Model | Cost |
|------|-------|------|
| 1st attempt | `deepseek/deepseek-r1:free` | Free |
| Auto-fallback | `deepseek/deepseek-v4-pro` | ~$0.44/M input tokens |

You can also choose `free`, `paid`, or `auto` (default) at invocation time.

## Requirements

- [Claude Code](https://claude.ai/code)
- Python 3.8+ (no extra packages — uses stdlib only)
- An [OpenRouter](https://openrouter.ai/) API key

## Installation

```bash
# 1. Copy the Python script
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

Claude will ask which model to use, then present findings and offer to apply fixes.

## Files

| File | Purpose |
|------|---------|
| `SKILL.md` | Skill definition — install to `~/.claude/skills/openrouter-review/` |
| `openrouter_review.py` | Python HTTP client for OpenRouter — install to `~/.claude/scripts/` |
| `test_openrouter_review.py` | Tests (no API key needed) |

## Running tests

```bash
python test_openrouter_review.py
```

## Why this exists

Codex is great but costs credits. This skill gives you a free-first reviewer that falls back to a cheap paid model — useful as a daily driver or Codex backup.

## License

MIT
