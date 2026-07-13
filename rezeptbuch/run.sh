#!/usr/bin/with-contenv bashio
# ==============================================================================
# Startet die Rezeptbuch-Web-App
# ==============================================================================
bashio::log.info "Rezeptbuch wird gestartet..."

exec python3 /app/app.py
