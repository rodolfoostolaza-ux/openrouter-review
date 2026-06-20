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


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"\n{len(fns)} tests pasaron.")
