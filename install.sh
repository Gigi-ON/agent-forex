#!/usr/bin/env bash
#
# Installateur — Agent de trading forex OANDA
# ---------------------------------------------------------------------------
# À lancer SUR LE VPS, idéalement sous un utilisateur dédié (pas root)
# — voir DEPLOIEMENT_HOSTINGER.md.
#
#   bash install.sh           # installation
#   bash install.sh --cron    # installation + planification des routines (cron)
#
# Affiche la progression de chaque étape et, en cas d'échec, le message
# d'erreur détaillé. sudo uniquement pour les paquets système ; jamais pip.
# Script idempotent (relançable sans casse), aucun secret écrit.
# ---------------------------------------------------------------------------
set -uo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

# Couleurs (désactivées hors terminal)
if [ -t 1 ]; then G="\033[0;32m"; Y="\033[0;33m"; R="\033[0;31m"; B="\033[1m"; N="\033[0m"; else G=""; Y=""; R=""; B=""; N=""; fi
info(){ echo -e "${G}▸${N} $1"; }
warn(){ echo -e "${Y}⚠${N} $1"; }
err(){  echo -e "${R}✗${N} $1" >&2; }

LOG="$(mktemp)"
STEP=0
TOTAL=4
cleanup(){ rm -f "$LOG"; }
trap cleanup EXIT
# Filet de sécurité : toute erreur non gérée affiche la ligne fautive
trap 'err "Erreur inattendue (ligne $LINENO). Voir le message ci-dessus."' ERR

# Exécute une étape : affiche un libellé + spinner, capture la sortie,
# et en cas d'échec affiche l'erreur détaillée puis arrête tout.
run_step(){
  STEP=$((STEP+1))
  local desc="$1" cmd="$2"
  printf "${B}[%d/%d]${N} %s " "$STEP" "$TOTAL" "$desc"
  if [ -t 1 ]; then
    bash -c "$cmd" >"$LOG" 2>&1 &
    local pid=$! spin='-\|/' i=0
    while kill -0 "$pid" 2>/dev/null; do i=$(((i+1)%4)); printf "\b%s" "${spin:$i:1}"; sleep 0.2; done
    wait "$pid"; local rc=$?
    [ $rc -eq 0 ] && printf "\b${G}OK${N}\n" || printf "\b${R}ÉCHEC${N}\n"
  else
    bash -c "$cmd" >"$LOG" 2>&1; local rc=$?
    [ $rc -eq 0 ] && echo "OK" || echo "ÉCHEC"
  fi
  if [ "$rc" -ne 0 ]; then
    echo ""
    err "Étape « $desc » échouée. Message d'erreur :"
    tail -n 20 "$LOG" | sed 's/^/    /'
    echo ""
    err "Corrigez le problème ci-dessus, puis relancez :  bash install.sh"
    exit 1
  fi
}

echo -e "${B}== Installateur Agent Forex ==${N}"
echo "Répertoire : $PROJECT_DIR"

# sudo seulement si pas root et si sudo existe
if [ "$(id -u)" -eq 0 ]; then
  SUDO=""; warn "Vous êtes root. Pour une app de trading, préférez un utilisateur dédié (voir le guide)."
elif command -v sudo >/dev/null 2>&1; then SUDO="sudo"
else SUDO=""; warn "Ni root ni sudo : l'installation des paquets système pourrait échouer."; fi

# Pré-requis : Python 3
if ! command -v python3 >/dev/null 2>&1; then
  err "python3 introuvable. Installez Python 3 puis relancez."
  exit 1
fi
info "Python détecté : $(python3 --version 2>&1)"

# [1/4] Paquets système (seulement si manquants)
if command -v apt-get >/dev/null 2>&1; then
  PKGS=()
  dpkg -s python3-venv >/dev/null 2>&1 || PKGS+=("python3-venv")
  command -v git >/dev/null 2>&1       || PKGS+=("git")
  if [ "${#PKGS[@]}" -gt 0 ]; then
    run_step "Installation des paquets : ${PKGS[*]}" \
      "$SUDO apt-get update -qq && $SUDO apt-get install -y ${PKGS[*]}"
  else
    run_step "Paquets système (déjà présents)" "true"
  fi
else
  run_step "Paquets système (apt-get absent — à vérifier manuellement)" \
    "echo 'apt-get introuvable : assurez-vous que python3-venv et git sont installés'"
fi

# [2/4] Environnement virtuel
if [ ! -d venv ]; then
  run_step "Création de l'environnement virtuel" "python3 -m venv venv"
else
  run_step "Environnement virtuel (déjà présent)" "true"
fi
# shellcheck disable=SC1091
source venv/bin/activate

# [3/4] Dépendances Python (dans le venv, jamais en sudo)
run_step "Installation des dépendances Python" \
  "pip install --upgrade pip && pip install -r requirements.txt"

# Fichier .env (jamais écrasé) + dossier data
mkdir -p data
if [ ! -f .env ]; then
  cp .env.example .env
  warn ".env créé depuis .env.example — ÉDITEZ-LE :  nano $PROJECT_DIR/.env"
else
  info ".env déjà présent (laissé intact)."
fi

# [4/4] Vérification hors-ligne (aucun réseau, aucun ordre)
run_step "Vérification de la logique OANDA (hors-ligne)" "python test_oanda_mock.py"

# Cron (optionnel)
install_cron(){
  local PY="$PROJECT_DIR/venv/bin/python"
  local WRAP="cd $PROJECT_DIR && set -a && . ./.env && set +a && $PY"
  local MARK="# agent-forex (généré par install.sh)"
  if crontab -l 2>/dev/null | grep -qF "$MARK"; then
    warn "Routines cron déjà installées — rien à faire."; return
  fi
  if { crontab -l 2>/dev/null || true; echo "$MARK"
    echo "0 0,7,13 * * 1-5 bash -lc \"$WRAP run_routine.py session_research\""
    echo "*/15 6-21 * * 1-5 bash -lc \"$WRAP run_routine.py execution_scan\""
    echo "*/5 6-21 * * 1-5 bash -lc \"$WRAP run_routine.py monitor\""
    echo "30 21 * * 1-5 bash -lc \"$WRAP run_routine.py end_of_day\""
    echo "35 21 * * 5 bash -lc \"$WRAP run_routine.py weekly_review\""
  } | crontab -; then
    info "Routines cron planifiées (voir : crontab -l)."
  else
    err "Échec de l'installation du cron (crontab indisponible ?)."
  fi
}
if [ "${1:-}" = "--cron" ]; then install_cron
else warn "Routines cron non installées. Pour planifier :  bash install.sh --cron"; fi

# Résumé
echo ""
echo -e "${B}== Installation terminée avec succès ==${N}"
echo "Prochaines étapes :"
echo "  1. Éditer .env  (token + account id OANDA, mode practice)"
echo "  2. Premier démarrage :  ./venv/bin/python oanda_bootstrap.py"
echo "  3. (optionnel) Planifier :  bash install.sh --cron"
echo ""
echo -e "${Y}Rappel :${N} on reste en practice tant que la campagne d'apprentissage"
echo "n'a pas rendu un verdict positif."
