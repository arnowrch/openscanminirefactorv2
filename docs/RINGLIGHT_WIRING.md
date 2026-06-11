# Ringlight Installation & Verkabelung — OpenScan Mini

**Wichtig**: Du hast TWO Ringlights:
1. **Serienmäßiges Ringlight** — Um die Arducam IMX519 herum (bereits verbaut)
2. **Zusätzliches 12V Ringlight** — Externe Lichtquelle, 2000mA @ 12V (neu hinzufügen)

Dieses Dokument beschreibt die Installation des **zusätzlichen Ringlights**.

---

## 1. Physische Installation des 2. Ringlights

### 1.1 Platzierung & Montage

Das Zusatz-Ringlight sollte **oben auf dem Rotor-Arm** (der 180°-Achse) montiert werden, damit:
- Es mit der Kamera rotiert
- Zusätzliche Beleuchtung von oben/Seite bietet
- Das Objekt von mehreren Winkeln beleuchtet wird

**Montage-Optionen:**
```
Option A: Auf dem Kamera-Bracket
┌─────────────────────┐
│  Zusatz-Ringlight   │ ← Oben aufgesetzt
│                     │
│  ┌─────────────────┤
│  │ Arducam IMX519  │ ← Serienmäßiges Ringlight
│  └─────────────────┤
│                     │
└─────────────────────┘

Option B: Separate Lichthalterung
         (3D-gedruckt oder kaufen)
         Befestigt am Rotor-Arm, Abstand ~10cm zur Kamera
```

**Befestigung:**
- M3 Schrauben + Nylonnuts (wie beim Original)
- Oder: Doppelseitiges Klebeband (wenn nicht permanent)
- Oder: 3D-gedruckte Bracket (plans in `/docs/3d-prints/`)

---

## 2. Elektrische Schaltung

### 2.1 Komponenten-Liste

Für **1× zusätzliches 12V Ringlight (2000mA)**:

| Komponente | Anzahl | Beschreibung | Notizen |
|-----------|--------|-------------|---------|
| MOSFET | 1 | 2N7000 oder IRF520N | Gate-Pin zum GPIO, kann bis 200mA treiben |
| Resistor | 1 | 1kΩ 1/4W | Zwischen GPIO und MOSFET Gate (Schutz) |
| Diode | 1 | 1N4007 | Parallel zur Ringlight (Back-EMF Schutz) |
| Stecker | 1 | JST XH 2-pin | Für Ringlight-Anschluss (wie serienmäßig) |
| Draht | ~2m | 22 AWG (0,3mm²) | Für Verkabelung |
| Schrumpfschlauch | 1m | 3mm Durchmesser | Für Isolierung |

**Zuverlässiger Lieferant**: Conrad, AliExpress, oder lokaler Elektronikshop

### 2.2 Schaltung — Detailliert

```
RASPBERRY PI GPIO PINS
│
├─ GPIO 19 ────┬──────[1kΩ Resistor]──────┬──────→ MOSFET Gate
│              │                           │
│              │ (optional: Pulldown)      │
│              │                           │
│              └───[10kΩ Resistor to GND]  │
│
├─ GPIO GND ────────────────────────────────┼───→ MOSFET Source (GND)
│
├─ 12V Power Supply (+) ────────────────────┼───→ MOSFET Drain
│                                           │
│                                           └──→ [Ringlight LED (+)]
│
└─ 12V Power Supply (-) ───────────────────────→ [Ringlight LED (-)]
                         ↑
                    [1N4007 Diode]
                    (Cathode → GND,
                     Anode → Ringlight -)
```

### 2.3 Steckdiagramm (Fritzing-Style)

```
Raspberry Pi 4
┌───────────────────────────────────────┐
│ GPIO Header (40-pin)                  │
│                                       │
│  Pin 39 (GND) ───────────────────┐    │
│  Pin 28 (GPIO 17)                │    │
│  ...                             │    │
│  Pin 35 (GPIO 19) ────┐          │    │
│  ...                  │          │    │
│  12V Power (via HAT)  │          │    │
│                       │          │    │
└───────────────────────┼──────────┼────┘
                        │          │
                        ▼          ▼
                    ┌────────┐  ┌──────┐
                    │ 1kΩ    │  │ GND  │
                    │resistor│  │ Wire │
                    └────┬───┘  └──┬───┘
                         │        │
                         ▼        ▼
                    ┌─────────────────┐
                    │    MOSFET       │
                    │  IRF520N/2N7000│
                    │                 │
                    │  Gate ← 1k res  │
                    │  Source → GND   │
                    │  Drain → 12V    │
                    └────────┬────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │ 12V Ringlight   │
                    │   (2000mA)      │
                    │                 │
                    │  + ← from Drain │
                    │  - → GND (via   │
                    │       1N4007)   │
                    └─────────────────┘
```

