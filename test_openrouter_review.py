import os
import pathlib
import subprocess
import sys
import tempfile

SCRIPT = str(pathlib.Path(__file__).parent / "openrouter_review.py")

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import openrouter_review as orr  # noqa: E402


def test_missing_api_key():
    env_sin_key = {k: v for k, v in os.environ.items() if k != "OPENROUTER_API_KEY"}
    tmp = None
    try:
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
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)


# --- Logica PURA del auto-refresco (2026-06-19): sin red ni disco --------------
def test_rank_codigo_primero_excluye_densos_y_hunde_modelitos():
    catalog = {"data": [
        {"id": "qwen/qwen3-coder:free", "name": "Qwen3 Coder 480B A35B",
         "context_length": 1000000, "pricing": {"prompt": "0", "completion": "0"}},
        {"id": "nvidia/nemotron-3-super-120b-a12b:free", "name": "Nemotron 3 Super 120B A12B",
         "context_length": 1000000, "pricing": {"prompt": "0", "completion": "0"}},
        {"id": "nousresearch/hermes-3-llama-3.1-405b:free", "name": "Hermes 3 405B",
         "context_length": 131000, "pricing": {"prompt": "0", "completion": "0"}},
        {"id": "openai/gpt-oss-20b:free", "name": "gpt-oss-20b",
         "context_length": 131000, "pricing": {"prompt": "0", "completion": "0"}},
        {"id": "some/tiny-1b:free", "name": "Tiny 1B", "context_length": 8000,
         "pricing": {"prompt": "0", "completion": "0"}},
        {"id": "openai/gpt-4o", "name": "GPT-4o", "context_length": 128000,
         "pricing": {"prompt": "5", "completion": "15"}},
        {"id": "x/zero-priced-generic-200b", "name": "Generic 200B",
         "context_length": 64000, "pricing": {"prompt": "0", "completion": "0"}},
        {"id": "nvidia/nemotron-3.5-content-safety:free", "name": "Nemotron Content Safety 4B",
         "context_length": 128000, "pricing": {"prompt": "0", "completion": "0"}},
    ]}
    out = orr.rank_free_models(catalog)
    assert "openai/gpt-4o" not in out                   # de pago: excluido
    assert "some/tiny-1b:free" not in out               # contexto < 32K: excluido
    assert "x/zero-priced-generic-200b" not in out      # familia desconocida: no es revisor
    assert "nvidia/nemotron-3.5-content-safety:free" not in out  # marcador de exclusion
    assert "nousresearch/hermes-3-llama-3.1-405b:free" not in out  # denso gigante (405B): lento
    # especialista de codigo primero
    assert out[0] == "qwen/qwen3-coder:free"
    # el modelito (gpt-oss-20B) va DESPUES de un modelo serio (nemotron-super 120B)
    assert (out.index("nvidia/nemotron-3-super-120b-a12b:free")
            < out.index("openai/gpt-oss-20b:free"))
    print("PASS: test_rank_codigo_primero_excluye_densos_y_hunde_modelitos")


def test_params_b_ignora_activos():
    # _params_b debe leer el TOTAL (550), no los activos (55) de 'a55b'.
    assert orr._params_b({"id": "nvidia/nemotron-3-ultra-550b-a55b:free"}) == 550.0
    # un modelo que SOLO declara activos no infla su total con ese numero.
    assert orr._params_b({"id": "x/raro-a55b:free"}) == 0.0
    # _active_b si captura los activos.
    assert orr._active_b({"id": "nvidia/nemotron-3-ultra-550b-a55b:free"}) == 55.0
    assert orr._active_b({"id": "meta-llama/llama-3.3-70b-instruct:free"}) is None
    print("PASS: test_params_b_ignora_activos")


