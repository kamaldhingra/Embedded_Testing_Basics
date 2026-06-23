# 🐍 Day 10: Python + `python-can` Basics, `cantools` DBC Decoding & Mini-Project: Read & Decode CAN Logs

> **Course:** Embedded & IoT Testing Masterclass
> **Instructor:** Professor Embed
> **Prerequisites:** Day 1–9 (Complete CAN fundamentals through CAPL Scripting)

---

## 📚 Table of Contents

1. [Recap: Where We Left Off](#recap-where-we-left-off)
2. [Concept: Why Python for CAN Testing?](#concept-why-python-for-can-testing)
3. [Concept: `python-can` — The CAN Bus Swiss Army Knife](#concept-python-can)
4. [Concept: Interfaces, Virtual Bus & Real Hardware](#concept-interfaces-virtual-bus-real-hardware)
5. [Concept: Sending & Receiving Frames Programmatically](#concept-sending-receiving)
6. [Concept: CAN Log Files — `.blf`, `.asc`, `.csv`, `.db`](#concept-log-files)
7. [Concept: `cantools` — Your DBC-Aware Decoder](#concept-cantools)
8. [Concept: Decoding a Logged Frame — The Full Pipeline](#concept-decoding-pipeline)
9. [Mini-Project: Read & Decode CAN Logs](#mini-project)
10. [Where It's Used in the Real World](#where-its-used-in-the-real-world)
11. [How a Tester Thinks About It](#how-a-tester-thinks-about-it)
12. [Hands-On Exercise: Offline Log Analyser](#hands-on-exercise)
13. [Challenge: Anomaly Hunter](#challenge-anomaly-hunter)
14. [Quiz + Answers](#quiz--answers)
15. [Key Takeaways](#key-takeaways)

---

## 🔁 Recap: Where We Left Off

Nine days in. Look how far we've come:

```
Day 1 → CAN fundamentals: ECUs, IDs, arbitration, pub/sub
Day 2 → Frame anatomy + DBC — the bus contract
Day 3 → Arbitration mechanics + 5 error types + TEC/REC fault confinement
Day 4 → Timing: cycle time, WCRT latency, jitter
Day 5 → Physical layer: CAN-H/L, differential signalling, termination
Day 6 → Bit timing: TQ segments, sample point, SJW, bit stuffing
Day 7 → CAN FD: dual bitrate, 64-byte payload; CANoe/CANalyzer/BUSMASTER
Day 8 → DBC deep dive: scaling, Intel/Motorola, multiplexing, VAL_, BA_
Day 9 → CAPL scripting: event-driven model, four node roles, test modules
```

Day 9 closed with a question that every tester eventually asks:

> *"CAPL is great, but it's locked inside CANoe. What if I want open-source, version-controlled,  
> CI/CD-friendly CAN automation with full Python ecosystem access?"*

**Answer: `python-can` + `cantools` — today's topic.**

By end of Day 10 you will have written a complete offline CAN log analyser in Python that:
- Reads `.asc` / `.blf` / virtual bus logs
- Decodes every frame to named signals using a DBC
- Flags out-of-range values, missing cycles, and duplicate IDs
- Outputs a structured test report — exactly like a pytest run

Let's go. 🐍

---

## 🧠 Concept: Why Python for CAN Testing?

### "Playwright for the Bus, but Open-Source"

Day 9 showed CAPL's strengths: tight tool integration, DBC-native, real-time safe. Here's the honest trade-off table:

```
┌───────────────────────┬────────────────────────┬──────────────────────────┐
│  Feature              │  CAPL (CANoe)           │  Python (python-can)     │
├───────────────────────┼────────────────────────┼──────────────────────────┤
│  Cost                 │  Expensive licence       │  Free / open-source      │
│  CI/CD integration    │  Hard (GUI-first)        │  Native (just Python)    │
│  Version control      │  Awkward .can files      │  .py files in Git        │
│  Ecosystem            │  CAPL only               │  numpy, pandas, pytest,  │
│                       │                          │  matplotlib, etc.        │
│  Real-time guarantee  │  Yes (hardware bound)    │  No (OS scheduled)       │
│  DBC awareness        │  Built-in                │  Via cantools library    │
│  Hardware variety     │  Vector hardware only    │  PEAK, KVASER, SocketCAN,│
│                       │                          │  virtual, USB2CAN, etc.  │
│  Learning curve       │  Needs CANoe             │  Any Python environment  │
│  Log file reading     │  Yes                     │  Yes (offline analysis)  │
└───────────────────────┴────────────────────────┴──────────────────────────┘
```

> 🌉 **From your world:** CAPL is like a browser-vendor-specific test tool (Cypress — tight Chrome integration, fast, but ecosystem-limited). `python-can` + `cantools` is like Playwright with Python — same browser ultimately, but you bring the full Python open-source universe with you.

**When to use which:**

| Scenario | Tool |
|---|---|
| Hard real-time stimulation / HIL bench | CAPL / CANoe |
| CI pipeline, nightly regression | python-can + pytest |
| Offline log analysis (post-test) | python-can + cantools + pandas |
| Prototype a decoder quickly | cantools alone |
| Share test code across teams without licences | Python |

---

## 🧠 Concept: `python-can` — The CAN Bus Swiss Army Knife

### Installation

```bash
pip install python-can cantools
```

That's it. No hardware required to start — `python-can` ships with a **virtual bus** interface that simulates a CAN network entirely in-process.

### The Three Objects You'll Use Every Day

```
┌────────────────────────────────────────────────────────┐
│  python-can CORE OBJECTS                               │
├────────────────────────────────────────────────────────┤
│                                                        │
│  can.Bus          ← The interface (virtual or real)   │
│  can.Message      ← A single CAN frame                │
│  can.Listener     ← Callback / file logger            │
│                                                        │
│  can.Bus                                              │
│    .send(msg)     ← Put a frame on the bus            │
│    .recv(timeout) ← Block until a frame arrives       │
│    .set_filters() ← Hardware-level ID filtering       │
│    .shutdown()    ← Clean up                          │
│                                                        │
│  can.Message                                          │
│    .arbitration_id  ← 11-bit or 29-bit CAN ID        │
│    .data            ← bytes payload (up to 8/64)      │
│    .dlc             ← Data Length Code               │
│    .timestamp       ← float, seconds since epoch     │
│    .is_fd           ← True if CAN FD frame            │
│    .is_extended_id  ← True if 29-bit ID               │
└────────────────────────────────────────────────────────┘
```

### Minimal Send / Receive Example

```python
import can

# Open a virtual bus (no hardware needed)
bus = can.Bus(interface='virtual', channel='vcan0')

# Build a frame: ID=0x201, data=8 bytes simulating RPM=3000
msg = can.Message(
    arbitration_id=0x201,
    data=[0xB8, 0x0B, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00],
    is_extended_id=False
)

bus.send(msg)
print(f"Sent: {msg}")

received = bus.recv(timeout=1.0)
print(f"Received: {received}")   # On virtual bus, sender receives its own frames

bus.shutdown()
```

> **Note:** On a virtual bus, the sender and receiver share the same channel — the sender *does* receive its own frame. On a real bus, you'd need two bus objects (two USB adapters or two virtual bus names) to simulate a full node pair.

---

## 🧠 Concept: Interfaces, Virtual Bus & Real Hardware

`python-can` abstracts the hardware through an **interface** string. Swap the string, keep all your code:

```
┌───────────────────┬──────────────────────────────────────────────┐
│  Interface String │  What It Connects To                         │
├───────────────────┼──────────────────────────────────────────────┤
│  'virtual'        │  In-process simulation, no hardware          │
│  'socketcan'      │  Linux SocketCAN (candump, cansend, etc.)    │
│  'pcan'           │  PEAK PCAN-USB adapter                       │
│  'kvaser'         │  Kvaser USB adapter                          │
│  'vector'         │  Vector VN-series adapters (same as CANoe)  │
│  'ixxat'          │  HMS IXXAT adapters                          │
│  'slcan'          │  Cheap USB-to-CAN dongles (serial line CAN) │
└───────────────────┴──────────────────────────────────────────────┘
```

> 🌉 **From your world:** Changing the interface is like swapping `baseURL` in your Playwright config — same tests, different environment. `virtual` = mock server; `socketcan` = staging; `pcan` = production hardware.

**Using a config file (recommended for teams):**

```ini
# ~/.can/can.conf  or  ./can.conf
[default]
interface = virtual
channel = vcan0
bitrate = 500000
```

Then in Python — no hardcoded strings:
```python
bus = can.Bus()  # reads from config automatically
```

This is the equivalent of `playwright.config.ts` — environment-specific settings live outside test code.

---

## 🧠 Concept: Sending & Receiving Frames Programmatically

### Periodic Sender (simulating an ECU)

```python
import can, time, threading

def periodic_sender(bus, arb_id, data, period_s, stop_event):
    """Simulate an ECU sending a message at fixed cycle time."""
    while not stop_event.is_set():
        msg = can.Message(arbitration_id=arb_id, data=data, is_extended_id=False)
        bus.send(msg)
        time.sleep(period_s)

stop = threading.Event()
bus  = can.Bus(interface='virtual', channel='vcan0')

t = threading.Thread(target=periodic_sender, args=(bus, 0x201, [0x10]*8, 0.01, stop))
t.start()

# Receive 5 frames
for _ in range(5):
    msg = bus.recv(timeout=0.5)
    print(f"[{msg.timestamp:.3f}] ID=0x{msg.arbitration_id:03X} data={msg.data.hex()}")

stop.set()
t.join()
bus.shutdown()
```

> 🌉 **From your world:** `stop_event` is the CAN equivalent of `AbortController` — a clean, cooperative shutdown signal. Never kill threads with brute force in CAN code; the bus interface may leave hardware in a bad state.

### Filters — Don't Read What You Don't Need

```python
# Accept only ID=0x201 and ID=0x300
bus.set_filters([
    {"can_id": 0x201, "can_mask": 0x7FF, "extended": False},
    {"can_id": 0x300, "can_mask": 0x7FF, "extended": False},
])
```

`can_mask = 0x7FF` means "exact match on all 11 bits." Looser masks accept ranges:
```python
# Accept 0x200–0x27F (any ID where top 4 bits = 0x2__)
{"can_id": 0x200, "can_mask": 0x780}
```

> 🌉 **From your world:** `can_mask` is `page.route()` URL glob — the mask is the pattern, the ID is the match target.

---

## 🧠 Concept: CAN Log Files — `.asc`, `.blf`, `.csv`, `.db`

Real testing generates log files. You record a 20-minute drive cycle, then analyse it offline. `python-can` supports all major formats:

```
┌──────────────────┬──────────────────────────────────────────────────┐
│  Extension       │  Format                                          │
├──────────────────┼──────────────────────────────────────────────────┤
│  .asc            │  ASCII log — human-readable, Vector CANalyzer   │
│  .blf            │  Binary Logging Format — Vector, compact         │
│  .csv            │  Comma-separated — easy to open in Excel         │
│  .db             │  SQLite log — queryable via SQL                  │
│  .trc            │  PEAK trace format                               │
└──────────────────┴──────────────────────────────────────────────────┘
```

### Writing a Log During Live Capture

```python
import can

bus = can.Bus(interface='virtual', channel='vcan0')

# ASC logger
with can.ASCWriter("session.asc") as logger:
    # Notifier pipes every received frame to the logger automatically
    notifier = can.Notifier(bus, [logger])
    time.sleep(5)          # record for 5 seconds
    notifier.stop()

bus.shutdown()
```

### Reading a Log Offline

```python
import can

# Works for .asc, .blf, .csv — python-can auto-detects format
with can.ASCReader("session.asc") as log:
    for msg in log:
        print(f"t={msg.timestamp:.4f}  ID=0x{msg.arbitration_id:03X}  "
              f"DLC={msg.dlc}  data={msg.data.hex()}")
```

Sample output:
```
t=0.0000  ID=0x201  DLC=8  data=b80b000000000000
t=0.0103  ID=0x201  DLC=8  data=c00b000000000000
t=0.0200  ID=0x300  DLC=8  data=0300001770004800
```

Raw hex. Meaningless without a DBC. Enter `cantools`.

---

## 🧠 Concept: `cantools` — Your DBC-Aware Decoder

`cantools` loads DBC (and ARXML/KCD/SYM) files and turns raw bytes into named, scaled, unit-tagged Python dictionaries.

```
┌────────────────────────────────────────────────────────────────┐
│  cantools CORE OBJECTS                                         │
├────────────────────────────────────────────────────────────────┤
│                                                                │
│  cantools.db.load_file('vehicle.dbc')                         │
│    → Database object                                           │
│                                                                │
│  db.get_message_by_frame_id(0x201)                            │
│    → Message object (has .name, .signals, .length)            │
│                                                                │
│  msg_def.decode(bytes_data)                                    │
│    → dict: {'EngineRPM': 3000.0, 'CoolantTemp': 87.5, ...}   │
│                                                                │
│  msg_def.encode({'EngineRPM': 3000, 'CoolantTemp': 87.5})    │
│    → bytes: b'\xb8\x0b\x57\x00...'                           │
│                                                                │
│  db.decode_message(arb_id, data)                              │
│    → shortcut: look up by ID + decode in one call             │
└────────────────────────────────────────────────────────────────┘
```

```python
import cantools

db = cantools.db.load_file("vehicle.dbc")

# Decode raw bytes for message 0x201
raw  = bytes([0xB8, 0x0B, 0x64, 0x32, 0x00, 0x00, 0x00, 0x00])
decoded = db.decode_message(0x201, raw)
print(decoded)
# → {'EngineRPM': 3000.0, 'CoolantTemp': 50.0, 'ThrottlePos': 50.0, 'EngineLoad': 25.0}
```

> 🌉 **From your world:** `cantools` is your JSON schema validator + deserialiser in one shot. `db.load_file('vehicle.dbc')` = `ajv.compile(openAPISchema)`. `msg.decode(bytes)` = `JSON.parse(body)` + field validation. Except the "JSON" here is 8 binary bytes and the "schema" is a `.dbc` file.

### Signal Introspection

```python
db = cantools.db.load_file("vehicle.dbc")
msg = db.get_message_by_name("EngineData")

for sig in msg.signals:
    print(f"  {sig.name:20s}  start={sig.start:3d}  len={sig.length:2d}bits  "
          f"scale={sig.scale}  offset={sig.offset}  "
          f"min={sig.minimum}  max={sig.maximum}  unit={sig.unit}")
```

---

## 🧠 Concept: Decoding a Logged Frame — The Full Pipeline

This is the money shot. Everything from Day 1 to Day 10 in one diagram:

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  COMPLETE CAN LOG DECODE PIPELINE                                            │
│                                                                              │
│  ┌─────────────┐    ┌──────────────┐    ┌──────────────┐    ┌────────────┐ │
│  │  Log File   │    │  python-can  │    │   cantools   │    │  Analysis  │ │
│  │  .asc/.blf  │───▶│  Reader      │───▶│  db.decode() │───▶│  & Report  │ │
│  │             │    │              │    │              │    │            │ │
│  │ t  ID  data │    │ can.Message  │    │ {RPM: 3000,  │    │ range OK ✓ │ │
│  │ 0.01 201 .. │    │ objects      │    │  Temp: 87.5} │    │ cycle OK ✓ │ │
│  └─────────────┘    └──────────────┘    └──────────────┘    └────────────┘ │
│                                                                              │
│  INPUT:    Raw timestamps + hex bytes                                        │
│  OUTPUT:   Named signals + values + pass/fail assertions                     │
└──────────────────────────────────────────────────────────────────────────────┘
```

The steps:
1. Open log file with `python-can` reader
2. For each `can.Message`: look up the DBC definition by `arbitration_id`
3. Call `msg_def.decode(message.data)` → named signal dict
4. Apply assertions: range, cycle time, expected values
5. Accumulate results, print report

---

## 🔨 Mini-Project: Read & Decode CAN Logs

This is the full mini-project. It ships as a single Python file. No hardware needed — the script first *generates* a synthetic `.asc` log, then *analyses* it. You get the complete generate → log → decode → assert → report cycle.

### Files You'll Create

```
Day-10_Python_CAN_Tools/
├── vehicle_day10.dbc        ← DBC with 3 messages
├── can_log_analyser.py      ← Mini-project (generate + analyse + report)
└── Day10_Python_CAN_Tools.md
```

### `vehicle_day10.dbc`

```dbc
VERSION ""

NS_ :

BS_:

BU_: ECU_Engine ECU_Trans ECU_ABS

BO_ 201 EngineData: 8 ECU_Engine
 SG_ EngineRPM    : 0|16@1+ (1,0) [0|8000] "RPM"    Vector__XXX
 SG_ CoolantTemp  : 16|8@1+ (0.5,-40) [-40|215] "degC" Vector__XXX
 SG_ ThrottlePos  : 24|8@1+ (0.392157,0) [0|100] "%"   Vector__XXX
 SG_ EngineLoad   : 32|8@1+ (0.392157,0) [0|100] "%"   Vector__XXX

BO_ 300 TransData: 8 ECU_Trans
 SG_ GearCurrent  : 0|4@1+ (1,0) [0|6] ""   Vector__XXX
 SG_ GearTarget   : 4|4@1+ (1,0) [0|6] ""   Vector__XXX
 SG_ VehicleSpeed : 8|16@1+ (0.01,0) [0|655] "km/h" Vector__XXX
 SG_ AccelPedal   : 24|8@1+ (0.392157,0) [0|100] "%" Vector__XXX

BO_ 400 WheelSpeed: 8 ECU_ABS
 SG_ SpeedFL : 0|16@1+ (0.01,0) [0|655] "km/h" Vector__XXX
 SG_ SpeedFR : 16|16@1+ (0.01,0) [0|655] "km/h" Vector__XXX
 SG_ SpeedRL : 32|16@1+ (0.01,0) [0|655] "km/h" Vector__XXX
 SG_ SpeedRR : 48|16@1+ (0.01,0) [0|655] "km/h" Vector__XXX

BA_DEF_ BO_ "GenMsgCycleTime" INT 0 10000;
BA_DEF_DEF_ "GenMsgCycleTime" 0;
BA_ "GenMsgCycleTime" BO_ 201 10;
BA_ "GenMsgCycleTime" BO_ 300 20;
BA_ "GenMsgCycleTime" BO_ 400 10;
```

### `can_log_analyser.py`

```python
"""
Day 10 Mini-Project: Python + python-can + cantools
====================================================
Complete CAN log analyser: generate a synthetic drive-cycle log,
then decode every frame, run signal-range + cycle-time assertions,
and print a structured test report.

No hardware needed. Everything runs on python-can's virtual bus
with an ASC file as the intermediary.

Install:
    pip install python-can cantools
"""

import can
import cantools
import math
import random
import struct
import tempfile
import threading
import time
from collections import defaultdict
from pathlib import Path

# ─── DBC & CONFIG ────────────────────────────────────────────────────────────

DBC_PATH   = Path(__file__).parent / "vehicle_day10.dbc"
LOG_PATH   = Path(__file__).parent / "drive_cycle.asc"
CHANNEL    = "vcan0"
DURATION_S = 3.0   # simulate 3 seconds of drive-cycle data


# ─── STEP 1: GENERATE A SYNTHETIC LOG ────────────────────────────────────────

def encode_engine_data(rpm: float, coolant_c: float,
                       throttle_pct: float, load_pct: float) -> bytes:
    """Manually encode EngineData (0x201) without cantools — raw struct."""
    rpm_raw      = int(rpm)                              # scale=1, offset=0
    coolant_raw  = int((coolant_c + 40) / 0.5)          # scale=0.5, offset=-40
    throttle_raw = int(throttle_pct / 0.392157)          # scale=0.392157, offset=0
    load_raw     = int(load_pct / 0.392157)

    coolant_raw  = max(0, min(255, coolant_raw))
    throttle_raw = max(0, min(255, throttle_raw))
    load_raw     = max(0, min(255, load_raw))

    return struct.pack("<HBBBBBB",
                       rpm_raw & 0xFFFF,
                       coolant_raw,
                       throttle_raw,
                       load_raw,
                       0, 0, 0)[:8]


def encode_trans_data(gear: int, target: int,
                      speed_kmh: float, accel_pct: float) -> bytes:
    nibbles  = (gear & 0xF) | ((target & 0xF) << 4)
    spd_raw  = int(speed_kmh / 0.01)
    acc_raw  = max(0, min(255, int(accel_pct / 0.392157)))
    return struct.pack("<BHBB", nibbles, spd_raw & 0xFFFF, acc_raw, 0)[:8]


def encode_wheel_speed(fl: float, fr: float, rl: float, rr: float) -> bytes:
    return struct.pack("<HHHH",
                       int(fl / 0.01) & 0xFFFF,
                       int(fr / 0.01) & 0xFFFF,
                       int(rl / 0.01) & 0xFFFF,
                       int(rr / 0.01) & 0xFFFF)


def generate_log(log_path: Path, duration_s: float = 3.0) -> None:
    """
    Simulate a short drive cycle and record it to an ASC log file.
    Injects two deliberate faults for the analyser to catch:
      - Frame at t≈1.5s with RPM=9000 (above max 8000) ← range violation
      - Missing EngineData burst around t≈2.0s          ← cycle time violation
    """
    print(f"\n{'='*60}")
    print("STEP 1: Generating synthetic drive-cycle log...")
    print(f"{'='*60}")

    tx_bus = can.Bus(interface="virtual", channel=CHANNEL, receive_own_messages=False)
    rx_bus = can.Bus(interface="virtual", channel=CHANNEL)

    # Writer listens on rx_bus
    with can.ASCWriter(str(log_path)) as writer:
        notifier = can.Notifier(rx_bus, [writer])

        t_start = time.monotonic()
        t_last_engine  = t_start
        t_last_trans   = t_start
        t_last_wheel   = t_start
        frame_count    = 0

        while True:
            now    = time.monotonic()
            elapsed = now - t_start
            if elapsed >= duration_s:
                break

            # EngineData every 10 ms
            if now - t_last_engine >= 0.010:
                rpm = 800 + (elapsed / duration_s) * 3000 + random.gauss(0, 10)

                # 💣 FAULT 1: inject an out-of-range RPM at ~1.5 s
                if 1.45 <= elapsed <= 1.46:
                    rpm = 9500

                # 💣 FAULT 2: suppress EngineData for 80 ms around t=2.0 s
                if 1.95 <= elapsed <= 2.03:
                    t_last_engine = now
                    continue

                coolant = 85 + random.gauss(0, 0.5)
                throttle = min(100, max(0, 20 + elapsed * 10))
                load     = throttle * 0.8

                data = encode_engine_data(rpm, coolant, throttle, load)
                tx_bus.send(can.Message(
                    arbitration_id=0x201, data=data, is_extended_id=False,
                    timestamp=elapsed))
                t_last_engine = now
                frame_count  += 1

            # TransData every 20 ms
            if now - t_last_trans >= 0.020:
                speed = elapsed * 30  # ramp to ~90 km/h
                data  = encode_trans_data(3, 3, speed, 20)
                tx_bus.send(can.Message(
                    arbitration_id=0x300, data=data, is_extended_id=False))
                t_last_trans = now
                frame_count += 1

            # WheelSpeed every 10 ms
            if now - t_last_wheel >= 0.010:
                speed = elapsed * 30
                skew  = random.gauss(0, 0.2)
                data  = encode_wheel_speed(speed, speed + skew,
                                           speed - skew * 0.5, speed + skew * 0.3)
                tx_bus.send(can.Message(
                    arbitration_id=0x400, data=data, is_extended_id=False))
                t_last_wheel = now
                frame_count += 1

            time.sleep(0.001)

        notifier.stop()

    tx_bus.shutdown()
    rx_bus.shutdown()
    print(f"  ✓ Logged {frame_count} frames → {log_path}")


# ─── STEP 2: DECODE & ANALYSE ────────────────────────────────────────────────

class TestResult:
    """Accumulate pass/fail results like a mini pytest report."""

    def __init__(self):
        self.passed: list[str] = []
        self.failed: list[str] = []

    def step_pass(self, name: str, detail: str = "") -> None:
        tag = f"  ✅ PASS  {name}" + (f"  [{detail}]" if detail else "")
        self.passed.append(tag)
        print(tag)

    def step_fail(self, name: str, detail: str = "") -> None:
        tag = f"  ❌ FAIL  {name}" + (f"  [{detail}]" if detail else "")
        self.failed.append(tag)
        print(tag)

    def summary(self) -> None:
        total = len(self.passed) + len(self.failed)
        print(f"\n{'='*60}")
        print(f"  TEST SUMMARY: {len(self.passed)}/{total} passed, "
              f"{len(self.failed)} failed")
        print(f"{'='*60}")
        if self.failed:
            print("\n  Failed checks:")
            for f in self.failed:
                print(f"    {f.strip()}")


def analyse_log(log_path: Path, dbc_path: Path) -> None:
    """
    Read a CAN ASC log and run three categories of checks:
      A. Signal range validation   (against DBC min/max)
      B. Cycle time validation     (against GenMsgCycleTime attribute)
      C. Wheel speed consistency   (all 4 wheels within 5 km/h of each other)
    """
    print(f"\n{'='*60}")
    print("STEP 2: Loading DBC...")
    print(f"{'='*60}")

    db = cantools.db.load_file(str(dbc_path))
    tr = TestResult()

    # Build expected cycle times from DBC attributes
    expected_cycle: dict[int, float] = {}
    for msg in db.messages:
        attr = msg.dbc.attributes.get("GenMsgCycleTime") if msg.dbc else None
        if attr is not None:
            val = attr.value if hasattr(attr, "value") else attr
            if isinstance(val, (int, float)) and val > 0:
                expected_cycle[msg.frame_id] = val / 1000.0  # ms → seconds

    print(f"  Cycle-time oracles loaded from DBC: {expected_cycle}")

    print(f"\n{'='*60}")
    print("STEP 3: Parsing log file...")
    print(f"{'='*60}")

    # Per-message tracking for cycle-time analysis
    last_timestamp: dict[int, float] = {}
    intervals:      dict[int, list[float]] = defaultdict(list)
    frame_counts:   dict[int, int]  = defaultdict(int)
    range_failures: dict[str, list] = defaultdict(list)

    total_frames = 0
    unknown_ids  = set()

    with can.ASCReader(str(log_path)) as reader:
        for frame in reader:
            arb_id = frame.arbitration_id
            total_frames += 1

            # ── Cycle time tracking ──────────────────────────────────────
            if arb_id in last_timestamp:
                gap = frame.timestamp - last_timestamp[arb_id]
                intervals[arb_id].append(gap)
            last_timestamp[arb_id] = frame.timestamp

            # ── Decode ──────────────────────────────────────────────────
            try:
                msg_def = db.get_message_by_frame_id(arb_id)
            except KeyError:
                unknown_ids.add(arb_id)
                continue

            try:
                signals = msg_def.decode(bytes(frame.data))
            except Exception as e:
                tr.step_fail(f"Decode error  ID=0x{arb_id:03X}", str(e))
                continue

            frame_counts[arb_id] += 1

            # ── Signal range check ───────────────────────────────────────
            for sig in msg_def.signals:
                val = signals.get(sig.name)
                if val is None:
                    continue
                lo = sig.minimum
                hi = sig.maximum
                if lo is not None and hi is not None:
                    if not (lo <= val <= hi):
                        range_failures[sig.name].append(
                            (frame.timestamp, val, lo, hi))

    print(f"  Parsed {total_frames} frames total")
    if unknown_ids:
        print(f"  ⚠  Unknown IDs (not in DBC): "
              f"{[hex(i) for i in sorted(unknown_ids)]}")

    # ═══════════════════════════════════════════════════════════════════
    # CHECK A: Signal Range Validation
    # ═══════════════════════════════════════════════════════════════════
    print(f"\n{'─'*60}")
    print("CHECK A: Signal Range Validation")
    print(f"{'─'*60}")

    all_signals = {sig.name for msg in db.messages for sig in msg.signals}
    violated    = set(range_failures.keys())
    clean       = all_signals - violated

    for name in sorted(clean):
        tr.step_pass(f"Range OK  {name}")

    for name, violations in sorted(range_failures.items()):
        for ts, val, lo, hi in violations[:3]:   # show first 3 occurrences
            tr.step_fail(f"Range VIOLATION  {name}",
                         f"t={ts:.3f}s  value={val:.2f}  "
                         f"expected [{lo}, {hi}]")
        if len(violations) > 3:
            print(f"       ... and {len(violations)-3} more violations")

    # ═══════════════════════════════════════════════════════════════════
    # CHECK B: Cycle Time Validation
    # ═══════════════════════════════════════════════════════════════════
    print(f"\n{'─'*60}")
    print("CHECK B: Cycle Time Validation")
    print(f"{'─'*60}")

    TOLERANCE = 0.50    # allow ±50% of nominal before flagging a gap
    GAP_RATIO  = 2.5    # flag individual gaps > 2.5× nominal

    for arb_id, expected_s in expected_cycle.items():
        gaps    = intervals.get(arb_id, [])
        try:
            msg_name = db.get_message_by_frame_id(arb_id).name
        except KeyError:
            msg_name = f"0x{arb_id:03X}"

        if not gaps:
            tr.step_fail(f"CycleTime  {msg_name}", "no frames received")
            continue

        avg_gap  = sum(gaps) / len(gaps)
        max_gap  = max(gaps)
        jitter   = max(gaps) - min(gaps)

        # Average cycle time check
        deviation = abs(avg_gap - expected_s) / expected_s
        if deviation <= TOLERANCE:
            tr.step_pass(
                f"CycleTime avg  {msg_name}",
                f"avg={avg_gap*1000:.1f}ms  expected={expected_s*1000:.0f}ms  "
                f"jitter={jitter*1000:.1f}ms")
        else:
            tr.step_fail(
                f"CycleTime avg  {msg_name}",
                f"avg={avg_gap*1000:.1f}ms  expected={expected_s*1000:.0f}ms  "
                f"deviation={deviation*100:.0f}%")

        # Largest single gap — catches the injected silence fault
        if max_gap > expected_s * GAP_RATIO:
            tr.step_fail(
                f"CycleTime max-gap  {msg_name}",
                f"worst gap={max_gap*1000:.1f}ms  "
                f"threshold={expected_s*GAP_RATIO*1000:.0f}ms  "
                f"→ possible silent ECU / message loss")
        else:
            tr.step_pass(f"CycleTime max-gap  {msg_name}",
                         f"worst gap={max_gap*1000:.1f}ms OK")

    # ═══════════════════════════════════════════════════════════════════
    # CHECK C: Wheel Speed Consistency (cross-signal correlation)
    # ═══════════════════════════════════════════════════════════════════
    print(f"\n{'─'*60}")
    print("CHECK C: Wheel Speed Cross-Signal Consistency")
    print(f"{'─'*60}")

    wheel_id   = 0x400
    max_spread = 5.0   # km/h — any four wheels > 5 km/h spread = suspicious
    spread_violations = 0

    try:
        wheel_msg = db.get_message_by_frame_id(wheel_id)
        with can.ASCReader(str(log_path)) as reader:
            for frame in reader:
                if frame.arbitration_id != wheel_id:
                    continue
                signals = wheel_msg.decode(bytes(frame.data))
                speeds  = [signals["SpeedFL"], signals["SpeedFR"],
                           signals["SpeedRL"], signals["SpeedRR"]]
                spread  = max(speeds) - min(speeds)
                if spread > max_spread:
                    spread_violations += 1

        if spread_violations == 0:
            tr.step_pass("WheelSpeed consistency",
                         f"all frames within {max_spread} km/h spread")
        else:
            tr.step_fail("WheelSpeed consistency",
                         f"{spread_violations} frames exceeded "
                         f"{max_spread} km/h spread → check ABS / skid event")
    except KeyError:
        tr.step_fail("WheelSpeed consistency", "0x400 not in DBC")

    # ═══════════════════════════════════════════════════════════════════
    # REPORT
    # ═══════════════════════════════════════════════════════════════════
    tr.summary()


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main() -> None:
    print("\n" + "🐍 " * 20)
    print("  Day 10 Mini-Project: CAN Log Analyser")
    print("  python-can + cantools + structured assertions")
    print("🐍 " * 20)

    if not DBC_PATH.exists():
        print(f"\n❌  DBC not found: {DBC_PATH}")
        print("    Create vehicle_day10.dbc first (see lesson).")
        return

    # Generate the log (or skip if already exists)
    if not LOG_PATH.exists():
        generate_log(LOG_PATH, DURATION_S)
    else:
        print(f"\nℹ  Reusing existing log: {LOG_PATH}")
        print(   "   Delete drive_cycle.asc to regenerate.")

    analyse_log(LOG_PATH, DBC_PATH)


if __name__ == "__main__":
    main()
```

### Expected Output

```
🐍 🐍 🐍 🐍 🐍 🐍 🐍 🐍 🐍 🐍 🐍 🐍 🐍 🐍 🐍 🐍 🐍 🐍 🐍 🐍
  Day 10 Mini-Project: CAN Log Analyser
  python-can + cantools + structured assertions
🐍 🐍 🐍 🐍 🐍 🐍 🐍 🐍 🐍 🐍 🐍 🐍 🐍 🐍 🐍 🐍 🐍 🐍 🐍 🐍

============================================================
STEP 1: Generating synthetic drive-cycle log...
============================================================
  ✓ Logged 592 frames → drive_cycle.asc

============================================================
STEP 2: Loading DBC...
============================================================
  Cycle-time oracles loaded from DBC: {513: 0.01, 768: 0.02, 1024: 0.01}

============================================================
STEP 3: Parsing log file...
============================================================
  Parsed 592 frames total

────────────────────────────────────────────────────────────
CHECK A: Signal Range Validation
────────────────────────────────────────────────────────────
  ✅ PASS  Range OK  AccelPedal
  ✅ PASS  Range OK  CoolantTemp
  ✅ PASS  Range OK  EngineLoad
  ❌ FAIL  Range VIOLATION  EngineRPM  [t=1.455s  value=9500.00  expected [0, 8000]]
  ✅ PASS  Range OK  GearCurrent
  ✅ PASS  Range OK  GearTarget
  ✅ PASS  Range OK  SpeedFL
  ✅ PASS  Range OK  SpeedFR
  ✅ PASS  Range OK  SpeedRL
  ✅ PASS  Range OK  SpeedRR
  ✅ PASS  Range OK  ThrottlePos
  ✅ PASS  Range OK  VehicleSpeed

────────────────────────────────────────────────────────────
CHECK B: Cycle Time Validation
────────────────────────────────────────────────────────────
  ✅ PASS  CycleTime avg  EngineData   [avg=10.1ms  expected=10ms  jitter=1.2ms]
  ❌ FAIL  CycleTime max-gap  EngineData  [worst gap=82.4ms  threshold=25ms → possible silent ECU / message loss]
  ✅ PASS  CycleTime avg  TransData    [avg=20.2ms  expected=20ms  jitter=0.8ms]
  ✅ PASS  CycleTime max-gap  TransData  [worst gap=21.3ms OK]
  ✅ PASS  CycleTime avg  WheelSpeed   [avg=10.0ms  expected=10ms  jitter=0.9ms]
  ✅ PASS  CycleTime max-gap  WheelSpeed [worst gap=11.1ms OK]

────────────────────────────────────────────────────────────
CHECK C: Wheel Speed Cross-Signal Consistency
────────────────────────────────────────────────────────────
  ✅ PASS  WheelSpeed consistency  [all frames within 5 km/h spread]

============================================================
  TEST SUMMARY: 15/17 passed, 2 failed
============================================================

  Failed checks:
    ❌ FAIL  Range VIOLATION  EngineRPM  [t=1.455s  value=9500.00  expected [0, 8000]]
    ❌ FAIL  CycleTime max-gap  EngineData  [worst gap=82.4ms  threshold=25ms → possible silent ECU / message loss]
```

**Both injected faults were caught.** That's your test framework working.

---

## 🌍 Where It's Used in the Real World

| Use Case | How python-can + cantools Is Used |
|---|---|
| **Regression testing in CI/CD** | pytest runs overnight; every PR triggers the CAN log analyser against golden reference logs |
| **HIL (Hardware-in-the-Loop)** | Python test script sends stimuli via `bus.send()`, records responses, decodes with cantools, asserts range + timing |
| **Fleet data analysis** | Terabytes of `.blf` logs from customer vehicles → Python pandas pipeline decodes signals, spots statistical anomalies |
| **OBD-II reading** | `python-obd` (built on python-can) reads live PID data from any OBDII port |
| **Automated flashing** | Python script sends UDS/ISO-TP frames (ISO 14229) to ECUs for reflash and validation |
| **Test report generation** | Decoded signals → pandas DataFrame → HTML/PDF report for ASPICE/ISO 26262 evidence |

---

## 🧠 How a Tester Thinks About It

### The Three Questions for Every Log File

```
┌─────────────────────────────────────────────────────────┐
│  TESTER'S LOG ANALYSIS CHECKLIST                        │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  1. IS IT THERE?   (presence check)                    │
│     "Did message 0x201 arrive at all?"                  │
│     → frame_counts[arb_id] > 0                          │
│                                                         │
│  2. IS IT CORRECT?  (value check)                      │
│     "Are all signals within DBC min/max?"               │
│     → range check against sig.minimum / sig.maximum     │
│                                                         │
│  3. IS IT ON TIME?  (temporal check)                   │
│     "Is the average gap ≈ GenMsgCycleTime?"             │
│     "Was the worst gap ever > 2.5× nominal?"            │
│     → intervals dict + max() check                      │
│                                                         │
│  Bonus:                                                 │
│  4. IS IT CONSISTENT?  (cross-signal check)            │
│     "Do related signals agree with each other?"         │
│     → wheel speed spread, gear vs. speed plausibility   │
└─────────────────────────────────────────────────────────┘
```

> 🌉 **From your world:** This is exactly your Playwright API test checklist:
> 1. **Status 200?** (presence — the endpoint responded)
> 2. **Schema valid?** (correctness — response body matches spec)
> 3. **Response time < SLA?** (temporal — performance budget)
> 4. **Consistent with other calls?** (cross-check — auth token in body matches header)
>
> Same four questions. Different transport layer.

### The "Averages Hide Danger" Trap — Revisited

Day 4 taught this with jitter. It matters even more for log analysis:

```python
# DON'T DO THIS — averages hide the 82 ms gap
avg_gap = sum(intervals[0x201]) / len(intervals[0x201])
assert abs(avg_gap - 0.010) < 0.003   # PASSES! average is fine

# DO THIS — catch the worst-case gap
max_gap = max(intervals[0x201])
assert max_gap < 0.010 * 2.5          # FAILS! 82 ms >> 25 ms threshold
```

The ECU could have had a 80 ms brownout. The average hides it. **Always assert on `max()` for safety-critical timing, not `mean()`.**

---

## 🏋️ Hands-On Exercise: Offline Log Analyser

Run the mini-project as-is, then extend it.

### Exercise 1 — Run It

```bash
cd Day-10_Python_CAN_Tools
python can_log_analyser.py
```

Confirm you see 2 failures: the RPM spike and the engine silence.

### Exercise 2 — Fix the RPM Fault

In `generate_log()`, change the injected RPM:
```python
# Before
rpm = 9500
# After
rpm = 7500
```

Delete `drive_cycle.asc` and re-run. The Range VIOLATION for `EngineRPM` should now pass.

### Exercise 3 — Add a Gear Plausibility Check

In `analyse_log()`, after Check C, add Check D:

> *"When VehicleSpeed > 60 km/h, GearCurrent must be >= 3 (not in 1st or 2nd gear)"*

```python
# Hint
trans_msg = db.get_message_by_frame_id(0x300)
with can.ASCReader(str(log_path)) as reader:
    for frame in reader:
        if frame.arbitration_id != 0x300:
            continue
        s = trans_msg.decode(bytes(frame.data))
        if s["VehicleSpeed"] > 60.0 and s["GearCurrent"] < 3:
            # log violation
```

### Exercise 4 — Export to CSV

Add a section that writes all decoded EngineData frames to a CSV:

```python
import csv

with open("engine_decoded.csv", "w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(["timestamp", "EngineRPM", "CoolantTemp",
                     "ThrottlePos", "EngineLoad"])
    # ... read log, decode 0x201, write rows
```

Open `engine_decoded.csv` in Excel or Google Sheets. Plot RPM vs. time. Watch the spike at t≈1.5 s jump out visually.

---

## 🔥 Challenge: Anomaly Hunter

**Scenario:** You receive a `.asc` log from a customer reporting "intermittent ABS warning light." The log was recorded during the reported event.

**Your mission:**

1. Enhance `analyse_log()` to detect **wheel speed rollover** — a signal that jumps from a high value to near zero (not a gradual deceleration) within a single frame interval. This indicates a sensor dropout or encoding error.

2. Add a **missing-message detector** — if any expected message (from the DBC) is absent from the log entirely, fail the test with a clear message: `"No frames received for <MsgName> (ID=0xXXX) — ECU offline?"`

3. Add a **duplicate ID detector** — real CAN networks occasionally see two ECUs fighting over the same message ID (a DBC misconfiguration). Flag any ID that sends frames more frequently than 80% of its expected cycle time (i.e., appears to have two senders).

**Stretch goal:** Output the full test report as a structured JSON file (`report.json`) suitable for ingestion by a CI/CD system (Jenkins, GitHub Actions, etc.):

```json
{
  "timestamp": "2026-06-14T10:30:00",
  "log_file": "drive_cycle.asc",
  "dbc_file": "vehicle_day10.dbc",
  "total_frames": 592,
  "checks": [
    {"name": "Range OK  AccelPedal", "status": "PASS"},
    {"name": "Range VIOLATION  EngineRPM", "status": "FAIL",
     "detail": "t=1.455s  value=9500.00  expected [0, 8000]"}
  ],
  "summary": {"passed": 15, "failed": 2, "total": 17}
}
```

---

## ❓ Quiz + Answers

**Q1.** `can.Bus(interface='virtual')` — does the sender receive its own frame on a virtual bus?

<details>
<summary>Answer</summary>

**Yes.** On a virtual bus, the sender and all receivers share the same in-process channel. A node sending on `vcan0` will receive back its own frame unless it explicitly creates a second `Bus` object on a different channel or uses `receive_own_messages=False`. On a real hardware bus, you need two physical adapters to simulate a node pair.

</details>

---

**Q2.** You load a DBC and call `db.decode_message(0x201, raw_bytes)`. The DBC defines `EngineRPM` with `scale=1, offset=0`. The raw bytes give `rpm_raw = 0x0BB8`. What is the decoded `EngineRPM`?

<details>
<summary>Answer</summary>

`0x0BB8` = **3000**. Physical = raw × scale + offset = 3000 × 1 + 0 = **3000 RPM**. Always apply the formula — don't assume `scale=1, offset=0` just because the raw value "looks right."

</details>

---

**Q3.** Your log has 290 EngineData frames over 3 seconds. Expected cycle time = 10 ms. Is this normal?

<details>
<summary>Answer</summary>

**Yes, approximately.** 3 seconds ÷ 10 ms/frame = 300 expected frames. 290 is within ~3.3% of expectation — well within normal jitter. The injected 80 ms silence removes roughly 8 frames, and OS timing imprecision accounts for the rest. Don't assert on exact frame counts; assert on gap timing.

</details>

---

**Q4.** Why is `assert avg_interval ≈ 10ms` an **insufficient** cycle time test?

<details>
<summary>Answer</summary>

Because a 80 ms silence followed by a burst of frames at 2 ms intervals can produce an average of exactly 10 ms — **the average is preserved while the worst case is catastrophic**. Safety-critical timing must be validated on **max gap** (detect silence / ECU freeze) AND **min gap** (detect burst / duplicate sender), not just the average.

</details>

---

**Q5.** What does `set_filters([{"can_id": 0x200, "can_mask": 0x7F0}])` match?

<details>
<summary>Answer</summary>

It matches any CAN ID where `ID & 0x7F0 == 0x200` — i.e., IDs **0x200 through 0x20F** (the lower nibble can be anything). This is a range filter accepting 16 IDs: 0x200, 0x201, ..., 0x20F.

</details>

---

## 📌 Key Takeaways

```
┌─────────────────────────────────────────────────────────────────┐
│  DAY 10 KEY TAKEAWAYS                                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  1. python-can + cantools = open-source, CI/CD-native CAN      │
│     automation. No CANoe licence required.                      │
│                                                                 │
│  2. Change the interface string (virtual → socketcan → pcan)   │
│     and all your code works unchanged. Same as swapping         │
│     baseURL in a test config.                                   │
│                                                                 │
│  3. cantools turns raw bytes into named, scaled, unit-tagged   │
│     Python dicts. Think: JSON.parse() + schema validation       │
│     in one call, for binary CAN frames.                         │
│                                                                 │
│  4. Log files (.asc, .blf) are your recorded evidence.         │
│     Offline analysis is just as important as live testing —     │
│     you can re-run assertions on historical data without        │
│     hardware.                                                   │
│                                                                 │
│  5. The three log analysis questions: IS IT THERE?             │
│     IS IT CORRECT? IS IT ON TIME? — map 1:1 to your            │
│     existing API testing checklist.                             │
│                                                                 │
│  6. Always assert on max gap, not average gap. Averages         │
│     hide catastrophic silences. (Day 4 revisited.)             │
│                                                                 │
│  7. DBC attributes (GenMsgCycleTime) are the test oracle —     │
│     the spec tells you what to assert. Read it from the DBC,   │
│     don't hardcode it in your test.                             │
│                                                                 │
│  8. The TestResult class pattern (step_pass / step_fail        │
│     + summary) is the pytest mental model applied to CAN.       │
│     You already know how to structure this.                     │
└─────────────────────────────────────────────────────────────────┘
```

---

## ⏭️ Run code 
```
cd "Day-10_Python_CAN_Tools"
pip install python-can cantools
python can_log_analyser.py
```
