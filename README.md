# mqtt-echo32 — Agent Zero Extension

Riceve testo trascritto dall'**M5Stack Atom Echo** via MQTT, lo processa con Agent Zero e pubblica la risposta vocale.

## Flusso

```
ESP32 → MQTT (agent0/echo32/stt)
           → Agent Zero LLM
               → MQTT (agent0/echo32/tts) → ESP32 parla
```

## Configurazione secrets

Aggiungi in `/a0/usr/secrets.env`:

```env
MQTT_BROKER=10.0.0.50
MQTT_PORT=1883
MQTT_USER=your_mqtt_user
MQTT_PASS=your_mqtt_password
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

## Troubleshooting

- **paho-mqtt mancante**: l'installer lo installa automaticamente via pip
- **Bridge non parte**: controlla che `MQTT_BROKER` sia impostato in secrets.env
- **Risposta non arriva all'ESP32**: verifica che il YAML ESPHome abbia la sottoscrizione a `agent0/echo32/tts`
