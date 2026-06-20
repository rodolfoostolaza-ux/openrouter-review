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
def test_rank_prefiere_familias_de_codigo_y_filtra_contexto_bajo():
    catalog = {"data": [
        {"id": "qwen/qwen3-coder:free", "context_length": 1000000,
         "pricing": {"prompt": "0", "completion": "0"}},
        {"id": "some/tiny-model:free", "context_length": 8000,
         "pricing": {"prompt": "0", "completion": "0"}},
        {"id": "meta-llama/llama-3.3-70b-instruct:free", "context_length": 131000,
         "pricing": {"prompt": "0", "completion": "0"}},
        {"id": "openai/gpt-4o", "context_length": 128000,
         "pricing": {"prompt": "5", "completion": "15"}},
        {"id": "x/zero-priced-generic", "context_length": 64000,
         "pricing": {"prompt": "0", "completion": "0"}},
    ]}
    out = orr.rank_free_models(catalog)
    assert "openai/gpt-4o" not in out            # de pago: excluido
    assert "some/tiny-model:free" not in out     # contexto < 32K: excluido
    assert out[0] == "qwen/qwen3-coder:free"     # familia 'coder' va primero
    assert "meta-llama/llama-3.3-70b-instruct:free" in out
    assert "x/zero-priced-generic" in out        # pricing 0 = gratis aunque no sea :free
    print("PASS: test_rank_prefiere_familias_de_codigo_y_filtra_contexto_bajo")


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
