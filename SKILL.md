---
name: openrouter-review
description: Review code or text using DeepSeek via OpenRouter (R1:free → V4 Pro fallback). Use as Codex alternative for review. After presenting findings, implements fixes directly with Edit/Write. Works with git diffs and/or text files (notes, docs, .md).
---

## When to use this skill
- When Codex is unavailable or credits are exhausted
- As a standalone code or text reviewer (notes, drafts, specs)
- For a second opinion before making important changes

## Steps

### 1. Verify API key

Run:
```bash
echo $OPENROUTER_API_KEY
```

If empty, tell the user:
> "Missing `OPENROUTER_API_KEY`. Add it to `~/.claude/settings.json` under `env`, or set it with: `export OPENROUTER_API_KEY='your-key'`"
Stop execution.

### 2. Select model

Ask the user with AskUserQuestion:
- **Free — DeepSeek R1:free** — no cost, reasoning model. May hit rate limits.
- **Paid — DeepSeek V4 Pro** — ~$0.44/M tokens, more capable, no rate limits.
- **Auto (Recommended)** — tries free first; falls back to paid automatically if it fails.

Map the answer to `--model free|paid|auto` for the script.

### 3. Verify script exists

Check that `$HOME/.claude/scripts/openrouter_review.py` exists.
If not, tell the user to follow the installation instructions.

### 4. Collect input

Run in parallel:
```bash
git diff HEAD 2>/dev/null || true
git diff --cached 2>/dev/null || true
```

Also read any files the user specified when invoking the skill.

If there is no diff and no files specified, ask:
> "What do you want to review? Specify files or paste content."
Wait for a response before continuing.

### 5. Build the prompt

Write the following to `$HOME/.claude/scripts/.or_review_prompt.txt`:

```
=== CONTENT FOR REVIEW ===

[Include only if there is a diff]
--- GIT DIFF ---
<diff output>

[Include only if files were specified]
--- FILES ---
<filename>:
<file content>

Review the content above. Report bugs, security issues, and improvements. Number each finding.
```

### 6. Run the script

Detect Python dynamically:
```bash
PYTHON_BIN=$(command -v python 2>/dev/null || command -v python3 2>/dev/null || echo "$HOME/AppData/Local/Programs/Python/Python312/python.exe")
```

Then run:
```bash
"$PYTHON_BIN" "$HOME/.claude/scripts/openrouter_review.py" \
  --prompt-file "$HOME/.claude/scripts/.or_review_prompt.txt" \
  --mode review \
  --model <free|paid|auto from step 2>
```

If a fallback message appears in stderr (line starting with `[openrouter-review]`), show it to the user before presenting findings.

### 7. Present findings

Structure DeepSeek's response into clear sections.

For **code**:
- **Bugs / logic errors** (numbered findings)
- **Security** (numbered findings)
- **Improvements** (numbered findings)

For **text or notes** (.md, .txt, documents):
- **Clarity** (numbered findings)
- **Consistency** (numbered findings)
- **Weak arguments** (numbered findings)

If DeepSeek's response is already numbered and structured, present it as-is without reformatting.

### 8. Offer implementation

Ask the user:
> "Do you want me to implement any fix? Specify numbers (e.g. `1, 3`) or say `all` / `none`."

### 9. Apply fixes directly

For each confirmed fix, read the affected file and apply the change using Edit or Write.

Apply **independent fixes in parallel** (multiple Edit calls in one message).
Apply dependent fixes sequentially.

Report to the user what changed and what remains pending.
