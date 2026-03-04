"""MQTT Echo32 Bridge — Agent Zero Extension

Listens on MQTT topic agent0/echo32/stt for transcribed text from the
M5Stack Atom Echo ESP32, processes it through Agent Zero, and publishes
the response back to agent0/echo32/tts.

Required secrets (in Agent Zero secrets or /a0/usr/secrets.env):
  MQTT_BROKER   - broker IP (e.g. 10.0.0.50)
  MQTT_PORT     - broker port (default: 1883)
  MQTT_USER     - broker username
  MQTT_PASS     - broker password

Topics:
  SUB  agent0/echo32/stt     <- testo trascritto dall'ESP32
  PUB  agent0/echo32/tts     -> risposta testuale verso ESP32
  SUB  agent0/echo32/status  <- online/offline dell'ESP32
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from typing import Any

try:
    import paho.mqtt.client as mqtt
    _PAHO_AVAILABLE = True
except ImportError:
    _PAHO_AVAILABLE = False

try:
    from python.helpers.extension import Extension  # type: ignore[reportMissingImports]
except Exception:
    class Extension:  # type: ignore[override]
        def __init__(self, agent=None, **kwargs):
            self.agent = agent

# ── costanti topic ──────────────────────────────────────────────────────────
TOPIC_STT    = "agent0/echo32/stt"
TOPIC_TTS    = "agent0/echo32/tts"
TOPIC_STATUS = "agent0/echo32/status"

# ── stato globale (singleton) ───────────────────────────────────────────────
_bridge: "Echo32MqttBridge | None" = None
_bridge_lock = threading.Lock()


def _log(msg: str) -> None:
    print(f"[mqtt-echo32] {msg}", flush=True)


def _load_secrets() -> dict[str, str]:
    """
    Legge le credenziali da:
    1. variabili d'ambiente (priorità alta)
    2. /a0/usr/secrets.env  (formato KEY=VALUE)
    """
    secrets: dict[str, str] = {}

    env_file = "/a0/usr/secrets.env"
    if os.path.exists(env_file):
        try:
            with open(env_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, _, v = line.partition("=")
                        secrets[k.strip()] = v.strip()
        except Exception as exc:
            _log(f"Errore lettura secrets.env: {exc}")

    # le variabili d'ambiente sovrascrivono il file
    for key in ("MQTT_BROKER", "MQTT_PORT", "MQTT_USER", "MQTT_PASS"):
        if os.environ.get(key):
            secrets[key] = os.environ[key]

    return secrets


# ── bridge ──────────────────────────────────────────────────────────────────

class Echo32MqttBridge:
    """Bridge MQTT asincrono per Echo32 ↔ Agent Zero."""

    def __init__(self, agent) -> None:
        self.agent = agent
        self._client: "mqtt.Client | None" = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._connected = False
        self._thread: threading.Thread | None = None

    # ── setup ────────────────────────────────────────────────────────────────

    def start(self) -> None:
        if not _PAHO_AVAILABLE:
            _log("paho-mqtt non installato — esegui: pip install paho-mqtt")
            return

        secrets = _load_secrets()
        broker = secrets.get("MQTT_BROKER", "")
        if not broker:
            _log("MQTT_BROKER non configurato in secrets.env o variabili d'ambiente")
            return

        port     = int(secrets.get("MQTT_PORT", "1883"))
        username = secrets.get("MQTT_USER", "")
        password = secrets.get("MQTT_PASS", "")

        # cattura il loop asyncio del thread principale (Agent Zero)
        try:
            self._loop = asyncio.get_event_loop()
        except RuntimeError:
            self._loop = asyncio.new_event_loop()

        self._client = mqtt.Client(client_id="agent0-echo32-bridge", clean_session=True)
        if username:
            self._client.username_pw_set(username, password)

        self._client.on_connect    = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message    = self._on_message

        try:
            self._client.connect(broker, port, keepalive=60)
        except Exception as exc:
            _log(f"Connessione MQTT fallita ({broker}:{port}): {exc}")
            return

        self._thread = threading.Thread(
            target=self._client.loop_forever,
            name="mqtt-echo32",
            daemon=True,
        )
        self._thread.start()
        _log(f"Bridge avviato → broker {broker}:{port}")

    # ── callbacks MQTT ───────────────────────────────────────────────────────

    def _on_connect(self, client, userdata, flags, rc) -> None:
        if rc == 0:
            self._connected = True
            client.subscribe(TOPIC_STT)
            client.subscribe(TOPIC_STATUS)
            _log(f"Connesso al broker. Iscritto a: {TOPIC_STT}, {TOPIC_STATUS}")
        else:
            _log(f"Errore connessione broker (rc={rc})")

    def _on_disconnect(self, client, userdata, rc) -> None:
        self._connected = False
        if rc != 0:
            _log(f"Disconnesso inaspettatamente (rc={rc}), riconnessione automatica...")

    def _on_message(self, client, userdata, msg) -> None:
        topic   = msg.topic
        payload = msg.payload.decode("utf-8", errors="replace").strip()

        if topic == TOPIC_STATUS:
            _log(f"ESP32 status: {payload}")
            return

        if topic == TOPIC_STT and payload:
            _log(f"STT ricevuto: {payload!r}")
            self._dispatch(payload)

    # ── dispatching verso Agent Zero ─────────────────────────────────────────

    def _dispatch(self, text: str) -> None:
        """Schedula il processing nel loop asyncio di Agent Zero."""
        if self._loop is None:
            _log("Loop asyncio non disponibile, messaggio scartato")
            return

        asyncio.run_coroutine_threadsafe(
            self._process(text),
            self._loop,
        )

    async def _process(self, text: str) -> None:
        """Processa il testo con Agent Zero e pubblica la risposta."""
        _log(f"Elaborazione: {text!r}")
        try:
            response = await self._run_agent(text)
            if response:
                self._publish_tts(response)
        except Exception as exc:
            _log(f"Errore elaborazione: {exc}")

    async def _run_agent(self, text: str) -> str:
        """
        Invia il testo ad Agent Zero e restituisce la risposta testuale.
        Usa l'agent agganciato all'estensione.
        """
        if self.agent is None:
            _log("Agent non disponibile")
            return ""

        try:
            # Agent Zero: AgentContext.communicate() o agent.message_loop()
            # Il modo standard è creare un nuovo messaggio utente
            from python.helpers.print_style import PrintStyle  # type: ignore[reportMissingImports]
            from python.helpers.task import Task                # type: ignore[reportMissingImports]

            # crea un task voice e lo esegui
            response = await self.agent.communicate(message=text)
            return str(response).strip() if response else ""

        except ImportError:
            # fallback diretto se le classi non sono disponibili
            _log("Fallback: communicate() diretto")
            try:
                result = await self.agent.communicate(message=text)
                return str(result).strip() if result else ""
            except Exception as exc:
                _log(f"communicate() fallito: {exc}")
                return ""

    # ── publish ──────────────────────────────────────────────────────────────

    def _publish_tts(self, text: str) -> None:
        if self._client and self._connected:
            self._client.publish(TOPIC_TTS, text, qos=0, retain=False)
            _log(f"TTS pubblicato: {text!r}")
        else:
            _log("Client MQTT non connesso, risposta scartata")

    def publish(self, topic: str, payload: str) -> None:
        """Metodo pubblico per publish manuale da altre parti dell'estensione."""
        if self._client and self._connected:
            self._client.publish(topic, payload, qos=0, retain=False)


# ── Extension hook ───────────────────────────────────────────────────────────

class MqttEcho32Extension(Extension):
    """
    Hook agent_init — parte una sola volta sull'agent 0.
    """
    _started = False
    _lock    = threading.Lock()

    async def execute(self, **kwargs) -> Any:
        # solo agent 0, solo una volta
        if getattr(self.agent, "number", 0) != 0:
            return None

        with MqttEcho32Extension._lock:
            if MqttEcho32Extension._started:
                return None
            MqttEcho32Extension._started = True

        global _bridge
        _bridge = Echo32MqttBridge(agent=self.agent)

        # avvio in thread separato per non bloccare l'init
        threading.Thread(
            target=_bridge.start,
            name="mqtt-echo32-init",
            daemon=True,
        ).start()

        _log("Estensione Echo32 MQTT inizializzata")
        return None
