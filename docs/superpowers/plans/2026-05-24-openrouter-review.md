# openrouter-review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Instalar globalmente un skill de Claude Code que revisa código y texto con DeepSeek R1:free (fallback a V4 Pro) y despacha subagentes Claude para implementar los fixes confirmados por el usuario.

**Architecture:** Dos archivos: `openrouter_review.py` maneja la llamada HTTP a OpenRouter con fallback automático de modelo; `SKILL.md` instruye a Claude en el flujo completo (detectar input → llamar script → presentar findings → despachar agentes). El script usa solo stdlib Python (sin pip). El skill vive en `~/.claude/skills/` para estar disponible globalmente en todas las sesiones.

**Tech Stack:** Python 3.12 stdlib (`urllib.request`, `json`, `argparse`), Claude Code Skills system, OpenRouter API (OpenAI-compatible), modelos `deepseek/deepseek-r1:free` y `deepseek/deepseek-v4-pro`.

---

### Task 1: Python script — implementación base

**Files:**
- Create: `C:\Users\chido\.claude\scripts\openrouter_review.py`
- Create: `C:\Users\chido\.claude\scripts\test_openrouter_review.py`

- [ ] **Step 1: Escribir el test de API key faltante**

Crear `C:\Users\chido\.claude\scripts\test_openrouter_review.py`:

```python
import os
import subprocess
import sys
import tempfile

SCRIPT = r"C:\Users\chido\.claude\scripts\openrouter_review.py"


def run(extra_env=None, prompt="test prompt"):
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(prompt)
        tmp = f.name
    env = {k: v for k, v in os.environ.items()}
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, SCRIPT, "--prompt-file", tmp],
        env=env,
        capture_output=True,
        text=True,
    ), tmp


def test_missing_api_key():
    env_sin_key = {k: v for k, v in os.environ.items() if k != "OPENROUTER_API_KEY"}
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write("test")
        tmp = f.name
    result = subprocess.run(
        [sys.executable, SCRIPT, "--prompt-file", tmp],
        env=env_sin_key,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1, f"Esperado exit 1, recibido {result.returncode}"
    assert "OPENROUTER_API_KEY" in result.stderr, f"Esperado nombre de key en stderr: {result.stderr}"
    print("PASS: test_missing_api_key")


if __name__ == "__main__":
    test_missing_api_key()
    print("\nTodos los tests pasaron.")
```

- [ ] **Step 2: Correr test para verificar que falla** (script aún no existe)

```powershell
python "C:\Users\chido\.claude\scripts\test_openrouter_review.py"
```

Esperado: error porque `openrouter_review.py` no existe todavía.

- [ ] **Step 3: Escribir el script Python**

Crear `C:\Users\chido\.claude\scripts\openrouter_review.py`:

```python
#!/usr/bin/env python3
"""Revisor de código/texto via OpenRouter DeepSeek. Fallback automático R1:free → V4 Pro."""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

FREE_MODEL = "deepseek/deepseek-r1:free"
PAID_MODEL = "deepseek/deepseek-v4-pro"
API_URL = "https://openrouter.ai/api/v1/chat/completions"

SYSTEM_PROMPTS = {
    "review": (
        "Eres un revisor experto. Analiza el siguiente contenido y reporta: "
        "(1) bugs o errores lógicos, (2) problemas de seguridad, "
        "(3) mejoras de claridad o estructura. "
        "Sé específico: señala línea o sección, describe el problema, "
        "sugiere el fix exacto. Numera cada finding."
    ),
    "rescue": (
        "Eres un ingeniero senior. Para cada problema encontrado, "
        "propón el fix exacto con el código o texto corregido completo. "
        "Formato: problema → fix listo para aplicar. Numera cada fix."
    ),
}


def call_model(prompt: str, model: str, mode: str, api_key: str) -> str:
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPTS[mode]},
            {"role": "user", "content": prompt},
        ],
    }).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Revisor via OpenRouter DeepSeek")
    parser.add_argument("--prompt-file", required=True, help="Archivo con el prompt completo")
    parser.add_argument("--mode", default="review", choices=["review", "rescue"])
    args = parser.parse_args()

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print(
            "ERROR: OPENROUTER_API_KEY no está configurada en el entorno.\n"
            "Agrégala con: $env:OPENROUTER_API_KEY = 'tu-key'",
            file=sys.stderr,
        )
        sys.exit(1)

    with open(args.prompt_file, encoding="utf-8") as f:
        prompt = f.read()

    # Intento con modelo gratuito
    try:
        print(call_model(prompt, FREE_MODEL, args.mode, api_key))
        return
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            print(
                f"[openrouter-review] {FREE_MODEL} devolvió 429 — "
                f"reintentando con {PAID_MODEL}...",
                file=sys.stderr,
            )
        else:
            print(
                f"[openrouter-review] Error HTTP {exc.code} con {FREE_MODEL}: {exc.reason}",
                file=sys.stderr,
            )
    except Exception as exc:
        print(f"[openrouter-review] Error con {FREE_MODEL}: {exc}", file=sys.stderr)

    # Fallback a modelo pagado
    try:
        print(call_model(prompt, PAID_MODEL, args.mode, api_key))
    except Exception as exc:
        print(f"ERROR: Ambos modelos fallaron. Último error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Correr test — debe pasar ahora**

```powershell
python "C:\Users\chido\.claude\scripts\test_openrouter_review.py"
```

Esperado: `PASS: test_missing_api_key` / `Todos los tests pasaron.`

- [ ] **Step 5: Smoke test con API real**

```powershell
$tmp = "$env:TEMP\or_smoke.txt"
Set-Content $tmp "def add(a, b): return a - b  # debería sumar, no restar"
python "C:\Users\chido\.claude\scripts\openrouter_review.py" --prompt-file $tmp
```

Esperado: DeepSeek señala que `a - b` es un bug y sugiere `a + b`.

- [ ] **Step 6: Test de fallback — usar modelo free inválido**

Verificar que el fallback funciona cambiando temporalmente `FREE_MODEL`:

```powershell
# Cambiar FREE_MODEL a modelo inexistente
(Get-Content "C:\Users\chido\.claude\scripts\openrouter_review.py") `
  -replace 'FREE_MODEL = "deepseek/deepseek-r1:free"', `
           'FREE_MODEL = "deepseek/nonexistent-model:free"' |
  Set-Content "C:\Users\chido\.claude\scripts\openrouter_review.py"

