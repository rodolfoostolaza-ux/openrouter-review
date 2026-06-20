#!/usr/bin/env python3
"""Revisor de codigo/texto via OpenRouter.

Endurecido 2026-06-10:
- Escalera de modelos GRATIS verificados contra /api/v1/models (no router generico).
- Retry con backoff en 429/5xx antes de saltar al siguiente modelo.
- Fallback automatico al pagado mas barato decente (deepseek-v4-flash).
- Anti-alucinacion: salida JSON con cita textual obligatoria; toda cita se
  verifica contra el input y los findings no verificables se descartan.
- Modo consensus: dos modelos distintos; findings "confirmado x2" vs "baja confianza".

Actualizado 2026-06-19:
- Escalera GRATIS auto-refrescada desde /api/v1/models (cache semanal); la lista
  clavada a mano (FREE_MODELS) queda como semilla y red de seguridad si la consulta falla.
- 429 mas inteligente: respeta Retry-After si es corto, si no salta de modelo de
  inmediato (los :free suelen tener tope DIARIO; esperar 8s no los desatura).
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

# Verificado contra https://openrouter.ai/api/v1/models el 2026-06-10.
# Si un modelo desaparece del catalogo, la escalera salta al siguiente.
FREE_MODELS = [
    "qwen/qwen3-coder:free",                    # 1M ctx, especialista en codigo
    "openai/gpt-oss-120b:free",                 # 131K ctx
    "nvidia/nemotron-3-super-120b-a12b:free",   # 1M ctx
    "meta-llama/llama-3.3-70b-instruct:free",   # 131K ctx
]
PAID_MODELS = [
    "deepseek/deepseek-v4-flash",   # $0.098/M in, $0.197/M out (2026-06-10), 1M ctx
    "qwen/qwen3-235b-a22b-2507",    # $0.09/M in, $0.10/M out, 262K ctx
]
API_URL = "https://openrouter.ai/api/v1/chat/completions"
RETRIES_PER_MODEL = 2
BACKOFF_SECONDS = 8
MIN_QUOTE_LEN = 8  # citas mas cortas no identifican nada y "verifican" por accidente

# --- Auto-refresco de la escalera GRATIS (2026-06-19) -------------------------
# El catalogo de OpenRouter cambia: los :free aparecen y desaparecen (la v1 murio
# porque deepseek-r1:free se borro). En vez de confiar solo en FREE_MODELS clavado
# arriba, se consulta /api/v1/models y se cachea una semana. FREE_MODELS sigue como
# semilla y red de seguridad si la consulta falla. Cero IA: la seleccion es heuristica.
MODELS_URL = "https://openrouter.ai/api/v1/models"
CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "openrouter_models_cache.json")
CACHE_TTL_SECONDS = 7 * 24 * 3600
MIN_CONTEXT = 32000          # un revisor con < 32K de contexto no sirve para diffs reales
MAX_FREE_LADDER = 6          # cuantos modelos gratis conservar en la escalera
RETRY_AFTER_MAX = 30         # si el 429 pide esperar mas que esto, saltar de modelo
# Familias conocidas-buenas-para-codigo, en orden de preferencia. El ranking se hace
# por (familia preferida, mayor contexto) — sin un LLM evaluando "para que sirve cada uno".
PREFERRED_FAMILIES = ["coder", "deepseek", "gpt-oss", "nemotron", "qwen3", "qwen",
                      "llama", "mistral", "gemma"]

JSON_RULES = (
    'Responde UNICAMENTE con JSON valido (sin markdown, sin texto fuera del JSON) con esta forma: '
    '{"findings": [{"severity": "alta|media|baja", '
    '"location": "archivo y linea o seccion", '
    '"quote": "fragmento TEXTUAL copiado caracter por caracter del contenido revisado", '
    '"problem": "descripcion concreta del problema", '
    '"fix": "fix especifico sugerido"}]} '
    "REGLAS DURAS: quote DEBE ser copia exacta de un fragmento del input (maximo 200 caracteres). "
    "Si no puedes citar el fragmento exacto, NO reportes ese finding. "
    "No inventes archivos, lineas, funciones ni codigo que no aparezca en el input. "
    'Si no hay problemas reales, responde {"findings": []}. No rellenes por compromiso.'
)

SYSTEM_PROMPTS = {
    "review": (
        "Eres un revisor experto de codigo y texto. Analiza el contenido y reporta: "
        "(1) bugs o errores logicos, (2) problemas de seguridad, "
        "(3) mejoras de claridad o estructura. " + JSON_RULES
    ),
    "rescue": (
        "Eres un ingeniero senior. Para cada problema encontrado propone el fix exacto "
        "listo para aplicar. " + JSON_RULES
    ),
}


def log(msg):
    print(f"[openrouter-review] {msg}", file=sys.stderr)


def call_model(prompt, model, mode, api_key):
    payload = json.dumps({
        "model": model,
        "temperature": 0.1,
        "max_tokens": 8000,
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
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError(f"API devolvio error: {data['error']}")
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"Respuesta inesperada de OpenRouter: {data}") from e
    if not content or not content.strip():
        raise RuntimeError("respuesta vacia")
    return content


def _is_zero(x):
    try:
        return float(x) == 0.0
    except (TypeError, ValueError):
        return False


def _http_get_json(url, api_key, timeout=30):
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {api_key}"}, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def rank_free_models(catalog):
    """Del catalogo de /api/v1/models, los ids GRATIS utiles, ordenados. Funcion PURA
    (no toca red): recibe el dict ya parseado y aplica la heuristica.

    Gratis = id termina en ':free' o pricing prompt+completion == 0. Filtra por contexto
    minimo y ordena por (familia preferida, mayor contexto). Tope MAX_FREE_LADDER."""
    data = catalog.get("data") if isinstance(catalog, dict) else None
    if not isinstance(data, list):
        return []
    cands = []
    for m in data:
        if not isinstance(m, dict):
            continue
        mid = m.get("id") or ""
        pricing = m.get("pricing") or {}
        es_gratis = mid.endswith(":free") or (
            _is_zero(pricing.get("prompt")) and _is_zero(pricing.get("completion")))
        if not es_gratis:
            continue
        try:
            ctx = int(m.get("context_length") or 0)
        except (TypeError, ValueError):
            ctx = 0
        if ctx < MIN_CONTEXT:
            continue
        low = mid.lower()
        rank = next((i for i, fam in enumerate(PREFERRED_FAMILIES) if fam in low),
                    len(PREFERRED_FAMILIES))
        cands.append((rank, -ctx, mid))
    cands.sort()
    return [mid for _r, _c, mid in cands[:MAX_FREE_LADDER]]


def load_free_models(api_key):
    """Escalera gratis fresca: usa el cache si tiene < 1 semana; si no, consulta
    /api/v1/models y lo refresca. Cae a FREE_MODELS (la semilla) si todo falla."""
    try:
        with open(CACHE_PATH, encoding="utf-8") as fh:
            cache = json.load(fh)
        if (time.time() - cache.get("fetched_epoch", 0) < CACHE_TTL_SECONDS
                and cache.get("free_models")):
            return cache["free_models"]
    except (OSError, ValueError):
        pass
    try:
        models = rank_free_models(_http_get_json(MODELS_URL, api_key))
    except Exception as exc:
        log(f"no se pudo refrescar el catalogo ({exc}); uso la escalera semilla")
        models = []
    if not models:
        return FREE_MODELS
    try:
        with open(CACHE_PATH, "w", encoding="utf-8") as fh:
            json.dump({"fetched_epoch": time.time(), "fetched_human": time.ctime(),
                       "free_models": models}, fh, indent=2)
    except OSError as exc:
        log(f"no se pudo escribir el cache de modelos ({exc})")
    log(f"escalera gratis refrescada: {', '.join(models)}")
    return models


def retry_after_seconds(exc):
    """Segundos a esperar segun el header Retry-After (si la API lo manda). None si no
    esta o viene en formato fecha HTTP (que ignoramos: mejor saltar de modelo)."""
    try:
        val = exc.headers.get("Retry-After")
    except Exception:
        return None
    if not val:
        return None
    val = val.strip()
    return int(val) if val.isdigit() else None


def try_ladder(prompt, models, mode, api_key, exclude):
    """Primer modelo de la escalera que responda: (model_id, texto). None si todos fallan."""
    for model in models:
        if model in exclude:
            continue
        for attempt in range(1, RETRIES_PER_MODEL + 1):
            try:
                return model, call_model(prompt, model, mode, api_key)
            except urllib.error.HTTPError as exc:
                log(f"{model}: HTTP {exc.code} {exc.reason} (intento {attempt}/{RETRIES_PER_MODEL})")
                if exc.code == 429:
                    # Rate limit. Los :free suelen tener tope DIARIO: dormir poco no los
                    # desatura. Respeta Retry-After solo si es corto; si no, salta de modelo
                    # de inmediato en vez de quemar el backoff esperando algo que no cede.
                    wait = retry_after_seconds(exc)
                    if wait is not None and wait <= RETRY_AFTER_MAX and attempt < RETRIES_PER_MODEL:
                        time.sleep(wait)
                        continue
                    break  # saturado a nivel cuenta: siguiente modelo ya
                if exc.code in (408, 500, 502, 503, 504) and attempt < RETRIES_PER_MODEL:
                    time.sleep(BACKOFF_SECONDS * attempt)  # transitorio: backoff y reintenta
                    continue
                break  # no retryable o agotado: siguiente modelo
            except Exception as exc:
                log(f"{model}: {exc} (intento {attempt}/{RETRIES_PER_MODEL})")
                if attempt < RETRIES_PER_MODEL:
                    time.sleep(BACKOFF_SECONDS)
                    continue
                break
    return None


def parse_findings(text):
    """Extrae findings del texto del modelo. None si no es JSON utilizable."""
    raw = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
    if fenced:
        raw = fenced.group(1).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        return None
    findings = data.get("findings") if isinstance(data, dict) else None
    if not isinstance(findings, list):
        return None
    out = []
    for f in findings:
        if not isinstance(f, dict):
            continue
        out.append({
            "severity": str(f.get("severity", "media")).lower().strip(),
            "location": str(f.get("location", "")).strip(),
            "quote": str(f.get("quote", "")),
            "problem": str(f.get("problem", "")).strip(),
            "fix": str(f.get("fix", "")).strip(),
        })
    return out


def normalize(s):
    return " ".join(s.split())


def verify_quotes(findings, source):
    """Separa findings con cita verificable en el input de los no verificables."""
    norm_source = normalize(source)
    ok, discarded = [], []
    for f in findings:
        q = normalize(f["quote"])
        if len(q) >= MIN_QUOTE_LEN and q in norm_source:
            ok.append(f)
        else:
            discarded.append(f)
    return ok, discarded


def get_verified_review(prompt, models, mode, api_key, exclude=frozenset()):
    """(model, findings_verificados, n_descartados) o None si ningun modelo sirvio."""
    tried = set(exclude)
    while True:
        result = try_ladder(prompt, models, mode, api_key, exclude=tried)
        if result is None:
            return None
        model, text = result
        findings = parse_findings(text)
        if findings is None:
            log(f"{model}: respuesta no es JSON valido; reintento correctivo")
            try:
                text2 = call_model(
                    prompt + "\n\nRECUERDA: responde SOLO el objeto JSON especificado, nada mas.",
                    model, mode, api_key,
                )
                findings = parse_findings(text2)
            except Exception as exc:
                log(f"{model}: reintento correctivo fallo: {exc}")
            if findings is None:
                log(f"{model}: descartado (no produce JSON); siguiente modelo")
                tried.add(model)
                continue
        ok, discarded = verify_quotes(findings, prompt)
        for d in discarded:
            log(f"{model}: finding DESCARTADO (cita no verificable): {d['problem'][:100]}")
        return model, ok, len(discarded)


def same_finding(a, b):
    qa, qb = normalize(a["quote"]), normalize(b["quote"])
    if qa and qb and (qa in qb or qb in qa):
        return True
    la, lb = normalize(a["location"]).lower(), normalize(b["location"]).lower()
    return bool(la) and la == lb


def merge_consensus(fa, fb):
    merged, used_b = [], set()
    for a in fa:
        match = next((i for i, b in enumerate(fb) if i not in used_b and same_finding(a, b)), None)
        a = dict(a)
        if match is not None:
            used_b.add(match)
            a["confidence"] = "confirmado x2"
        else:
            a["confidence"] = "1 modelo (baja confianza)"
        merged.append(a)
    for i, b in enumerate(fb):
        if i not in used_b:
            b = dict(b)
            b["confidence"] = "1 modelo (baja confianza)"
            merged.append(b)
    return merged


SEV_ORDER = {"alta": 0, "media": 1, "baja": 2}


def render(findings, models, discarded_total):
    lines = [f"## Review via OpenRouter -- modelo(s): {', '.join(models)}"]
    if discarded_total:
        lines.append(
            f"_{discarded_total} finding(s) descartados por cita no verificable (anti-alucinacion)._"
        )
    if not findings:
        lines.append("")
        lines.append("Sin findings verificables. El contenido pasa la revision.")
        return "\n".join(lines)
    findings = sorted(findings, key=lambda f: SEV_ORDER.get(f["severity"], 1))
    for i, f in enumerate(findings, 1):
        conf = f" -- {f['confidence']}" if f.get("confidence") else ""
        lines.append("")
        lines.append(f"### {i}. [{f['severity'].upper()}] {f['location']}{conf}")
        lines.append(f"- Cita: `{f['quote'][:200]}`")
        lines.append(f"- Problema: {f['problem']}")
        if f["fix"]:
            lines.append(f"- Fix: {f['fix']}")
    return "\n".join(lines)


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="Revisor via OpenRouter con verificacion de citas")
    parser.add_argument("--prompt-file", required=True, help="Archivo con el prompt completo")
    parser.add_argument("--mode", default="review", choices=["review", "rescue"])
    parser.add_argument(
        "--model",
        default="auto",
        choices=["auto", "free", "paid", "consensus"],
        help=(
            "auto=escalera gratis con fallback a pagado barato; free=solo gratis; "
            "paid=solo pagado; consensus=dos modelos distintos con etiquetas de confianza"
        ),
    )
    args = parser.parse_args()

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        log("ERROR: OPENROUTER_API_KEY no esta configurada en el entorno.")
        sys.exit(1)

    with open(args.prompt_file, encoding="utf-8") as f:
        prompt = f.read()

    # Escalera gratis fresca (cache semanal). En modo 'paid' no hace falta tocar el
    # catalogo, asi que se evita la llamada de red.
    free_models = FREE_MODELS if args.model == "paid" else load_free_models(api_key)
    ladders = {
        "free": free_models,
        "paid": PAID_MODELS,
        "auto": free_models + PAID_MODELS,
        "consensus": free_models + PAID_MODELS,
    }
    ladder = ladders[args.model]

    r1 = get_verified_review(prompt, ladder, args.mode, api_key)
    if r1 is None:
        log("ERROR: ningun modelo de la escalera respondio.")
        sys.exit(1)
    model1, f1, d1 = r1
    if model1 in PAID_MODELS:
        log(f"AVISO: se uso modelo PAGADO {model1} (los gratis fallaron)")

    if args.model != "consensus":
        print(render(f1, [model1], d1))
        return

    r2 = get_verified_review(prompt, ladder, args.mode, api_key, exclude={model1})
    if r2 is None:
        log("Solo un modelo respondio; consenso degradado a revision simple.")
        for f in f1:
            f["confidence"] = "1 modelo (sin consenso disponible)"
        print(render(f1, [model1], d1))
        return
    model2, f2, d2 = r2
    if model2 in PAID_MODELS:
        log(f"AVISO: se uso modelo PAGADO {model2} como segundo revisor")
    print(render(merge_consensus(f1, f2), [model1, model2], d1 + d2))


if __name__ == "__main__":
    main()
