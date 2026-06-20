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

# Verificado contra https://openrouter.ai/api/v1/models el 2026-06-19.
# Si un modelo desaparece del catalogo, la escalera salta al siguiente.
# Orden: especialista de codigo y MoE agiles primero (en MoE manda la velocidad los params
# ACTIVOS, no el total); densos lentos abajo o fuera.
FREE_MODELS = [
    "qwen/qwen3-coder:free",                    # 480B MoE (35B act): especialista codigo, agil
    "openai/gpt-oss-120b:free",                 # 120B MoE (~5B act): alto razonamiento, agil
    "nvidia/nemotron-3-super-120b-a12b:free",   # 120B MoE (12B act)
    "nvidia/nemotron-3-ultra-550b-a55b:free",   # 550B MoE (55B act): refuerzo pesado
    "meta-llama/llama-3.3-70b-instruct:free",   # 70B denso
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
# Cuarentena: un modelo que se porta mal (JSON basura, HTTP 4xx) se saca de la escalera por
# un cooldown y se registra en el log. Se reintenta solo cuando expira (auto-sanacion). Los
# 429 (rate limit) NO mandan a cuarentena: el modelo esta bien, solo lleno hoy.
QUARANTINE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "openrouter_quarantine.json")
QUARANTINE_COOLDOWN_SECONDS = 24 * 3600       # HTTP 4xx: modelo roto/dado de baja, fuera 1 dia
QUARANTINE_SOFT_COOLDOWN_SECONDS = 2 * 3600   # JSON malo: suele ser transitorio (truncamiento
                                              # por max_tokens, mala suerte), fuera solo un rato
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "openrouter_review.log")
MIN_CONTEXT = 32000          # un revisor con < 32K de contexto no sirve para diffs reales
MAX_FREE_LADDER = 10         # cuantos modelos gratis conservar en la escalera
RETRY_AFTER_MAX = 30         # si el 429 pide esperar mas que esto, saltar de modelo
SMALL_PARAMS_B = 50          # < esto = "modelito": va al fondo, no compite con los grandes
DENSE_SLOW_B = 150           # un modelo DENSO (sin params activos declarados) >= esto es muy
                             # lento en el tier gratis y se excluye (ej. hermes-405B denso)
# Familias conocidas-buenas-para-codigo (porton: solo estas entran a la escalera). El orden
# prioriza al especialista de codigo y a los modelos agiles; la familia ya ordena
# coder > gpt-oss > nemotron > ... Sin un LLM evaluando "para que sirve cada uno".
PREFERRED_FAMILIES = ["coder", "deepseek", "gpt-oss", "nemotron", "qwen3", "qwen",
                      "llama", "mistral", "gemma"]
# Modelos que pasan el filtro de familia por coincidencia de substring pero NO son
# revisores de codigo (moderacion, vision-only, audio/musica, embeddings): se excluyen.
EXCLUDE_MARKERS = ["content-safety", "guardrail", "moderation", "uncensored", "venice",
                   "lyria", "whisper", "embed", "rerank", "-tts", "-vl", "-omni"]

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


def _params_b(model):
    """Params TOTALES en miles de millones, inferidos del id y el name (ej. '...-550b-a55b'
    -> 550, 'Qwen3 Coder 480B' -> 480). El \\b inicial evita capturar los params ACTIVOS
    ('a55b': la 'a' pega al numero, no hay frontera de palabra, asi que '55b' NO matchea).
    Toma el mayor de los totales. 0.0 si no se detecta. Sin red ni LLM: puro parseo."""
    text = ((model.get("id") or "") + " " + (model.get("name") or "")).lower()
    return max((float(n) for n in re.findall(r"\b(\d+(?:\.\d+)?)\s*b\b", text)), default=0.0)


def _active_b(model):
    """Params ACTIVOS declarados (patron 'aNNb', ej. 'a35b' -> 35) en id/name; None si no
    los declara. En un MoE los activos mandan la velocidad; un modelo sin 'aNNb' se trata
    como DENSO (enciende todos sus params -> lento si es grande)."""
    text = ((model.get("id") or "") + " " + (model.get("name") or "")).lower()
    vals = [float(n) for n in re.findall(r"a(\d+(?:\.\d+)?)\s*b\b", text)]
    return max(vals) if vals else None