# Correr el smoke test — debe usar V4 Pro y aun responder
python "C:\Users\chido\.claude\scripts\openrouter_review.py" --prompt-file $tmp

# Restaurar
(Get-Content "C:\Users\chido\.claude\scripts\openrouter_review.py") `
  -replace 'FREE_MODEL = "deepseek/nonexistent-model:free"', `
           'FREE_MODEL = "deepseek/deepseek-r1:free"' |
  Set-Content "C:\Users\chido\.claude\scripts\openrouter_review.py"
```

Esperado en stderr: `[openrouter-review] Error HTTP ... con deepseek/nonexistent-model:free` seguido de respuesta de V4 Pro en stdout.

---

### Task 2: SKILL.md — instrucciones del skill global

**Files:**
- Create: `C:\Users\chido\.claude\skills\openrouter-review\SKILL.md`

- [ ] **Step 1: Crear el directorio del skill**

```powershell
New-Item -ItemType Directory -Force "C:\Users\chido\.claude\skills\openrouter-review" | Out-Null
Write-Output "ok"
```

Esperado: `ok`

- [ ] **Step 2: Escribir el SKILL.md**

Crear `C:\Users\chido\.claude\skills\openrouter-review\SKILL.md`:

````markdown
---
name: openrouter-review
description: Review code or text using DeepSeek via OpenRouter (R1:free → V4 Pro fallback). Use as Codex alternative for review. After presenting findings, can dispatch parallel Claude subagents to implement confirmed fixes. Works with git diffs and/or text files (notes, docs, .md).
---

## Cuándo usar este skill
- Cuando Codex no está disponible o sus créditos se agotaron
- Como revisor standalone de código o texto (notas, drafts, specs)
- Para obtener una segunda opinión antes de mergear cambios

## Pasos

### 1. Verificar API key

Correr:
```bash
echo $OPENROUTER_API_KEY
```

Si la salida está vacía, decirle al usuario:
> "Falta `OPENROUTER_API_KEY`. Configúrala con: `$env:OPENROUTER_API_KEY = 'tu-key'`"
Y detener la ejecución.

### 2. Verificar que el script existe

Verificar que existe `C:\Users\chido\.claude\scripts\openrouter_review.py`.
Si no existe, decirle al usuario que corra el plan de instalación primero.

### 3. Recopilar input

Ejecutar en paralelo:
```bash
git diff HEAD 2>/dev/null || true
git diff --cached 2>/dev/null || true
```

Leer también cualquier archivo que el usuario haya especificado al invocar el skill.

Si no hay diff ni archivos especificados, preguntar:
> "¿Qué quieres revisar? Especifica archivos o pega el contenido."
Y esperar respuesta antes de continuar.

### 4. Construir el prompt

Escribir el siguiente contenido a `$HOME/.claude/scripts/.or_review_prompt.txt`
(ruta fija, funciona en bash y PowerShell por igual):

```
=== CONTEXTO PARA REVIEW ===

