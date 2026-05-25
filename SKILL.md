---
name: openrouter-review
description: Review code or text using OpenRouter (openrouter/free → DeepSeek V4 Pro fallback). Use as Codex alternative for review. After presenting findings, can implement fixes directly with Edit/Write. Works with git diffs and/or text files (notes, docs, .md).
---

## Cuándo usar este skill
- Cuando Codex no está disponible o sus créditos se agotaron
- Como revisor standalone de código o texto (notas, drafts, specs)
- Para obtener una segunda opinión antes de hacer cambios importantes

## Pasos

### 1. Verificar API key

Correr:
```bash
echo $OPENROUTER_API_KEY
```

Si la salida está vacía, decirle al usuario:
> "Falta `OPENROUTER_API_KEY`. Debería estar en settings.json — reinicia Claude Code o configúrala con: `$env:OPENROUTER_API_KEY = 'tu-key'`"
Y detener la ejecución.

### 2. Seleccionar modelo

Preguntar al usuario con AskUserQuestion:
- **Gratis — openrouter/free** — sin costo, elige automáticamente el mejor modelo gratuito disponible. Puede tener rate limits.
- **Pagado — DeepSeek V4 Pro** — $0.44/M tokens, más capaz y sin límites de tasa.
- **Auto** — intenta gratis primero; si falla, usa pagado automáticamente. (Recomendado)

Mapear la respuesta a `--model free|paid|auto` para el script.

### 3. Verificar que el script existe

Verificar que existe `C:\Users\chido\.claude\scripts\openrouter_review.py`.
Si no existe, decirle al usuario que reinstale el skill.

### 4. Recopilar input

Ejecutar en paralelo:
```bash
git diff HEAD 2>/dev/null || true
git diff --cached 2>/dev/null || true
```

Leer también cualquier archivo que el usuario haya especificado al invocar el skill.

Si no hay diff ni archivos especificados, preguntar:
> "¿Qué quieres revisar? Especifica archivos o pega el contenido."
Y esperar respuesta antes de continuar.

### 5. Construir el prompt

Escribir el siguiente contenido a `$HOME/.claude/scripts/.or_review_prompt.txt`
(ruta fija que funciona en bash y PowerShell):

```
=== CONTEXTO PARA REVIEW ===

[Incluir esta sección solo si hay diff]
--- GIT DIFF ---
<contenido del diff>

[Incluir esta sección solo si hay archivos especificados]
--- ARCHIVOS ---
<nombre de archivo>:
<contenido del archivo>

Revisa el contenido anterior. Reporta bugs, problemas de seguridad y mejoras. Numera cada finding.
```

### 6. Ejecutar el script

Detectar Python dinámicamente:
```bash
PYTHON_BIN=$(command -v python 2>/dev/null || command -v python3 2>/dev/null || echo "$HOME/AppData/Local/Programs/Python/Python312/python.exe")
```

Luego ejecutar:
```bash
"$PYTHON_BIN" "$HOME/.claude/scripts/openrouter_review.py" \
  --prompt-file "$HOME/.claude/scripts/.or_review_prompt.txt" \
  --mode review \
  --model <free|paid|auto según paso 2>
```

Si aparece un mensaje de fallback en stderr (línea que empieza con `[openrouter-review]`), mostrárselo al usuario antes de presentar los findings.

### 7. Presentar findings

Estructurar la respuesta de DeepSeek en secciones claras.

Para **código**:
- **Bugs / errores lógicos** (findings numerados)
- **Seguridad** (findings numerados)
- **Mejoras** (findings numerados)

Para **texto o notas** (.md, .txt, documentos):
- **Claridad** (findings numerados)
- **Consistencia** (findings numerados)
- **Argumentos débiles** (findings numerados)

Si la respuesta de DeepSeek ya viene numerada y estructurada, presentarla tal cual sin reformatear.

### 8. Ofrecer implementación

Preguntar al usuario:
> "¿Quieres que implemente algún fix? Indica los números (ej. `1, 3`) o di `todos` / `ninguno`."

### 9. Aplicar fixes directamente

Para cada fix confirmado, leer el archivo afectado y aplicar el cambio usando Edit o Write.

Aplicar fixes **independientes en paralelo** (múltiples Edit en el mismo mensaje).
Fixes que dependen unos de otros aplicarlos en secuencia.

Después de aplicar, reportar al usuario qué cambió y qué quedó pendiente.