---

## 3. Detaillierter Aufbau (Step-by-Step)

### Step 1: MOSFET vorbereiten

**Pinout (von oben/Beschriftung lesbar):**
```
    [Gate]
     │
     [Drain]
     │
     [Source]

IRF520N Pinout:
  Pin 1: Gate
  Pin 2: Drain
  Pin 3: Source
```

**Beschriften** (mit feinem Edding/Etikett):
- Gate: G
- Drain: D  
- Source: S

### Step 2: Widerstände & Diode löten

1. **1kΩ Resistor zwischen Gate und GPIO 19**:
   ```
   One end: 1kΩ anode → GPIO 19 pin header
   Other end: 1kΩ cathode → MOSFET Gate pin
   
   Löt-Punkte:
   - Resistor Leg 1 an GPIO 19 header
   - Resistor Leg 2 an MOSFET Gate pin
   - Schrumpfschlauch drüber
   ```

2. **1N4007 Diode (Back-EMF Schutz)**:
   ```
   Cathode (schwarzer Ring) → GND Wire
   Anode (rot/no markierung) → Ringlight LED (-)
   
   Diese Diode schützt den MOSFET vor Spannungsspitzen
   wenn die Ringlight aus- oder einschaltet
   ```

### Step 3: Verkabelung

**Kabel-Längen** (grob):
- GPIO 19 → MOSFET Gate: ~20cm
- MOSFET Source → GND: ~30cm
- MOSFET Drain → Ringlight (+): ~50cm (je nach physischer Montage)
- 12V Power (+) → MOSFET Drain: ~100cm (zum Power-Jack)
- 12V Power (-) → Ringlight GND: ~100cm

**Connector am Ringlight** (JST XH Standard):
```
       ┌─ Ringlight (+12V)
       │
[Pin2] ─[Pin1]
       │
       └─ Ringlight GND
```

Wenn dein Ringlight mit Stecker kommt:
- Red wire → Pin 1 (+12V)
- Black wire → Pin 2 (GND)

Wenn es direkt gelötet werden muss:
- Rot → MOSFET Drain
- Schwarz → Diode Cathode (und GND)

### Step 4: Testen (VOR dem Anschalten!)

**Durchgangsprüfung mit Multimeter:**
```
1. MOSFET Gate ↔ 1kΩ Resistor: 1kΩ Widerstand? ✓
2. MOSFET Source ↔ GND: ~0Ω (durchgehend)? ✓
3. 1N4007 Diode: Forward ~0,7V, Reverse = ∞? ✓
4. Ringlight LED: Ohmmeter zeigt sehr hohen Wert? ✓ (nicht kurzgeschlossen)
```

---

## 4. Anschluss an Raspberry Pi

### 4.1 GPIO-Erweiterung (HAT oder Direct GPIO)

**Option A: Direkt an GPIO Header** (Einfach, aber fragil)
```
Pi Pin 35: GPIO 19 ← 1kΩ Resistor ← MOSFET Gate
Pi Pin 39: GND ← MOSFET Source (und Diode Cathode)

12V Power: Extern vom 12V Netzteil (nicht vom Pi!)
```

**Option B: Über Custom PCB / Breakout** (Sauber, robust)
```
Mini-PCB mit:
- GPIO 19 Breakout
- GND Breakout
- 12V Power Breakout
- Alle Komponenten (Resistor, Diode, MOSFET) auf einer Platine
```

### 4.2 12V Power Supply

**WICHTIG**: Das 12V Netzteil muss separat sein!

```
12V Power Supply (2A+)
├─ (+) → MOSFET Drain → Ringlight (+)
└─ (-) → GND (gemeinsamer GND mit Pi!)

Gemeinsamer GND:
  Pi GND ═══ 12V Power GND ═══ MOSFET Source
     (alle drei müssen galvanisch verbunden sein!)
```

**Empfohlenes Netzteil:**
- 12V, **≥2A** (für 2000mA Ringlight + Verluste)
- Stabilisiert (kein billiges Noname-Zeug)
- Mit GND-Anschluss-Stecker

---

## 5. Software-Integration

### 5.1 Hardware-Config Update

