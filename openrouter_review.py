#!/usr/bin/env python3
"""Revisor de código/texto via OpenRouter DeepSeek. Fallback automático R1:free → V4 Pro."""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

FREE_MODEL = "openrouter/free"
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
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as e:
            raise RuntimeError(f"Respuesta inesperada de OpenRouter: {e}") from e


def main() -> None:
    parser = argparse.ArgumentParser(description="Revisor via OpenRouter DeepSeek")
    parser.add_argument("--prompt-file", required=True, help="Archivo con el prompt completo")
    parser.add_argument("--mode", default="review", choices=["review", "rescue"])
    parser.add_argument(
        "--model",
        default="auto",
        choices=["auto", "free", "paid"],
        help="auto=gratis con fallback a pagado, free=solo R1:free, paid=solo V4 Pro",
    )
    args = parser.parse_args()

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print(
            "ERROR: OPENROUTER_API_KEY no está configurada en el entorno.\n"
            "Ejemplo: export OPENROUTER_API_KEY='tu-key'  (bash/Linux/macOS)\n"
            "      o  $env:OPENROUTER_API_KEY = 'tu-key'  (PowerShell)",
            file=sys.stderr,
        )
        sys.exit(1)

    with open(args.prompt_file, encoding="utf-8") as f:
        prompt = f.read()

    # Modelo pagado directo
    if args.model == "paid":
        try:
            print(call_model(prompt, PAID_MODEL, args.mode, api_key))
        except Exception as exc:
            print(f"ERROR: {PAID_MODEL} falló: {exc}", file=sys.stderr)
            sys.exit(1)
        return

    # Modelo gratuito directo (sin fallback)
    if args.model == "free":
        try:
            print(call_model(prompt, FREE_MODEL, args.mode, api_key))
        except Exception as exc:
            print(f"ERROR: {FREE_MODEL} falló: {exc}", file=sys.stderr)
            sys.exit(1)
        return

    # auto: intenta gratis, fallback a pagado
    try:
        print(call_model(prompt, FREE_MODEL, args.mode, api_key))
        return
    except urllib.error.HTTPError as exc:
        print(
            f"[openrouter-review] Error HTTP {exc.code} con {FREE_MODEL}: {exc.reason}.",
            file=sys.stderr,
        )
    except Exception as exc:
        print(f"[openrouter-review] Error con {FREE_MODEL}: {exc}", file=sys.stderr)
    print(f"[openrouter-review] Reintentando con {PAID_MODEL}…", file=sys.stderr)

    try:
        print(call_model(prompt, PAID_MODEL, args.mode, api_key))
    except Exception as exc:
        print(f"ERROR: Ambos modelos fallaron. Último error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
