#!/usr/bin/env bash
# Lokalen Worker starten (Mac/Linux). Beim ersten Start Server-URL + Token angeben:
#   ./start_worker.sh --server https://DEINE-WEBSITE --token DEIN_TOKEN
cd "$(dirname "$0")"
python3 worker.py "$@"