[Incluir si hay diff]
--- GIT DIFF ---
<contenido del diff>

[Incluir si hay archivos]
--- ARCHIVOS ---
<nombre de archivo y contenido de cada archivo>

Revisa el contenido anterior. Reporta bugs, problemas de seguridad y mejoras. Numera cada finding.
```

### 5. Ejecutar el script

```bash
python "$HOME/.claude/scripts/openrouter_review.py" \
  --prompt-file "$HOME/.claude/scripts/.or_review_prompt.txt" \
  --mode review
```

Mostrar al usuario si aparece el mensaje de fallback en stderr.

### 6. Presentar findings

Estructurar la respuesta de DeepSeek en secciones claras. Para código:
- **Bugs / errores lógicos** (findings numerados)
- **Seguridad** (findings numerados)
- **Mejoras** (findings numerados)

Para texto o notas:
- **Claridad** (findings numerados)
- **Consistencia** (findings numerados)
- **Argumentos débiles** (findings numerados)

### 7. Ofrecer implementación

Preguntar al usuario:
> "¿Quieres que implemente algún fix? Indica los números (ej. `1, 3`) o di `todos` / `ninguno`."

### 8. Despachar subagentes (si el usuario confirma)

Para cada fix confirmado, preparar un task description que incluya:
- El finding exacto de DeepSeek
- La sección o archivo afectado
- El fix sugerido

Despachar los fixes **independientes en paralelo** usando el Agent tool (subagent_type: "claude").
Fixes que dependen unos de otros ejecutarlos en secuencia.

Cada subagente tiene acceso a Edit, Write y Bash para aplicar el fix.

Después de que todos los agentes terminen, reportar al usuario qué se implementó y qué quedó pendiente.
````

- [ ] **Step 3: Verificar que Claude Code detecta el skill**

Cerrar y reabrir Claude Code (o iniciar nueva sesión). Verificar que `openrouter-review` aparece en la lista de skills disponibles.

Alternativa sin reiniciar: en una nueva sesión de Claude Code, el skill aparecerá en el system-reminder bajo `available skills`.

---

### Task 3: Test de integración end-to-end

**Files:** ninguno nuevo — es verificación del flujo completo.

- [ ] **Step 1: Crear un diff de prueba**

Crear un archivo temporal con un bug obvio:

```powershell
$testFile = "$env:TEMP\test_review_integration.py"
Set-Content $testFile @'
def calcular_promedio(numeros):
    return sum(numeros) / len(numeros)  # falla si lista vacía

def buscar_usuario(db, user_id):
    query = f"SELECT * FROM users WHERE id = {user_id}"  # SQL injection
    return db.execute(query)

def procesar_datos(items):
    resultados = []
    for i in range(len(items)):  # debería usar enumerate
        resultados.append(items[i] * 2)
    return resultados
'@
Write-Output "Archivo creado: $testFile"
```

- [ ] **Step 2: Construir prompt y llamar al script directamente**

```powershell
$prompt = "$env:TEMP\or_integration_prompt.txt"
Set-Content $prompt "=== ARCHIVO PARA REVIEW ===`n$(Get-Content $testFile -Raw)"
python "C:\Users\chido\.claude\scripts\openrouter_review.py" --prompt-file $prompt
```

Esperado: DeepSeek debe señalar al menos:
- División por cero en `calcular_promedio` con lista vacía
- SQL injection en `buscar_usuario`
- Uso de `range(len())` en lugar de `enumerate`

- [ ] **Step 3: Invocar el skill completo desde Claude Code**

En una sesión de Claude Code con el skill instalado, invocar:
```
/openrouter-review
```

Verificar que Claude:
1. Corre `git diff` automáticamente
2. Construye el prompt
3. Llama al script Python
4. Presenta findings numerados y estructurados
5. Pregunta si implementar

- [ ] **Step 4: Probar con archivo de notas**

```
/openrouter-review C:\Users\chido\ruta\a\nota_dee.md
```

Verificar que Claude lee el archivo, lo incluye en el prompt, y presenta findings en categorías de texto (Claridad / Consistencia / Argumentos débiles).

- [ ] **Step 5: Probar implementación con subagentes**

En el paso anterior, cuando Claude presente los findings, responder:
> "implementa el 1"

Verificar que Claude despacha un subagente que aplica el fix (no Claude directamente, sino un Agent).

---

## Notas de instalación

La API key ya está disponible en la sesión de Rodolfo. Si en una nueva sesión no está, agregarla al `$PROFILE` de PowerShell:

```powershell
Add-Content $PROFILE "`n`$env:OPENROUTER_API_KEY = 'tu-key'"
```

O al settings de Claude Code como variable de entorno de sesión.
