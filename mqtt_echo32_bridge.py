#!/usr/bin/env python3
"""mqtt_echo32_bridge.py — daemon autonomo MQTT ↔ Agent Zero

Avviato direttamente dall'installer a ogni boot del container.
- Ascolta MQTT topic agent0/echo32/stt (testo trascritto dall'ESP32)
- Invia il testo all'API HTTP locale di Agent Zero
- Pubblica la risposta su agent0/echo32/tts

Secrets (in /a0/usr/secrets.env o variabili d'ambiente):
  MQTT_BROKER, MQTT_PORT, MQTT_USER, MQTT_PASS
  A0_API_URL   (default: http://localhost:80)
  A0_API_KEY   (opzionale, se Agent Zero richiede auth)
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time
import threading
from pathlib import Path

# ── logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG,
    format="[mqtt-echo32] %(levelname)s %(message)s",
    stream=sys.stdout,
    force=True,
)
log = logging.getLogger("mqtt-echo32")

# ── dipendenze ───────────────────────────────────────────────────────────────
try:
    import paho.mqtt.client as mqtt
    log.debug("paho-mqtt importato correttamente")
except ImportError:
    log.error("paho-mqtt non trovato — installa con: pip install paho-mqtt")
    sys.exit(1)

try:
    import requests
    log.debug("requests importato correttamente")
except ImportError:
    log.error("requests non trovato — installa con: pip install requests")
    sys.exit(1)

# ── topic ────────────────────────────────────────────────────────────────────
TOPIC_STT    = "agent0/echo32/stt"
TOPIC_TTS    = "agent0/echo32/tts"
TOPIC_STATUS = "agent0/echo32/status"


# ── secrets ──────────────────────────────────────────────────────────────────

def load_secrets() -> dict[str, str]:
    secrets: dict[str, str] = {}

    env_file = Path("/a0/usr/secrets.env")
    if env_file.exists():
        log.debug("Lettura %s", env_file)
        try:
            loaded: list[str] = []
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    # rimuove virgolette singole e doppie attorno al valore
                    secrets[k.strip()] = v.strip().strip('"\'')
                    loaded.append(k.strip())
            log.debug("secrets.env chiavi trovate: %s", loaded)
        except Exception as exc:
            log.error("Errore lettura secrets.env: %s", exc)
    else:
        log.debug("%s non trovato", env_file)

    for key in ("MQTT_BROKER", "MQTT_PORT", "MQTT_USER", "MQTT_PASS", "A0_API_URL", "A0_API_KEY"):
        if os.environ.get(key):
            secrets[key] = os.environ[key]
            log.debug("%s da variabile d'ambiente", key)

    log.debug(
        "Secrets risolti → BROKER=%s PORT=%s USER=%s PASS=%s A0_API_URL=%s",
        secrets.get("MQTT_BROKER", "—"),
        secrets.get("MQTT_PORT", "1883"),
        secrets.get("MQTT_USER", "—"),
        "***" if secrets.get("MQTT_PASS") else "—",
        secrets.get("A0_API_URL", "http://localhost:80"),
    )
    return secrets


# ── Agent Zero HTTP API ───────────────────────────────────────────────────────

def call_agent_zero(text: str, api_url: str, api_key: str) -> str:
    """
    Invia testo ad Agent Zero via HTTP e restituisce la risposta testuale.
    Supporta sia /api/chat (Agent Zero nativo) sia /v1/chat/completions (OpenAI compat).
    """
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    # Prova tutti i formati noti di Agent Zero
    # Formato 1: Agent Zero nativo (testo come "text", context come "context_id")
    # Formato 2: Agent Zero nativo alternativo (campo "message")
    # Formato 3: OpenAI compat
    endpoints = [
        (f"{api_url}/api/chat", {"text": text, "context_id": "echo32"}),
        (f"{api_url}/api/chat", {"message": text, "context_id": "echo32"}),
        (f"{api_url}/v1/chat/completions", {
            "model": "agent-zero",
            "messages": [{"role": "user", "content": text}],
        }),
    ]

    for endpoint, payload in endpoints:
        log.info("POST %s payload=%r", endpoint, payload)
        try:
            resp = requests.post(
                endpoint,
                json=payload,
                headers=headers,
                timeout=90,
            )
            log.info("Risposta HTTP %s: status=%d body=%r", endpoint, resp.status_code, resp.text[:1000])
            if resp.status_code == 200:
                try:
                    data = resp.json()
                except Exception:
                    # risposta non-JSON ma 200 OK → usala direttamente
                    log.info("Risposta non-JSON 200 OK, uso testo grezzo")
                    return resp.text.strip()
                # Agent Zero nativo — campo "response"
                if "response" in data:
                    return str(data["response"]).strip()
                # Agent Zero — potrebbe avere "message" o "content"
                if "message" in data:
                    return str(data["message"]).strip()
                if "content" in data:
                    return str(data["content"]).strip()
                # OpenAI compat
                if "choices" in data:
                    return data["choices"][0]["message"]["content"].strip()
                log.warning("Risposta inattesa da %s: %r", endpoint, data)
                return str(data)
            elif resp.status_code == 422:
                log.warning("HTTP 422 (Unprocessable Entity) da %s — payload non accettato: %r",
                            endpoint, resp.text[:500])
            else:
                log.warning("HTTP %d da %s: %r", resp.status_code, endpoint, resp.text[:300])
        except requests.exceptions.ConnectionError as exc:
            log.error("Connessione RIFIUTATA a %s: %s — Agent Zero è in ascolto su %s?", endpoint, exc, api_url)
            break  # se la connessione è rifiutata non ha senso riprovare altri endpoint
        except requests.exceptions.Timeout:
            log.error("Timeout (90s) su %s — Agent Zero troppo lento o bloccato", endpoint)
        except Exception as exc:
            log.error("Errore chiamata %s: %s", endpoint, exc)

    return ""


# ── Bridge ────────────────────────────────────────────────────────────────────

class Echo32Bridge:
    def __init__(self, secrets: dict[str, str]) -> None:
        self.broker   = secrets["MQTT_BROKER"]
        self.port     = int(secrets.get("MQTT_PORT", "1883"))
        self.username = secrets.get("MQTT_USER", "")
        self.password = secrets.get("MQTT_PASS", "")
        self.api_url  = secrets.get("A0_API_URL", "http://localhost:80").rstrip("/")
        self.api_key  = secrets.get("A0_API_KEY", "")

        self._client: mqtt.Client | None = None
        self._connected = False
        self._processing_lock = threading.Lock()

    def start(self) -> None:
        log.info("Avvio bridge MQTT → broker %s:%d", self.broker, self.port)

        self._client = mqtt.Client(client_id="agent0-echo32-bridge", clean_session=True)
        if self.username:
            self._client.username_pw_set(self.username, self.password)
            log.debug("Credenziali MQTT impostate user=%s", self.username)

        self._client.on_connect    = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message    = self._on_message

        log.debug("Tentativo connect() a %s:%d ...", self.broker, self.port)
        try:
            self._client.connect(self.broker, self.port, keepalive=60)
        except Exception as exc:
            log.error("Connessione MQTT fallita (%s:%d): %s", self.broker, self.port, exc)
            sys.exit(1)

        log.info("loop_forever() avviato")
        self._client.loop_forever()

    # ── callbacks ─────────────────────────────────────────────────────────────

    def _on_connect(self, client, userdata, flags, rc) -> None:  # noqa: ANN001
        log.debug("on_connect rc=%d flags=%s", rc, flags)
        if rc == 0:
            self._connected = True
            r1, _ = client.subscribe(TOPIC_STT)
            r2, _ = client.subscribe(TOPIC_STATUS)
            log.info("Connesso al broker. Subscribe %s rc=%d, %s rc=%d",
                     TOPIC_STT, r1, TOPIC_STATUS, r2)
        else:
            rc_msgs = {
                1: "protocollo non accettato", 2: "client ID rifiutato",
                3: "server non disponibile",   4: "credenziali non valide",
                5: "non autorizzato",
            }
            log.error("Errore connessione broker rc=%d: %s", rc, rc_msgs.get(rc, "sconosciuto"))

    def _on_disconnect(self, client, userdata, rc) -> None:  # noqa: ANN001
        self._connected = False
        log.debug("on_disconnect rc=%d (%s)", rc, "inaspettato" if rc != 0 else "normale")
        if rc != 0:
            log.warning("Disconnesso inaspettatamente (rc=%d), paho riconnette automaticamente...", rc)

    def _on_message(self, client, userdata, msg) -> None:  # noqa: ANN001
        topic   = msg.topic
        raw     = msg.payload
        payload = raw.decode("utf-8", errors="replace").strip()
        log.debug("on_message topic=%r qos=%d retain=%d len=%dB payload=%r",
                  topic, msg.qos, msg.retain, len(raw), payload)

        if topic == TOPIC_STATUS:
            log.info("ESP32 status: %s", payload)
            return

        if topic == TOPIC_STT:
            if not payload:
                log.debug("STT ricevuto ma payload vuoto, ignorato")
                return
            log.info("STT ricevuto: %r", payload)
            threading.Thread(
                target=self._handle_stt,
                args=(payload,),
                daemon=True,
            ).start()

    def _handle_stt(self, text: str) -> None:
        with self._processing_lock:
            log.info("Elaborazione STT: %r", text)
            response = call_agent_zero(text, self.api_url, self.api_key)
            if response:
                self._publish_tts(response)
                log.info("TTS pubblicato: %r", response)
            else:
                log.warning("Nessuna risposta da Agent Zero per: %r", text)

    def _publish_tts(self, text: str) -> None:
        if self._client and self._connected:
            self._client.publish(TOPIC_TTS, text, qos=0, retain=False)
        else:
            log.error("Client MQTT non connesso, risposta scartata")


# ── entrypoint ────────────────────────────────────────────────────────────────

def main() -> None:
    log.info("mqtt_echo32_bridge.py avviato (PID=%d)", os.getpid())

    secrets = load_secrets()

    if not secrets.get("MQTT_BROKER"):
        log.error("MQTT_BROKER non configurato — imposta in /a0/usr/secrets.env")
        sys.exit(1)

    bridge = Echo32Bridge(secrets)

    def _shutdown(sig, frame):  # noqa: ANN001
        log.info("Segnale %d ricevuto, shutdown...", sig)
        if bridge._client:
            bridge._client.disconnect()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    bridge.start()


if __name__ == "__main__":
    main()
