#!/bin/bash
# Lance l'application Sourcing M&A.
# - Tue tout Streamlit du projet déjà actif
# - Applique le fix favicon (override du favicon statique Streamlit)
# - Lance Streamlit sur le port 8502
# - Ouvre automatiquement Safari
# Pour arrêter : fermez la fenêtre Terminal ou Ctrl+C.

cd "$(dirname "$0")"

echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  Sourcing M&A — Lancement de l'application"
echo "═══════════════════════════════════════════════════════════"
echo ""

# 1. Tuer un éventuel Streamlit du projet déjà actif
existing=$(lsof -ti :8502 2>/dev/null)
if [ -n "$existing" ]; then
  echo "  → Processus existant sur port 8502 (PID $existing). Arrêt..."
  kill -9 $existing 2>/dev/null
fi
pkill -f "streamlit run app.py" 2>/dev/null
sleep 1

# 2. Appliquer le fix favicon (override du favicon statique Streamlit)
STREAMLIT_STATIC=$(find .venv/lib -path '*/streamlit/static' -type d 2>/dev/null | head -1)
if [ -n "$STREAMLIT_STATIC" ] && [ -f "assets/favicon-v3.png" ]; then
  cp -f assets/favicon-v3.png "$STREAMLIT_STATIC/favicon.png"
  [ -f "assets/favicon.ico" ] && cp -f assets/favicon.ico "$STREAMLIT_STATIC/favicon.ico"
  echo "  → Favicon Sourcing M&A appliqué"
fi

# 3. Vider le cache pyc pour forcer le rechargement de tout le code
find . -type d -name "__pycache__" -not -path "./.venv/*" -exec rm -rf {} + 2>/dev/null

echo "  → Démarrage..."
echo "  URL : http://localhost:8502"
echo ""
echo "  Pour arrêter : fermez cette fenêtre ou Ctrl+C."
echo ""
echo "═══════════════════════════════════════════════════════════"
echo ""

# 4. Lancement Streamlit
.venv/bin/streamlit run app.py \
  --server.port 8502 \
  --browser.gatherUsageStats false
