---
name: openrouter-review
description: Review code or text using a dedicated-quota model ladder (Cerebras Gemma → Gemini free → OpenRouter :free → cheap paid), with per-provider RPM throttle and anti-hallucination quote verification. Use as automatic fallback when codex:rescue fails, or as standalone reviewer. After presenting findings, can implement fixes directly with Edit/Write. Works with git diffs and/or text files (notes, docs, .md).
---

## Cuándo usar este skill
- **Modo fallback (automático):** cuando `codex:rescue` falla, no responde o agota
  créditos. Correr DE INMEDIATO, sin preguntar nada al usuario (ver "Modo fallback").
- **Modo manual:** como revisor standalone de código o texto, o segunda opinión.

## Cómo funciona el script (contexto)

`~/.claude/scripts/openrouter_review.py` ya trae la robustez integrada — no hay
que orquestarla desde el skill:
- **Escalera multi-proveedor con cuota DEDICADA primero (2026-07-04):** el orden es
  `cerebras:gemma-4-31b` → `gemini:gemini-flash-latest` → `:free` de OpenRouter →
  pagado barato. Los dos primeros usan cuota TUYA (no la alberca `:free` compartida
  de OpenRouter, que da 429 masivo en horas pico sin importar tu saldo) y son rápidos
  (gemma ~0.7s, gemini ~8s). Verificados como revisores (5/5 bugs sembrados, 0 falsos
  positivos, respetan foco). Un id con prefijo `cerebras:`/`gemini:` va a su endpoint;
  sin prefijo = OpenRouter. Se saltan solos si falta su key en el entorno.
- **Throttle de RPM por proveedor** (`PROVIDER_MIN_INTERVAL`, estado en
  `openrouter_ratelimit.json`): respeta el rate limit gratis de cada proveedor ENTRE
  invocaciones (Cerebras 5 req/min → 12s; Gemini ~15 rpm → 4.5s). "No me cobren":
  quedarse dentro del free tier. Best-effort (no atómico entre procesos paralelos).
- La parte `:free` de OpenRouter sigue AUTO-REFRESCADA desde /api/v1/models (cache
  semanal, heurística por familia-de-código + contexto); `FREE_MODELS` queda como
  semilla. Retry+backoff inteligente en 429/5xx: respeta `Retry-After` si es corto,
  si no salta de modelo (los :free suelen tener tope diario, esperar no los desatura).
- **Inputs enormes (diffs de cientos de líneas):** truncan la salida de cualquier
  modelo y rompen el JSON. El script lo detecta (`finish_reason=length` / `MAX_TOKENS`)
  y **salta de modelo SIN cuarentenar** (no castiga a gemma/gemini por un diff gigante).
  Aun así, para un review muy grande conviene **`--model auto`** (un solo modelo) en vez
  de `consensus`, o **trocear el input**: el consensus con input enorme puede tardar o
  escalar a los `:free` saturados y colgarse.
- Si todos los gratis fallan, cae solo al pagado más barato decente
  (deepseek-v4-flash, ~$0.10/M in — un review típico cuesta centavos) y lo avisa
  en stderr con `AVISO: se uso modelo PAGADO`.
- Anti-alucinación: exige JSON con cita textual por finding y descarta
  automáticamente todo finding cuya cita no exista en el input (lo reporta en stderr).
- `--model consensus`: dos modelos distintos; findings `confirmado x2` vs
  `1 modelo (baja confianza)`.

## Pasos

### 1. Verificar API keys

```bash
echo "OR=${OPENROUTER_API_KEY:+ok} CB=${CEREBRAS_API_KEY:+ok} GM=${GEMINI_API_KEY:+ok}"
```

- `OPENROUTER_API_KEY` es **requerida** (catálogo `:free` + fallback pagado). Si falta:
  "Falta `OPENROUTER_API_KEY`. Debería estar en settings.json — reinicia Claude Code."
  Y detener.
- `CEREBRAS_API_KEY` y `GEMINI_API_KEY` son **recomendadas** (los dos primeros peldaños
  con cuota dedicada). Si faltan, el script se degrada solo a la escalera OpenRouter y lo
  avisa en stderr (`omito cerebras:… / gemini:…`) — funciona, solo pierde velocidad y la
  cuota propia. Las tres viven en `settings.json` bajo `env`.

