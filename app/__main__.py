"""Start the web app:  python -m app   (HOST / PORT from the environment or .env; default 127.0.0.1:8000)."""
import logging, os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
LOCAL = ("127.0.0.1", "localhost")  # the app has no login: never listen on anything else


def settings():
    host, port = os.getenv("HOST") or "127.0.0.1", os.getenv("PORT") or "8000"
    if host not in LOCAL:
        raise SystemExit(f"HOST={host} refused: Resumeit has no login, so it only listens on 127.0.0.1 / localhost")
    if not port.isdigit() or not 0 < int(port) < 65536:
        raise SystemExit(f"PORT={port} is not a valid port number")
    return host, int(port)


if __name__ == "__main__":
    import uvicorn
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    host, port = settings()
    uvicorn.run("app.main:app", host=host, port=port)