def test_quarantine_vigente_vs_expirada():
    ahora = 1_000_000.0
    q = {"a/malo:free": ahora + 3600, "b/viejo:free": ahora - 10, "c/basura:free": "nope"}
    assert orr._is_quarantined("a/malo:free", q, now=ahora) is True     # cooldown vigente
    assert orr._is_quarantined("b/viejo:free", q, now=ahora) is False   # ya expiro
    assert orr._is_quarantined("c/basura:free", q, now=ahora) is False  # valor corrupto
    assert orr._is_quarantined("d/no-listado:free", q, now=ahora) is False
    print("PASS: test_quarantine_vigente_vs_expirada")


def test_quarantine_cooldown_diferenciado():
    import shutil
    import time as _time
    d = tempfile.mkdtemp()
    orig_q, orig_l = orr.QUARANTINE_PATH, orr.LOG_PATH
    try:
        orr.QUARANTINE_PATH = os.path.join(d, "q.json")
        orr.LOG_PATH = os.path.join(d, "q.log")
        antes = _time.time()
        orr.quarantine_model("x/soft:free", "json malo", cooldown=orr.QUARANTINE_SOFT_COOLDOWN_SECONDS)
        orr.quarantine_model("y/hard:free", "http 404")   # default = cooldown largo (24h)
        q = orr._load_quarantine()
        assert 1.5 * 3600 < q["x/soft:free"] - antes < 2.5 * 3600   # ~2h, transitorio
        assert 23 * 3600 < q["y/hard:free"] - antes < 25 * 3600     # ~24h, modelo roto
        print("PASS: test_quarantine_cooldown_diferenciado")
    finally:
        orr.QUARANTINE_PATH, orr.LOG_PATH = orig_q, orig_l
        shutil.rmtree(d, ignore_errors=True)


def test_rank_respeta_tope_max_ladder():
    data = [{"id": f"qwen/model-{i}:free", "context_length": 100000 + i,
             "pricing": {"prompt": "0", "completion": "0"}} for i in range(20)]
    out = orr.rank_free_models({"data": data})
    assert len(out) == orr.MAX_FREE_LADDER
    print("PASS: test_rank_respeta_tope_max_ladder")


def test_rank_vacio_ante_basura():
    assert orr.rank_free_models({}) == []
    assert orr.rank_free_models({"data": "nope"}) == []
    assert orr.rank_free_models({"data": [None, 42, "x"]}) == []
    print("PASS: test_rank_vacio_ante_basura")


class _FakeExc:
    def __init__(self, headers):
        self.headers = headers


def test_retry_after_parsea_segundos_enteros():
    assert orr.retry_after_seconds(_FakeExc({"Retry-After": "12"})) == 12
    assert orr.retry_after_seconds(_FakeExc({"Retry-After": "  7 "})) == 7
    print("PASS: test_retry_after_parsea_segundos_enteros")


