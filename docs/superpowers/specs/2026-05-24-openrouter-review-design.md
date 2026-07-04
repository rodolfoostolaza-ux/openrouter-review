# Spec: openrouter-review skill

**Fecha:** 2026-05-24  
**Estado:** Aprobado por usuario  
**Alcance:** Skill global de revisión + implementación vía DeepSeek (OpenRouter) + agentes Claude

---

## Propósito

Skill de Claude Code que actúa como revisor de código y texto usando DeepSeek vía OpenRouter, con fallback automático de modelo gratuito a pagado, y capacidad de despachar agentes Claude para implementar los fixes encontrados.

Caso de uso principal: alternativa de revisión cuando Codex no está disponible o como herramienta de revisión standalone de bajo costo.

---

## Archivos

```
~/.claude/scripts/openrouter_review.py    ← cliente HTTP a OpenRouter (Python stdlib)
~/.claude/skills/openrouter-review/SKILL.md  ← skill global invocable con /openrouter-review
```

---

## Componentes

### 1. SKILL.md

Instrucciones para Claude que definen:
- Cómo detectar qué hay para revisar (git diff, archivos, texto)
- Cómo construir el prompt y escribirlo a archivo temporal
- Cómo invocar el script Python
- Cómo presentar los findings al usuario
- Cómo preguntar si implementar y cómo despachar subagentes

### 2. openrouter_review.py

Script Python que:
- Lee el prompt desde un archivo (arg `--prompt-file <ruta>`)
- Acepta `--mode review|rescue` para seleccionar el system prompt
- Llama a `deepseek/deepseek-r1:free` vía OpenRouter API
- Si recibe HTTP 429 o error de cuota → reintenta con `deepseek/deepseek-v4-pro`
- Lee `OPENROUTER_API_KEY` del entorno; si falta, imprime error claro y sale con código 1
- Imprime la respuesta como texto plano a stdout
- Usa solo `urllib.request` (stdlib Python 3, sin pip)

---

## Modelos

| Prioridad | Modelo | Slug OpenRouter | Costo |
|-----------|--------|-----------------|-------|
| 1 (base)  | DeepSeek R1 free | `deepseek/deepseek-r1:free` | $0 |
| 2 (fallback) | DeepSeek V4 Pro | `deepseek/deepseek-v4-pro` | $0.44/$0.87 por M tokens |

Trigger del fallback: HTTP 429 o cualquier error de cuota/rate-limit del modelo free.

---

## Detección de input

Claude detecta automáticamente qué revisar:

| Situación | Acción |
|-----------|--------|
| Hay cambios en git | Corre `git diff HEAD` y `git diff --cached` |
| Usuario especifica archivos | Lee esos archivos directamente |
| Ambos | Combina diff + archivos en el prompt |
| Sin diff y sin archivos | Pide al usuario qué revisar |

Tipos de archivos de texto soportados: `.md`, `.txt`, `.rst` y cualquier extensión que el usuario indique explícitamente.

---

## System prompts por modo

**review:**
> Eres un revisor experto. Analiza el siguiente contenido y reporta: (1) bugs o errores lógicos, (2) problemas de seguridad, (3) mejoras de claridad o estructura. Sé específico: señala línea o sección, describe el problema, sugiere el fix exacto.

**rescue** (cuando el usuario quiere implementar):
> Eres un ingeniero senior. Para cada problema encontrado, propón el fix exacto con el código o texto corregido completo. Formato: problema → fix listo para aplicar.

---

## Flujo completo

```
Invocación: /openrouter-review [archivos opcionales]
        ↓
1. Claude detecta input: git diff + archivos especificados
        ↓
2. Claude construye prompt → escribe a %TEMP%\or_review_prompt.txt
        ↓
3. python openrouter_review.py --prompt-file <tmp> --mode review
   → deepseek/deepseek-r1:free
   → (si 429) deepseek/deepseek-v4-pro
        ↓
4. Claude presenta findings estructurados:
   · Código: bugs / seguridad / mejoras
   · Texto/notas: claridad / consistencia / argumentos débiles
        ↓
5. Claude pregunta: "¿Implemento algún fix? (todos / cuáles / ninguno)"
        ↓
6. Si usuario confirma:
   → Claude despacha subagentes paralelos (dispatching-parallel-agents)
   → Un agente por fix independiente, con Edit/Write/Bash
   → Claude reporta resultados al terminar
```

---

## Manejo de errores

| Error | Comportamiento |
|-------|---------------|
| `OPENROUTER_API_KEY` no existe | Script sale con código 1 + mensaje claro al usuario |
| R1:free devuelve 429 | Reintentar automáticamente con V4 Pro, sin interrumpir al usuario |
| V4 Pro también falla | Imprimir error descriptivo, no continuar |
| Sin git repo en el directorio | Omitir diff, usar solo archivos explícitos |
| Sin diff y sin archivos especificados | Preguntar al usuario qué revisar antes de continuar |

---

## Instalación

```
# 1. Copiar script Python
~/.claude/scripts/openrouter_review.py

# 2. Crear carpeta del skill
~/.claude/skills/openrouter-review/

# 3. Copiar skill
~/.claude/skills/openrouter-review/SKILL.md

# 4. Variable de entorno (ya disponible en sesión de Rodolfo)
OPENROUTER_API_KEY=<tu key>
```

No requiere pip, no requiere Node.js, no requiere configuración adicional.

---

## Criterios de éxito

1. `/openrouter-review` invocado sin argumentos detecta el diff actual y llama a DeepSeek R1 free
2. Si R1:free devuelve 429, el fallback a V4 Pro ocurre de forma transparente
3. Los findings se presentan estructurados (no texto crudo del modelo)
4. El usuario puede confirmar implementación; Claude despacha agentes que aplican los fixes
5. Funciona con archivos de notas `.md` además de código
6. `OPENROUTER_API_KEY` ausente produce un error claro, no un crash críptico

---

## Fuera de alcance

- Publicación en GitHub (extra, no requerido para MVP)
- Soporte para otros proveedores (solo OpenRouter)
- Streaming de la respuesta de DeepSeek
- Historial de reviews anteriores
