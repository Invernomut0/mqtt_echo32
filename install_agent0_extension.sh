#!/usr/bin/env bash
# install_agent0_extension.sh — installer idempotente per mqtt-echo32
# Argomento $1 = root Agent Zero (default: /a0)

set -euo pipefail

A0_ROOT="${1:-/a0}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="[mqtt-echo32-installer]"

log()  { echo "$PREFIX $*"; }
fail() { echo "$PREFIX ERROR: $*" >&2; exit 1; }

log "Avvio installazione (A0_ROOT=$A0_ROOT)"

# ── 1. installa paho-mqtt se mancante ────────────────────────────────────────
if ! python3 -c "import paho.mqtt" 2>/dev/null; then
    log "Installazione paho-mqtt..."
    pip install --quiet paho-mqtt || fail "pip install paho-mqtt fallito"
    log "paho-mqtt installato"
else
    log "paho-mqtt già presente"
fi

# ── 2. copia i file dell'estensione ─────────────────────────────────────────
SRC="$SCRIPT_DIR/python/extensions"
DST="$A0_ROOT/python/extensions"

if [ ! -d "$SRC" ]; then
    fail "Directory sorgente non trovata: $SRC"
fi

mkdir -p "$DST"

find "$SRC" -name "*.py" | while IFS= read -r src_file; do
    rel="${src_file#$SRC/}"
    dst_file="$DST/$rel"
    dst_dir="$(dirname "$dst_file")"

    mkdir -p "$dst_dir"

    # copia solo se cambiato (idempotente)
    if [ ! -f "$dst_file" ] || ! cmp -s "$src_file" "$dst_file"; then
        cp "$src_file" "$dst_file"
        log "Copiato: $rel"
    else
        log "Invariato: $rel"
    fi
done

# ── 3. verifica secrets.env ──────────────────────────────────────────────────
SECRETS_FILE="$A0_ROOT/usr/secrets.env"

if [ ! -f "$SECRETS_FILE" ]; then
    log "Creazione secrets.env di esempio in $SECRETS_FILE"
    mkdir -p "$(dirname "$SECRETS_FILE")"
    cat > "$SECRETS_FILE" << 'EOF'
# Credenziali MQTT per il bridge Echo32
# Decommentare e compilare con i valori reali

# MQTT_BROKER=10.0.0.50
# MQTT_PORT=1883
# MQTT_USER=your_mqtt_user
# MQTT_PASS=your_mqtt_password
EOF
    log "secrets.env creato — configura le credenziali MQTT"
else
    # aggiunge le chiavi mancanti senza sovrascrivere
    for key in MQTT_BROKER MQTT_PORT MQTT_USER MQTT_PASS; do
        if ! grep -q "^${key}=" "$SECRETS_FILE" 2>/dev/null; then
            echo "# ${key}=" >> "$SECRETS_FILE"
            log "Aggiunta chiave commentata: $key"
        fi
    done
fi

log "Installazione completata"