def test_retry_after_none_si_ausente_o_fecha_http():
    assert orr.retry_after_seconds(_FakeExc({})) is None
    assert orr.retry_after_seconds(
        _FakeExc({"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})) is None
    print("PASS: test_retry_after_none_si_ausente_o_fecha_http")


# --- Multi-proveedor + throttle de RPM (2026-07-04) ---------------------------
def test_resolve_provider():
    assert orr.resolve_provider("cerebras:gemma-4-31b") == (
        "cerebras", "gemma-4-31b", "CEREBRAS_API_KEY")
    assert orr.resolve_provider("gemini:gemini-flash-latest") == (
        "gemini", "gemini-flash-latest", "GEMINI_API_KEY")
    # sin prefijo = OpenRouter; el id se conserva tal cual (incluido el ':free' del final)
    assert orr.resolve_provider("qwen/qwen3-coder:free") == (
        "openrouter", "qwen/qwen3-coder:free", "OPENROUTER_API_KEY")
    assert orr.resolve_provider("deepseek/deepseek-v4-flash") == (
        "openrouter", "deepseek/deepseek-v4-flash", "OPENROUTER_API_KEY")
    print("PASS: test_resolve_provider")


def test_model_family_dedicados_distintos():
    # cada proveedor dedicado es su propia familia; en OpenRouter la familia es el
    # proveedor (antes del '/'), sin que el ':free' del final la contamine.
    assert orr.model_family("cerebras:gemma-4-31b") == "cerebras"
    assert orr.model_family("gemini:gemini-flash-latest") == "gemini"
    assert orr.model_family("qwen/qwen3-coder:free") == "qwen"
    assert orr.model_family("openai/gpt-oss-120b:free") == "openai"
    # gemma (Cerebras) y gemini son familias DISTINTAS -> el consenso los cuenta separados
    assert orr.model_family("cerebras:gemma-4-31b") != orr.model_family("gemini:gemini-flash-latest")
    print("PASS: test_model_family_dedicados_distintos")


def test_throttle_respeta_intervalo():
    import json as _json
    import shutil
    import time as _time
    d = tempfile.mkdtemp()
    orig_path = orr.RATELIMIT_PATH
    orig_int = orr.PROVIDER_MIN_INTERVAL.copy()
    try:
        orr.RATELIMIT_PATH = os.path.join(d, "rl.json")
        # sin estado previo: no espera
        t0 = _time.time(); orr._throttle("cerebras"); assert _time.time() - t0 < 1.0
        # ultima llamada = ahora + intervalo corto: debe dormir ~ ese intervalo
        orr.PROVIDER_MIN_INTERVAL["cerebras"] = 0.5
        _json.dump({"cerebras": _time.time()}, open(orr.RATELIMIT_PATH, "w"))
        t0 = _time.time(); orr._throttle("cerebras"); dt = _time.time() - t0
        assert 0.3 < dt < 1.5, f"esperado ~0.5s, fue {dt:.2f}"
        # proveedor sin intervalo (openrouter): no throttlea
        t0 = _time.time(); orr._throttle("openrouter"); assert _time.time() - t0 < 0.3
        print("PASS: test_throttle_respeta_intervalo")
    finally:
        orr.RATELIMIT_PATH = orig_path
        orr.PROVIDER_MIN_INTERVAL.clear(); orr.PROVIDER_MIN_INTERVAL.update(orig_int)
        shutil.rmtree(d, ignore_errors=True)


def test_truncado_no_cuarentena_y_salta_de_modelo():
    """Una respuesta truncada (input muy grande) NO debe cuarentenar el modelo: salta al
    siguiente sin castigar (blindaje 2026-07-04, evita cuarentenar gemma/gemini por un diff enorme)."""
    import shutil
    d = tempfile.mkdtemp()
    orig_q, orig_l, orig_cm = orr.QUARANTINE_PATH, orr.LOG_PATH, orr.call_model
    try:
        orr.QUARANTINE_PATH = os.path.join(d, "q.json")
        orr.LOG_PATH = os.path.join(d, "q.log")
        llamados = []

        def fake_call(prompt, model, mode):
            llamados.append(model)
            if model == "cerebras:gemma-4-31b":
                raise orr.TruncatedResponse("truncado por tamano")
            return '{"findings": []}'

        orr.call_model = fake_call
        res = orr.try_ladder(
            "x", ["cerebras:gemma-4-31b", "gemini:gemini-flash-latest"], "review", exclude=set())
        assert res is not None and res[0] == "gemini:gemini-flash-latest"  # salto al 2do
        assert llamados == ["cerebras:gemma-4-31b", "gemini:gemini-flash-latest"]  # gemma 1 vez, sin reintento
        assert "cerebras:gemma-4-31b" not in orr._load_quarantine()  # NO castigado por truncar
        print("PASS: test_truncado_no_cuarentena_y_salta_de_modelo")
    finally:
        orr.QUARANTINE_PATH, orr.LOG_PATH, orr.call_model = orig_q, orig_l, orig_cm
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"\n{len(fns)} tests pasaron.")