In `hardware_greenshield.json`:
```json
"ringlight": {
  "type": "12V LED Ringlight (2x)",
  "power": "2000 mA @ 12V per channel",
  "control": "PWM via GPIO",
  "channels": [
    {
      "id": 1,
      "gpio_pin": 26,
      "pwm_capable": true,
      "pwm_frequency_hz": 1000,
      "circuit": "GPIO → MOSFET Gate → 12V Supply",
      "description": "Serial Ringlight (around camera)"
    },
    {
      "id": 2,
      "gpio_pin": 19,
      "pwm_capable": true,
      "pwm_frequency_hz": 1000,
      "circuit": "GPIO → MOSFET Gate → 12V Supply",
      "description": "Additional top-mounted Ringlight (new)"
    }
  ],
  ...
}
```

### 5.2 Python Control

```python
from gpiozero import PWMLED

# Zusätzliches Ringlight
ringlight_top = PWMLED(pin=19, frequency=1000)

# Helligkeit einstellen (0.0 = aus, 1.0 = voll)
ringlight_top.value = 0.8  # 80% Helligkeit

# Pulse-Effekt (optional)
ringlight_top.pulse()

# Ausschalten
ringlight_top.off()
```

---

## 6. Sicherheit & Fehlersuche

### 6.1 Sicherheitschecks

- [ ] **GND überall verbunden?** Pi GND = 12V Supply GND
- [ ] **Diode richtig gepolt?** Cathode (Ring) → GND, Anode → Ringlight (-)
- [ ] **Keine Kurzschlüsse?** Mit Multimeter durchmessen (besonders 12V / GND)
- [ ] **1N4007 nicht vergessen?** Ohne sie: MOSFET zerstört beim Ausschalten
- [ ] **1kΩ Resistor richtig?** Zu niedrig = GPIO kann durchbrennen
- [ ] **MOSFET richtig gepolt?** Gate ≠ Drain ≠ Source

### 6.2 Troubleshooting

| Problem | Ursache | Lösung |
|---------|---------|--------|
| Ringlight leuchtet nicht | GPIO nicht richtig verdrahtet | Durchgangsprüfung mit Multimeter |
| Ringlight leuchtet aber PWM funktioniert nicht | gpiozero nicht installiert oder falsche GPIO | `sudo apt install python3-gpiozero`, Pin überprüfen |
| Ringlight flackert | Schlechter GND-Kontakt | GND-Lötstelle überprüfen & nachbessern |
| MOSFET wird heiß | Zu hohe Last oder defektes MOSFET | Stromaufnahme messen, MOSFET ggf. wechseln |
| "Permission denied" beim GPIO-Zugriff | Nutzer nicht in `gpio` Gruppe | `sudo usermod -a -G gpio pi` |

### 6.3 Test-Befehle (auf der Pi)

```bash
# 1. Testen, ob GPIO 19 funktioniert
python3 << 'EOF'
from gpiozero import LED
led = LED(19)
led.on()
print("LED sollte jetzt an sein")
import time
time.sleep(2)
led.off()
print("LED aus")
EOF

# 2. PWM testen
python3 << 'EOF'
from gpiozero import PWMLED
pwm = PWMLED(19, frequency=1000)
pwm.pulse()  # Pulsieren lassen
import time
time.sleep(5)
pwm.off()
EOF

# 3. GPIO-Zugriff testen
gpioinfo gpiochip0 | grep -A 2 "GPIO 19"
```

---

## 7. Finales Setup-Checklist

- [ ] **Hardware bestellt**: MOSFET, 1kΩ Resistor, 1N4007 Diode, JST Stecker, Draht
- [ ] **Ringlight vorbereitet**: Auf dem Kamera-Arm/Bracket montiert, bereit zum Löten
- [ ] **Schaltung aufgebaut**: Alle Komponenten gelötet, Isolierung mit Schrumpfschlauch
- [ ] **Durchgangsprüfung**: Alle Lötstellen OK, keine Kurzschlüsse
- [ ] **GND-Verbindung**: Pi GND = 12V Supply GND (kritisch!)
- [ ] **12V Netzteil**: Separat vom Pi, ≥2A, stabilisiert
- [ ] **Config-Datei** aktualisiert: `hardware_greenshield.json` mit GPIO 19
- [ ] **gpiozero installiert**: `pip install gpiozero`
- [ ] **Software-Test**: LED-Blinken via Python

---

## 8. Nächste Schritte

Sobald das Ringlight verdrahtet ist:

1. **Durchgangsprüfung** durchführen
2. **LED-Test-Code** ausführen (siehe 6.3)
3. **In `ARCHITECTURE.md` dokumentieren** (optional)
4. **Phase 2 starten**: Motor Control + RinglightController Integration

---

**Status**: Anleitung bereit  
**Fragen?** Stell sie, bevor du lötest! Besser safe than sorry! 🔌

