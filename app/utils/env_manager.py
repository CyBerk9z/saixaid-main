from contextlib import contextmanager
import os

@contextmanager
def temporary_env(var: str, value: str):
    """
    一時的に環境変数を変更するコンテキストマネージャー
    """
    original = os.getenv(var)
    os.environ[var] = value
    try:
        yield
    finally:
        if original is None:
            os.environ.pop(var)
        else:
            os.environ[var] = original
            