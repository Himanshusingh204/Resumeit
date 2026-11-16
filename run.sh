#!/bin/sh
# Linux/macOS launcher. HOST / PORT come from the environment or .env (default 127.0.0.1:8000).
cd "$(dirname "$0")" || exit 1
[ -f .env ] || cp .env.example .env
exec "${PYTHON:-python3}" -m app