### 2. Determinar el modo de invocación

**Modo fallback** (vienes de un codex:rescue fallido o el flujo de code review
te mandó aquí): NO preguntar nada. Usar `--model consensus` directo y saltar al paso 3.

**Modo manual** (el usuario invocó el skill directamente): preguntar con
AskUserQuestion:
- **Consensus — 2 modelos gratis (Recomendado)** — dos revisores independientes,
  findings etiquetados por confianza. Gratis salvo que los gratis fallen.
- **Auto — 1 modelo** — más rápido, escalera gratis con fallback a pagado barato.
- **Free — solo gratis** — nunca paga; si todos dan 429, falla y reporta.
- **Paid — directo al pagado** — deepseek-v4-flash (~$0.10/M in, $0.20/M out), sin esperas.

Mapear a `--model consensus|auto|free|paid`.

### 3. Recopilar input

Ejecutar en paralelo:
```bash
git diff HEAD 2>/dev/null || true
git diff --cached 2>/dev/null || true
```

Leer también cualquier archivo que el usuario (o el flujo de fallback) haya
especificado. Si no hay diff ni archivos: preguntar "¿Qué quieres revisar?"
y esperar (solo en modo manual; en fallback, usar los archivos que venían
del intento de codex:rescue).

### 4. Construir el prompt

Escribir a `$HOME/.claude/scripts/.or_review_prompt.txt`:

```
=== CONTEXTO PARA REVIEW ===

[Solo si hay diff]
--- GIT DIFF ---
<contenido del diff>

[Solo si hay archivos]
--- ARCHIVOS ---
<nombre de archivo>:
<contenido del archivo>

[Si vienes de codex:rescue, incluir también los invariantes del dominio que
se le habían pasado a codex]
--- INVARIANTES DEL DOMINIO ---
<invariantes>
```

No hace falta añadir instrucciones de review al prompt — el system prompt del
script ya las trae (incluido el formato JSON con citas).

### 5. Ejecutar el script

```bash
PYTHON_BIN=$(command -v python 2>/dev/null || command -v python3 2>/dev/null || echo "$HOME/AppData/Local/Programs/Python/Python312/python.exe")
"$PYTHON_BIN" "$HOME/.claude/scripts/openrouter_review.py" \
  --prompt-file "$HOME/.claude/scripts/.or_review_prompt.txt" \
  --mode review \
  --model <según paso 2>
```

Leer stderr: ahí van los saltos de escalera, findings descartados por cita no
verificable, y el aviso si se usó modelo pagado. Resumir eso al usuario en una
línea (ej. "qwen dio 429, revisaron gpt-oss-120b y nemotron, 1 finding
descartado por cita inventada, costo $0").

### 6. Presentar findings

El stdout ya viene estructurado (severidad, cita, problema, fix, confianza).
Presentarlo tal cual o condensado. Reglas:
- Findings `confirmado x2` → alta credibilidad.
- Findings `baja confianza` → presentarlos como tales; verificar contra el
  archivo real antes de actuar sobre ellos.
- Si el script descartó findings (stderr), mencionarlo: es la señal de que el
  filtro anti-alucinación está trabajando.

### 7. Aplicar fixes

- **Severidad alta** (bug, seguridad, dato incorrecto) y `confirmado x2`:
  aplicar autónomamente con Edit/Write (regla de CLAUDE.md global).
- **Alta pero baja confianza:** verificar contra el archivo real; si se
  confirma, aplicar; si no, descartar y decirlo.
- **Media/baja:** listar y preguntar (en modo manual) o solo listar (en fallback).

Fixes independientes en paralelo; dependientes en secuencia. Al final,
reportar qué cambió y qué quedó pendiente.

## Mantenimiento

Desde 2026-06-19 la escalera GRATIS se auto-refresca: `load_free_models()` consulta
`https://openrouter.ai/api/v1/models` y cachea una semana en
`openrouter_models_cache.json` (junto al script). Ya NO hay que actualizar
`FREE_MODELS` a mano — queda solo como semilla/fallback si la consulta falla. Para
forzar un refresco antes de la semana: borrar ese cache.

`PAID_MODELS` sigue hardcoded (precios verificados 2026-06-10); revisar solo si el
pagado de respaldo cambia de precio.