def rank_free_models(catalog):
    """Del catalogo de /api/v1/models, los ids GRATIS utiles para revisar codigo, ordenados.
    Funcion PURA (no toca red): recibe el dict ya parseado y aplica la heuristica.

    Orden buscado: especialista de codigo y modelos agiles primero; modelitos al fondo;
    gigantes DENSOS (lentos) fuera. Concretamente:
      - Gratis (id ':free' o pricing prompt+completion == 0) con contexto >= MIN_CONTEXT.
      - Excluye no-revisores (EXCLUDE_MARKERS) y familias desconocidas.
      - Excluye densos gigantes: sin 'aNNb' declarado y total >= DENSE_SLOW_B (ej. hermes-405B
        denso, que enciende sus 405B completos -> lentisimo en el tier gratis).
      - Ordena por (modelo serio antes que modelito, familia preferida, mayor tamano, mayor
        contexto). El porton de familia ya prioriza coder > gpt-oss > nemotron > ...
      Tope MAX_FREE_LADDER."""
    data = catalog.get("data") if isinstance(catalog, dict) else None
    if not isinstance(data, list):
        return []
    cands = []
    for m in data:
        if not isinstance(m, dict):
            continue
        mid = m.get("id") or ""
        low = mid.lower()
        full = (mid + " " + (m.get("name") or "")).lower()
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
        if any(marker in full for marker in EXCLUDE_MARKERS):
            continue
        rank = next((i for i, fam in enumerate(PREFERRED_FAMILIES) if fam in low), None)
        if rank is None:        # familia desconocida: no es un revisor de codigo confiable
            continue
        total = _params_b(m)
        if _active_b(m) is None and total >= DENSE_SLOW_B:
            # gigante denso: demasiado lento para el tier gratis. Se avisa para que la
            # exclusion sea visible (y no un buen modelo descartado en silencio).
            log(f"excluido por denso gigante (lento): {mid} (~{total:.0f}B densos)")
            continue
        size_tier = 0 if total >= SMALL_PARAMS_B else 1   # modelitos al fondo de la escalera
        cands.append((size_tier, rank, -total, -ctx, mid))
    cands.sort()
    return [mid for _t, _r, _c, _x, mid in cands[:MAX_FREE_LADDER]]


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


# --- Cuarentena de modelos que se portan mal (2026-06-19) ---------------------
# Si un modelo escupe JSON basura o devuelve HTTP 4xx (esta roto, no solo saturado),
# se saca de la escalera por un cooldown y se anota en el log. Al expirar, se reintenta.
def _load_quarantine():
    """Dict {model_id: until_epoch} de modelos en cuarentena. {} si no hay archivo o
    esta corrupto (nunca revienta: una cuarentena perdida solo reintenta un modelo malo)."""
    try:
        with open(QUARANTINE_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _is_quarantined(model, quarantine, now=None):
    """True si el modelo tiene una cuarentena vigente (su cooldown aun no expira)."""
    until = quarantine.get(model)
    if not isinstance(until, (int, float)):
        return False
    return (now if now is not None else time.time()) < until


def _append_log(line):
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError as exc:
        log(f"no se pudo escribir el log ({exc})")


def quarantine_model(model, reason, cooldown=None):
    """Saca un modelo de la escalera por `cooldown` segundos (default el largo, 24h) y registra
    el motivo. Cooldown corto para fallas transitorias (JSON malo), largo para fallas duras
    (HTTP 4xx). Escritura atomica (tmp + os.replace): el JSON nunca queda a medias si dos
    procesos coinciden (en el peor caso se pierde una entrada, no se corrompe el archivo)."""
    cooldown = QUARANTINE_COOLDOWN_SECONDS if cooldown is None else cooldown
    until = time.time() + cooldown
    data = _load_quarantine()
    data[model] = until
    try:
        tmp = QUARANTINE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp, QUARANTINE_PATH)
    except OSError as exc:
        log(f"no se pudo escribir la cuarentena ({exc})")
    _append_log(f"{time.ctime()}  CUARENTENA  {model}  ({reason})  hasta {time.ctime(until)}")
    log(f"modelo en cuarentena {cooldown // 3600}h: {model} ({reason})")


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
                if 400 <= exc.code < 500 and exc.code != 429:
                    # 4xx (400/404/422...) = el modelo rechaza o ya no existe: esta roto,
                    # no solo saturado. A cuarentena. (429 es rate limit: NO se castiga.)
                    quarantine_model(model, f"HTTP {exc.code} {exc.reason}")
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
                # Cooldown CORTO: el JSON malo suele ser transitorio (truncamiento por
                # max_tokens, mala suerte), no un modelo roto. No bancarlo 24h por un tropiezo.
                quarantine_model(model, "no produce JSON valido (ni tras reintento correctivo)",
                                 cooldown=QUARANTINE_SOFT_COOLDOWN_SECONDS)
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
    # Saca de la escalera los modelos en cuarentena vigente (fallaron feo hace poco).
    quarantine = _load_quarantine()
    vigentes = [m for m in free_models if not _is_quarantined(m, quarantine)]
    if len(vigentes) < len(free_models):
        fuera = [m for m in free_models if m not in vigentes]
        log(f"en cuarentena (omitidos de la escalera): {', '.join(fuera)}")
    free_models = vigentes or free_models  # si TODO quedo en cuarentena, intentarlos igual
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
