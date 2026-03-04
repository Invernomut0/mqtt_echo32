# mqtt-echo32 — Agent Zero Extension

Riceve testo trascritto dall'**M5Stack Atom Echo** via MQTT, lo processa con Agent Zero e pubblica la risposta vocale.

## Architettura

**Daemon autonomo** (`mqtt_echo32_bridge.py`) avviato direttamente dall'installer a ogni boot del container, senza dipendere dall'hook `agent_init` di Agent Zero.

```
ESP32 → MQTT (agent0/echo32/stt)
           → mqtt_echo32_bridge.py (daemon)
               → Agent Zero HTTP API (localhost:80)
                   → MQTT (agent0/echo32/tts) → ESP32 parla
```

### File principali

| File | Ruolo |
|---|---|
| `mqtt_echo32_bridge.py` | Daemon MQTT standalone (lanciato dall'installer) |
| `install_agent0_extension.sh` | Installer idempotente — installa dipendenze, copia file, avvia daemon |
| `python/extensions/agent_init/_60_mqtt_echo32.py` | Hook Agent Zero (usato solo per interazione diretta con l'agent) |

## Configurazione secrets

Aggiungi in `/a0/usr/secrets.env`:

```env
MQTT_BROKER=10.0.0.50
MQTT_PORT=1883
MQTT_USER=your_mqtt_user
MQTT_PASS=your_mqtt_password

# URL API Agent Zero locale (default: http://localhost:80)
A0_API_URL=http://localhost:80

# API Key Agent Zero (opzionale)
# A0_API_KEY=
```

In alternativa puoi usare variabili d'ambiente con gli stessi nomi.

## Installazione nel tuo addon

Aggiungi nelle opzioni dell'addon Home Assistant:

```yaml
extension_repositories:
  - https://github.com/TUO_USER/mqtt-echo32
extensions_auto_install: true
extensions_auto_run_installers: true
```

## Topic MQTT

| Topic | Direzione | Contenuto |
|---|---|---|
| `agent0/echo32/stt` | ESP32 → Agent Zero | Testo trascritto |
| `agent0/echo32/tts` | Agent Zero → ESP32 | Risposta testuale |
| `agent0/echo32/status` | ESP32 → Agent Zero | online / offline |

## Log e debug

I log del daemon sono in `/a0/tmp/mqtt_echo32_bridge.log`:

```bash
# dentro il container
tail -f /a0/tmp/mqtt_echo32_bridge.log
```

Log attesi all'avvio:
```
[mqtt-echo32] INFO mqtt_echo32_bridge.py avviato (PID=...)
[mqtt-echo32] DEBUG Lettura /a0/usr/secrets.env
[mqtt-echo32] INFO Avvio bridge MQTT → broker 10.0.0.50:1883
[mqtt-echo32] INFO Connesso al broker. Subscribe agent0/echo32/stt ...
```

Ogni messaggio MQTT ricevuto:
```
[mqtt-echo32] DEBUG on_message topic='agent0/echo32/stt' qos=0 retain=0 len=12B payload='hello world'
[mqtt-echo32] INFO STT ricevuto: 'hello world'
[mqtt-echo32] INFO TTS pubblicato: 'La risposta di Agent Zero'
```

## Troubleshooting

- **Daemon non parte**: controlla che `MQTT_BROKER` sia impostato in secrets.env (il daemon non si avvia se il broker non è configurato)
- **`rc=4` in on_connect**: credenziali MQTT errate — verifica `MQTT_USER`/`MQTT_PASS`
- **`rc=5` in on_connect**: broker non autorizza la connessione
- **Nessuna risposta da Agent Zero**: verifica `A0_API_URL` — prova sia `/api/chat` che `/v1/chat/completions`
- **Risposta non arriva all'ESP32**: verifica che il YAML ESPHome abbia la sottoscrizione a `agent0/echo32/tts`
- **paho-mqtt / requests mancanti**: l'installer li installa automaticamente, ma puoi forzare con `pip install paho-mqtt requests`
