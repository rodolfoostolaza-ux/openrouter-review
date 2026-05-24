import os
import pathlib
import subprocess
import sys
import tempfile

SCRIPT = str(pathlib.Path(__file__).parent / "openrouter_review.py")


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


if __name__ == "__main__":
    test_missing_api_key()
    print("\nTodos los tests pasaron.")
