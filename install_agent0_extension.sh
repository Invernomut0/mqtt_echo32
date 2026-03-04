#!/usr/bin/env bash
# install_agent0_extension.sh — installer idempotente per mqtt-echo32
# Argomento $1 = root Agent Zero (default: /a0)

set -euo pipefail

A0_ROOT="${1:-/a0}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="[mqtt-echo32-installer]"

log()  { echo "$PREFIX $*"; }
warn() { echo "$PREFIX[warn] $*"; }
fail() { echo "$PREFIX ERROR: $*" >&2; exit 1; }

log "Avvio installazione (A0_ROOT=$A0_ROOT)"

# ── 0. auto-repair: ripristina il repo se ci sono modifiche locali ────────────
if git -C "$SCRIPT_DIR" rev-parse --git-dir &>/dev/null; then
    DIRTY_FILES="$(git -C "$SCRIPT_DIR" status --porcelain 2>/dev/null || true)"
    if [ -n "$DIRTY_FILES" ]; then
        warn "Rilevate modifiche locali nel repo — ripristino automatico a HEAD:"
        echo "$DIRTY_FILES" | while read -r line; do warn "  $line"; done
        git -C "$SCRIPT_DIR" reset --hard HEAD 2>/dev/null || true
        git -C "$SCRIPT_DIR" clean -fd 2>/dev/null || true
        log "Repo ripristinato a HEAD"
    fi
fi

# ── 1. rileva interprete Python ──────────────────────────────────────────────
PYTHON=""
for candidate in /opt/venv-a0/bin/python3 python3 python; do
    if command -v "$candidate" &>/dev/null; then
        PYTHON="$candidate"
        break
    fi
done
[ -n "$PYTHON" ] || fail "Nessun interprete Python trovato"
log "Python: $PYTHON ($($PYTHON --version 2>&1))"

# ── 2. installa dipendenze ────────────────────────────────────────────────────
for pkg in paho-mqtt requests; do
    module="${pkg//-/_}"
    if ! "$PYTHON" -c "import $module" 2>/dev/null; then
        log "Installazione $pkg..."
        "$PYTHON" -m pip install --quiet "$pkg" || fail "pip install $pkg fallito"
        log "$pkg installato"
    else
        log "$pkg già presente"
    fi
done

# ── 3. copia bridge daemon ────────────────────────────────────────────────────
BRIDGE_SRC="$SCRIPT_DIR/mqtt_echo32_bridge.py"
BRIDGE_DST="$A0_ROOT/mqtt_echo32_bridge.py"

[ -f "$BRIDGE_SRC" ] || fail "Bridge script non trovato: $BRIDGE_SRC"

if [ ! -f "$BRIDGE_DST" ] || ! cmp -s "$BRIDGE_SRC" "$BRIDGE_DST"; then
    cp "$BRIDGE_SRC" "$BRIDGE_DST"
    log "Copiato: mqtt_echo32_bridge.py → $BRIDGE_DST"
else
    log "Invariato: mqtt_echo32_bridge.py"
fi

# ── 4. copia estensione agent_init ───────────────────────────────────────────
SRC="$SCRIPT_DIR/python/extensions"
DST="$A0_ROOT/python/extensions"

if [ ! -d "$SRC" ]; then
    warn "Directory estensioni non trovata: $SRC — skip"
else

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
fi  # end if [ -d "$SRC" ]

# ── 5. verifica secrets.env ──────────────────────────────────────────────────
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
    for key in MQTT_BROKER MQTT_PORT MQTT_USER MQTT_PASS A0_API_URL A0_API_KEY; do
        if ! grep -qE "^#?${key}=" "$SECRETS_FILE" 2>/dev/null; then
            echo "# ${key}=" >> "$SECRETS_FILE"
            log "Aggiunta chiave commentata: $key"
        fi
    done
fi

# ── 6. avvia / riavvia bridge daemon ─────────────────────────────────────────
TMP_DIR="$A0_ROOT/tmp"
PID_FILE="$TMP_DIR/mqtt_echo32_bridge.pid"
LOG_FILE="$TMP_DIR/mqtt_echo32_bridge.log"

mkdir -p "$TMP_DIR"

if [ -f "$PID_FILE" ]; then
    OLD_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
        log "Fermando istanza precedente (PID=$OLD_PID)..."
        kill "$OLD_PID" 2>/dev/null || true
        sleep 1
    fi
    rm -f "$PID_FILE"
fi

MQTT_BROKER_VAL=""
if grep -qE "^MQTT_BROKER=.+" "$SECRETS_FILE" 2>/dev/null; then
    MQTT_BROKER_VAL="$(grep -E "^MQTT_BROKER=" "$SECRETS_FILE" | tail -1 | cut -d= -f2-)"
fi
[ -z "$MQTT_BROKER_VAL" ] && MQTT_BROKER_VAL="${MQTT_BROKER:-}"

if [ -z "$MQTT_BROKER_VAL" ]; then
    warn "MQTT_BROKER non configurato — daemon NON avviato."
    warn "Imposta MQTT_BROKER in $SECRETS_FILE e riesegui l'installer."
    log "Installazione completata (daemon non avviato)"
    # cleanup repo anche in uscita anticipata
    if git -C "$SCRIPT_DIR" rev-parse --git-dir &>/dev/null; then
        git -C "$SCRIPT_DIR" reset --hard HEAD 2>/dev/null || true
        git -C "$SCRIPT_DIR" clean -fd 2>/dev/null || true
    fi
    exit 0
fi

log "Avvio bridge daemon ($PYTHON)..."
nohup "$PYTHON" "$BRIDGE_DST" >> "$LOG_FILE" 2>&1 &
DAEMON_PID=$!
echo "$DAEMON_PID" > "$PID_FILE"

sleep 1
if kill -0 "$DAEMON_PID" 2>/dev/null; then
    log "Bridge daemon avviato (PID=$DAEMON_PID, log=$LOG_FILE)"
else
    warn "Il daemon è terminato subito — controlla $LOG_FILE"
    tail -20 "$LOG_FILE" 2>/dev/null || true
fi

log "Installazione completata"
echo ""
echo "✅ mqtt-echo32 installato (startup-safe)"
echo "   MQTT_BROKER: $MQTT_BROKER_VAL"
echo "   Bridge daemon: PID=$DAEMON_PID  log=$LOG_FILE  pid=$PID_FILE"

# ── 7. cleanup finale: ripristina il repo per garantire future git pull ───────
if git -C "$SCRIPT_DIR" rev-parse --git-dir &>/dev/null; then
    git -C "$SCRIPT_DIR" reset --hard HEAD 2>/dev/null || true
    git -C "$SCRIPT_DIR" clean -fd 2>/dev/null || true
    log "Repo locale ripristinato a HEAD (futura git pull ok)"
fi
