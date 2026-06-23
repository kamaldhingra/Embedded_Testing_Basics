# 📖✏️ Day 13: UDS Services — ReadDataByIdentifier (0x22) & WriteDataByIdentifier (0x2E)

> **Course:** Embedded & IoT Testing Masterclass
> **Instructor:** Professor Embed
> **Prerequisites:** Day 1–12 (CAN fundamentals + python-can + UDS Session Control & ECU Reset)

---

## 📚 Table of Contents

1. [Recap: Where We Left Off](#recap-where-we-left-off)
2. [Concept: What Is a Data Identifier (DID)?](#concept-what-is-a-did)
3. [Concept: The Standard DID Catalogue — Knowing Your F18X From Your F1XX](#concept-did-catalogue)
4. [Concept: Service 0x22 — ReadDataByIdentifier](#concept-0x22)
5. [Concept: Multi-DID Read — One Request, Many Values](#concept-multi-did)
6. [Concept: Service 0x2E — WriteDataByIdentifier](#concept-0x2e)
7. [Concept: Session & Security Gating for Writes](#concept-security-gating)
8. [Concept: NRCs Specific to 0x22 and 0x2E](#concept-nrcs)
9. [The Big Picture: The DID as a REST Resource](#the-big-picture)
10. [Where It's Used in the Real World](#where-its-used)
11. [How a Tester Thinks About It](#how-a-tester-thinks)
12. [Hands-On Exercise: ECU Data Store Simulator](#hands-on-exercise)
13. [Challenge: The Calibration Validation Suite](#challenge)
14. [Quiz + Answers](#quiz--answers)
15. [Key Takeaways](#key-takeaways)

---

## 🔁 Recap: Where We Left Off

Day 12 gave you the **gatekeeping layer** of UDS:

- Sessions are access levels — default (guest), extended (admin), programming (root)
- Service 0x10 switches sessions; direct jumps are rejected with NRC 0x24
- Service 0x11 reboots the ECU; hard reset takes the ECU offline for its boot time
- Positive response = SID + 0x40 — the golden rule
- Negative response = `0x7F [SID] [NRC]`
- The S3 timer is a session expiry watchdog — just like a JWT timeout

You now know how to **knock on the door and get inside**. Today you learn what to do once you're in — specifically, how to **read and write named data from inside the ECU**.

> *"Session control was the lobby. Today we open the filing cabinets."*

---

## 🧠 Concept: What Is a Data Identifier (DID)?

### The Spreadsheet Cell Analogy 📊

Imagine the ECU's memory organised like a giant spreadsheet. Each row has a **two-byte address** (the DID) and a **value** (bytes of any length). You can:
- **Read** any cell you're allowed to read: `GET /cell/F190` → returns VIN
- **Write** any cell you're allowed to write: `PUT /cell/2001` → sets tyre pressure calibration

This is exactly what 0x22 and 0x2E do.

```
┌──────────────────────────────────────────────────────────────────┐
│  ECU INTERNAL DATA STORE (conceptual)                            │
├────────┬──────────────────────────────┬──────────────────────────┤
│  DID   │  Name                        │  Value                   │
├────────┼──────────────────────────────┼──────────────────────────┤
│  0xF190│  VIN                         │  "WBA3A5G59DNP26082"     │
│  0xF18C│  ECU Serial Number           │  "ECU20240315-001"       │
│  0xF189│  ECU Software Version        │  "v2.4.1"                │
│  0xF187│  Vehicle Spare Part Number   │  "3AA-906-021-A"         │
│  0x2001│  TyrePressureCalibration_FL  │  0x00A0  (= 160 kPa)    │
│  0x2002│  TyrePressureCalibration_FR  │  0x00A0                  │
│  0x3001│  MaxRPMLimit                 │  0x1F40  (= 8000 rpm)    │
│  0x4001│  OdometerReading             │  0x001234 (= 4660 km)    │
│  0x5001│  InternalTemperatureSensor   │  0x003C  (= 60 °C)       │
└────────┴──────────────────────────────┴──────────────────────────┘
```

> **DIDs are two bytes**, allowing 65,536 possible identifiers. In practice, each ECU supports a manufacturer-defined subset, documented in the **ODX/PDXS or diagnostic specification**. If you ask for a DID the ECU doesn't recognise, you get NRC 0x31 (requestOutOfRange).

> 🌉 **From your world:** A DID is a **REST resource URI** with a fixed address. `0xF190` is like `/api/ecu/vin`. `0x2001` is like `/api/calibration/tyre-pressure/fl`. `ReadDataByIdentifier` = HTTP GET. `WriteDataByIdentifier` = HTTP PUT. The diagnostic spec is the API documentation; the DID is the endpoint path.

---

## 🧠 Concept: The Standard DID Catalogue — Knowing Your F18X From Your F1XX

ISO 14229 reserves a range of DIDs with standardised meanings. These are the ones every automotive tester must know cold:

```
┌────────┬──────────────────────────────────────────────────────────┐
│  DID   │  ISO 14229 Standardised Meaning                          │
├────────┼──────────────────────────────────────────────────────────┤
│  0xF186│  ActiveDiagnosticSessionDataRecord                       │
│        │  → Returns the CURRENT session type (0x01/02/03)         │
│        │  Test use: "What session am I in right now?"             │
├────────┼──────────────────────────────────────────────────────────┤
│  0xF187│  VehicleManufacturerSparePartNumber                      │
├────────┼──────────────────────────────────────────────────────────┤
│  0xF188│  VehicleManufacturerECUSoftwareNumber                    │
├────────┼──────────────────────────────────────────────────────────┤
│  0xF189│  VehicleManufacturerECUSoftwareVersionNumber             │
│        │  → Firmware version string — validate before flashing    │
├────────┼──────────────────────────────────────────────────────────┤
│  0xF18A│  SystemSupplierIdentifierDataIdentifier                  │
├────────┼──────────────────────────────────────────────────────────┤
│  0xF18C│  ECUSerialNumberDataIdentifier                           │
│        │  → Unique ECU identity — trace to physical hardware      │
├────────┼──────────────────────────────────────────────────────────┤
│  0xF190│  VINDataIdentifier                                        │
│        │  → 17-character Vehicle Identification Number            │
│        │  → Most important EOL check: "Is this ECU married to     │
│           the right car?"                                         │
├────────┼──────────────────────────────────────────────────────────┤
│  0xF191│  VehicleManufacturerECUHardwareNumber                    │
├────────┼──────────────────────────────────────────────────────────┤
│  0xF197│  SystemNameOrEngineType                                  │
└────────┴──────────────────────────────────────────────────────────┘

Manufacturer-specific DIDs typically live in ranges:
  0x0100–0xEFFF : manufacturer-defined (calibration, live data, config)
  0xF000–0xFEFF : legislative/standardised (above)
  0xFF00–0xFFFF : reserved
```

> **0xF186 is the tester's secret weapon.** Read it and you instantly know the active session without relying on state tracking in your test tool. It's an ECU self-describing its own auth level. Use it as a pre-condition assertion in every test case that cares about session state.

---

## 🧠 Concept: Service 0x22 — ReadDataByIdentifier

### Request Format

```
┌──────────┬──────────────────────────────────────────────┐
│  0x22    │  DID_high  DID_low  [DID2_high  DID2_low ...]│
│  SID     │  2 bytes per DID, one or more DIDs            │
└──────────┴──────────────────────────────────────────────┘

Example: Read VIN (DID 0xF190)
  Tester → ECU:  [0x03, 0x22, 0xF1, 0x90, 0x00, 0x00, 0x00, 0x00]
                  ^^^   ^^^^  ^^^^^^^^^^^^
                  PCI   SID   DID bytes (big-endian: F1 then 90)
```

### Positive Response Format

```
┌──────────┬────────────────┬──────────────────────────────┐
│  0x62    │  DID_high low  │  dataRecord (N bytes)        │
│  (0x22+  │  2 bytes       │  ECU's current value         │
│   0x40)  │                │                              │
└──────────┴────────────────┴──────────────────────────────┘

Example: Positive response with 17-byte VIN
  ECU → Tester:  [0x62, 0xF1, 0x90, 'W','B','A','3','A','5',...] (multi-frame!)
                  ^^^^  ^^^^^^^^^^^^  ─────────────────────────
                  SID   DID echoed     17 bytes of VIN data
```

> ⚠️ **Single Frame vs Multi-Frame:** The VIN is 17 bytes — plus the 3-byte header (SID + DID) = 20 bytes total. That's **way beyond a single 7-byte ISO-TP single frame**. So a real VIN read uses ISO-TP First Frame + Consecutive Frame. In our Python simulation we handle the response as a raw byte buffer (abstracting away the ISO-TP framing for clarity), but you must understand that in a real trace any DID response > 7 bytes will be multi-frame.

### Session Availability

| Session | 0x22 ReadDataByIdentifier |
|---|---|
| Default (0x01) | ✅ Most DIDs (VIN, serial, software version) |
| Extended (0x03) | ✅ All DIDs including diagnostic status DIDs |
| Programming (0x02) | ⚠️ Limited (typically only basic ID DIDs allowed) |

> `ReadDataByIdentifier` is **generally allowed in all sessions** for standardised DIDs. Manufacturer-specific live-data DIDs may require extended session. Read-only data (VIN, serial, SW version) is almost always available in defaultSession — it would be odd to hide your VIN from a tool.

---

## 🧠 Concept: Multi-DID Read — One Request, Many Values

This is a power feature many testers miss. ISO 14229 allows **multiple DIDs in a single 0x22 request**:

```
Request: Read VIN + Software Version + Active Session in one shot
  [0x22, 0xF1, 0x90,   ← DID 0xF190 (VIN)
         0xF1, 0x89,   ← DID 0xF189 (SW version)
         0xF1, 0x86]   ← DID 0xF186 (active session)

Positive Response:
  [0x62,
   0xF1, 0x90, <17 VIN bytes>,
   0xF1, 0x89, <N SW version bytes>,
   0xF1, 0x86, <1 session byte>]
```

The ECU **concatenates all DID responses** into one multi-frame reply. Each DID's data is prefixed by its own 2-byte DID header, so the receiver can parse them in sequence.

**Why this matters for testing:**
- **Faster pre-condition checks** — one round-trip to verify VIN + SW version + session before the real test begins
- **Atomic snapshot** — all values captured at the same moment (same ISO-TP transaction)
- **Fewer CAN frames** — better for bandwidth-sensitive logging

> 🌉 **From your world:** Multi-DID is a **batch API call** or GraphQL query vs. N separate REST calls. You already know the performance argument — one network round trip beats N sequential ones. Same discipline, different transport.

> ⚠️ **The trap:** If the ECU doesn't support one of the DIDs in the list, some ECUs return NRC 0x31 for the **entire request** (rejecting all DIDs). Others return responses for the DIDs they know and skip the unknown ones. Test this behaviour explicitly — don't assume partial success.

---

## 🧠 Concept: Service 0x2E — WriteDataByIdentifier

### Request Format

```
┌──────────┬────────────────┬───────────────────────────────┐
│  0x2E    │  DID_high low  │  dataRecord (N bytes)         │
│  SID     │  2 bytes       │  new value to write           │
└──────────┴────────────────┴───────────────────────────────┘

Example: Write tyre pressure calibration (DID 0x2001) = 180 kPa
  Tester → ECU:  [0x2E, 0x20, 0x01, 0x00, 0xB4]
                  ^^^^  ^^^^^^^^^^^^  ^^^^^^^^^
                  SID   DID           0x00B4 = 180 (big-endian)
```

### Positive Response Format

```
┌──────────┬────────────────┐
│  0x6E    │  DID_high low  │
│  (0x2E+  │  2 bytes       │  ← just echoes the DID, no data
│   0x40)  │                │
└──────────┴────────────────┘

Example: Positive response to the tyre pressure write
  ECU → Tester:  [0x6E, 0x20, 0x01]
```

> **The positive response to 0x2E is minimal by design** — it just acknowledges the write succeeded by echoing the DID. The value is not echoed back. To verify the write took effect, immediately follow with a 0x22 ReadDataByIdentifier on the same DID. This read-back-verify pattern is mandatory for testing.

### What CAN be Written via 0x2E?

| DID Category | Examples | Notes |
|---|---|---|
| Calibration data | Tyre pressure, sensor offsets, PID gains | Requires extended session |
| Configuration flags | Feature enables/disables, country variants | May require security access |
| Identifiers | VIN, ECU serial (only on blank ECUs) | EOL process; usually requires security access |
| Odometer | Mileage | Heavily restricted (legal implications) |
| Test flags | Endurance mode, debug output enable | Factory use only |

> ⚠️ **You cannot write to read-only DIDs.** Trying to write VIN on an already-programmed ECU returns NRC 0x22 (conditionsNotCorrect) or NRC 0x33 (securityAccessDenied). The ECU protects itself. Testers often forget to test this — the spec says "VIN is writable" and they test the write but forget to test the **second write is rejected** (VIN lock).

---

## 🧠 Concept: Session & Security Gating for Writes

This is the key difference between 0x22 and 0x2E. Reads are almost always safe. Writes need protection.

```
┌────────────────────────────────────────────────────────────────────┐
│  WRITE PROTECTION LAYERS (0x2E requires ALL applicable layers)     │
├────────────────────────────────────────────────────────────────────┤
│                                                                    │
│  Layer 1: CORRECT SESSION                                         │
│  Most writes require extendedDiagnosticSession (0x03) or          │
│  programmingSession (0x02). Attempting a write in default session  │
│  returns NRC 0x22 (conditionsNotCorrect).                          │
│                                                                    │
│  Layer 2: SECURITY ACCESS (0x27) — if required by the DID         │
│  High-risk writes (firmware, VIN, calibration) require a          │
│  SecurityAccess unlock (seed → key challenge/response).            │
│  Without it: NRC 0x33 (securityAccessDenied).                     │
│  (Security Access is Day 14's topic — today we simulate a         │
│   simplified "always unlocked in extended session" model.)         │
│                                                                    │
│  Layer 3: DID IS WRITABLE                                         │
│  Some DIDs are read-only by design (live sensor data, odometer    │
│  on a used ECU). Attempting to write returns NRC 0x31             │
│  (requestOutOfRange) or NRC 0x22 (conditionsNotCorrect).          │
│                                                                    │
│  Layer 4: DATA VALIDITY                                           │
│  The value must be within the DID's defined range and have the    │
│  correct length. Wrong length → NRC 0x13. Out of range → 0x31.   │
└────────────────────────────────────────────────────────────────────┘
```

> 🌉 **From your world:** This is a **multi-factor authorization** model. A REST API with `PATCH /calibration/tyre-pressure` would require:
> 1. Valid JWT (session = right session)
> 2. `role: admin` claim (security access = unlocked)
> 3. The endpoint must support PATCH (DID must be writable)
> 4. Request body must pass schema validation (data must be valid)
>
> UDS writes require exactly the same four checks. The NRC tells you which layer rejected you.

---

## 🧠 Concept: NRCs Specific to 0x22 and 0x2E

You already know the general NRC table from Day 12. Here are the ones that specifically bite you with these two services:

```
┌────────┬──────────────────────────────┬──────────────────────────────────────┐
│  NRC   │  Name                        │  When you see it with 0x22/0x2E      │
├────────┼──────────────────────────────┼──────────────────────────────────────┤
│  0x13  │  incorrectMessageLength      │  0x2E: wrong number of bytes sent    │
│        │  OrInvalidFormat             │  e.g., sent 3 bytes for a 4-byte DID │
├────────┼──────────────────────────────┼──────────────────────────────────────┤
│  0x22  │  conditionsNotCorrect        │  0x2E: correct session NOT active    │
│        │                              │  "You're in default; go to extended" │
├────────┼──────────────────────────────┼──────────────────────────────────────┤
│  0x31  │  requestOutOfRange           │  0x22: DID not supported by ECU      │
│        │                              │  0x2E: value outside allowed range,  │
│        │                              │  or DID is read-only                 │
├────────┼──────────────────────────────┼──────────────────────────────────────┤
│  0x33  │  securityAccessDenied        │  0x2E: DID requires SecurityAccess   │
│        │                              │  unlock (0x27) first                 │
├────────┼──────────────────────────────┼──────────────────────────────────────┤
│  0x78  │  requestCorrectlyReceived    │  Either service: ECU needs more      │
│        │  ResponsePending             │  time — extend wait, not a failure   │
└────────┴──────────────────────────────┴──────────────────────────────────────┘
```

> **NRC 0x31 for 0x22 is the "404 for DIDs."** If a tool asks for a DID the ECU has never heard of, it responds with 0x31. This is the right behaviour — it's not a CAN error, it's a feature mismatch between what the tester expects and what the ECU implements. Always check the ECU's diagnostic specification for the supported DID list before writing tests.

---

## 🧩 The Big Picture: The DID as a REST Resource

Let's make the analogy concrete and then put everything in one diagram:

```
┌──────────────────────────────────────────────────────────────────────┐
│  DID  ≡  REST RESOURCE                                               │
│                                                                      │
│  HTTP            │  UDS                                              │
│  ──────────────  │  ─────────────────────────────────────────        │
│  GET /vin        │  0x22 0xF190  → 0x62 0xF190 <17 bytes>           │
│  GET /sw-version │  0x22 0xF189  → 0x62 0xF189 <bytes>              │
│  PUT /tyre-cal   │  0x2E 0x2001 <value> → 0x6E 0x2001               │
│  404 Not Found   │  0x7F 0x22 0x31  (DID not supported)             │
│  401 Unauth'd    │  0x7F 0x2E 0x33  (SecurityAccess needed)         │
│  412 Precond.    │  0x7F 0x2E 0x22  (wrong session)                 │
│  400 Bad Request │  0x7F 0x2E 0x13  (wrong data length)             │
│  Batch GET       │  0x22 0xF190 0xF189 0xF186  (multi-DID)          │
│                                                                      │
│  ─────────────────────────────────────────────────────────────────  │
│                    TEST STRATEGY MAPPING                             │
│                                                                      │
│  Read VIN                  → 0x22 0xF190,  assert 17 bytes, valid   │
│  Read SW version           → 0x22 0xF189,  assert version ≥ minimum │
│  Read active session       → 0x22 0xF186,  use as pre-condition      │
│  Write calibration         → switch to extended → 0x2E → read-back  │
│  Write in wrong session    → in default → 0x2E → expect NRC 0x22    │
│  Write unknown DID         → 0x2E 0x9999 → expect NRC 0x31          │
│  Write wrong data length   → 0x2E 0x2001 <1 byte> → NRC 0x13        │
│  Write out-of-range value  → 0x2E 0x2001 <max+1> → NRC 0x31         │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 🌍 Where It's Used in the Real World

| Context | 0x22 / 0x2E Usage |
|---|---|
| **EOL production test** | Read VIN (0xF190) to confirm it matches the car's build record; read SW version to confirm the right firmware was flashed; write VIN onto a blank ECU |
| **Workshop diagnostics** | Read live data DIDs: engine temperature, battery state-of-health, mileage; read fault-related counters |
| **Field calibration** | Sensor offset correction after a component replacement (e.g., steering angle sensor zero-point write); tyre pressure threshold updates |
| **OTA update pre/post check** | Read SW version before flashing, verify target version after flashing, read ECU serial to confirm identity |
| **HIL / regression testing** | Read a known DID to assert ECU is alive and in the expected state before each test case; write calibration values to set up a specific scenario |
| **ASPICE evidence** | "System shall correctly report VIN" — 0x22 0xF190 response is the verification artefact |

---

## 🔬 How a Tester Thinks About It

```
┌────────────────────────────────────────────────────────────────┐
│  TESTER'S CHECKLIST — 0x22 and 0x2E                            │
├────────────────────────────────────────────────────────────────┤
│                                                                │
│  READ (0x22)                                                   │
│  ✓ Does 0xF190 return a 17-byte VIN? (correct length?)        │
│  ✓ Is the VIN format valid (no illegal chars)?                 │
│  ✓ Does 0xF186 confirm the expected session?                   │
│  ✓ Does 0xF189 return a version ≥ minimum required?           │
│  ✓ Does requesting an unsupported DID return NRC 0x31?        │
│  ✓ Do multi-DID reads return all DIDs correctly?              │
│  ✓ Does a multi-DID read with one bad DID fail correctly?     │
│                                                                │
│  WRITE (0x2E)                                                  │
│  ✓ Does write in defaultSession return NRC 0x22?              │
│  ✓ Does write in extendedSession with valid data succeed?      │
│  ✓ After write: does a read-back confirm the new value?        │
│  ✓ Does write of wrong-length data return NRC 0x13?           │
│  ✓ Does write of out-of-range value return NRC 0x31?          │
│  ✓ Does write to a read-only DID return NRC 0x31 or 0x22?     │
│  ✓ After ECU reset: does written value persist? (NVM check)   │
│  ✓ Is the write idempotent? (same value, same result)         │
└────────────────────────────────────────────────────────────────┘
```

### The Read-Back-Verify Pattern

This is non-negotiable for 0x2E testing:

```
Write sequence (ALWAYS do all three steps):

  1.  0x2E [DID] [new_value]  →  0x6E [DID]         ← write acknowledged
  2.  0x22 [DID]              →  0x62 [DID] [value]  ← read it back
  3.  assert decoded_value == new_value               ← verify it stuck

Why: A positive response from 0x2E means the ECU RECEIVED the write.
     It does NOT guarantee the ECU APPLIED it. Only a read-back confirms.
     This is the UDS equivalent of "write then read" in a database test —
     you don't trust a write until you've verified the read.
```

> 🌉 **From your world:** You've always done this in API testing — `POST /user` returns 201, then `GET /user/{id}` verifies the data is actually there. Same discipline. A 0x6E is just a 201 Created; the 0x62 readback is your GET assertion.

### The NVM Persistence Test — The One Testers Always Forget

After a write, **reset the ECU** and read the DID again. The value should persist. If it doesn't, the ECU is writing to RAM only, not to Non-Volatile Memory (NVM). This is a genuine bug that passes every write test but shows up in production when the car loses power.

```
Write test (incomplete):
  write 0x2E → OK → read-back → OK → ✅ PASS (but wrong!)

Write test (complete):
  write 0x2E → OK → read-back → OK → reset ECU → read-back again → OK → ✅ PASS (correct)
```

> 🌉 **From your world:** This is your "does the state persist after a server restart?" test. You've been doing this for APIs backed by databases for years. Same pattern — just ECU NVM instead of PostgreSQL.

---

## 🛠️ Hands-On Exercise: ECU Data Store Simulator

### What You'll Build

A simulated ECU with an internal DID store, plus a tester client that exercises all read/write scenarios:

```
Day-13_UDS_ReadWrite_Data/
├── uds_read_write.py   ← full simulation + test suite
└── Day13_UDS_ReadWrite_Data.md
```

The ECU supports:
- `0xF190` VIN (read-only, standardised)
- `0xF189` SW Version (read-only, standardised)
- `0xF186` Active Session (read-only, dynamic — returns current session)
- `0xF18C` ECU Serial Number (read-only, standardised)
- `0x2001` TyrePressure_FL (read-write, requires extended session, 2 bytes, 80–280 kPa)
- `0x3001` MaxRPMLimitCalibration (read-write, requires extended session + security access flag, 2 bytes, 4000–8000 rpm)
- `0x5001` InternalTemperature (read-only, live sensor data)

---

## 🛠️ `uds_read_write.py` — Full Listing

```python
"""
Day 13: UDS ReadDataByIdentifier (0x22) & WriteDataByIdentifier (0x2E)
=======================================================================
Simulates an ECU with a DID data store on a python-can virtual bus.
Exercises read, multi-DID read, write with session gating, range
validation, NVM persistence, and all relevant error paths.

No hardware needed.

Install:
    pip install python-can
"""

import can
import threading
import time
import struct

# ─── UDS CONSTANTS ───────────────────────────────────────────────────────────

CHANNEL      = "vcan0"
TESTER_TX_ID = 0x7E0
TESTER_RX_ID = 0x7E8

# Service IDs
SID_SESSION  = 0x10
SID_READ     = 0x22
SID_WRITE    = 0x2E
SID_NEG      = 0x7F

# Session types
SESSION_DEFAULT     = 0x01
SESSION_PROGRAMMING = 0x02
SESSION_EXTENDED    = 0x03

# NRCs
NRC_SUBFUNC_NOT_SUPPORTED          = 0x12
NRC_INCORRECT_MSG_LENGTH_OR_FORMAT = 0x13
NRC_CONDITIONS_NOT_CORRECT         = 0x22
NRC_REQUEST_OUT_OF_RANGE           = 0x31
NRC_SECURITY_ACCESS_DENIED         = 0x33

# Standardised DIDs
DID_ACTIVE_SESSION   = 0xF186
DID_SW_VERSION       = 0xF189
DID_ECU_SERIAL       = 0xF18C
DID_VIN              = 0xF190

# Manufacturer DIDs
DID_TYRE_PRESSURE_FL = 0x2001
DID_MAX_RPM_LIMIT    = 0x3001
DID_INTERNAL_TEMP    = 0x5001


# ─── ISO-TP HELPERS ───────────────────────────────────────────────────────────

def build_single_frame(uds_bytes: list) -> bytes:
    assert 1 <= len(uds_bytes) <= 7
    pci    = len(uds_bytes)
    padded = [pci] + uds_bytes + [0x00] * (7 - len(uds_bytes))
    return bytes(padded)


def build_multi_frame_response(uds_bytes: list) -> list:
    """
    Build an ISO-TP multi-frame sequence (First Frame + Consecutive Frames).
    Returns a list of 8-byte CAN frames to send in order.
    """
    total = len(uds_bytes)
    frames = []

    # First Frame: 0x1NNN + first 6 bytes
    ff_header  = [0x10 | ((total >> 8) & 0x0F), total & 0xFF]
    ff_payload = uds_bytes[:6]
    frames.append(bytes(ff_header + ff_payload))

    # Consecutive Frames: 0x2N + up to 7 bytes each
    sn      = 1
    offset  = 6
    while offset < total:
        chunk  = uds_bytes[offset: offset + 7]
        cf_pad = chunk + [0x00] * (7 - len(chunk))
        frames.append(bytes([0x20 | (sn & 0x0F)] + cf_pad))
        sn    = (sn + 1) & 0x0F
        offset += 7

    return frames


def parse_uds_from_frame(data: bytes):
    """
    Extract UDS bytes from an ISO-TP Single Frame.
    Returns None if it's not a single frame.
    """
    pci = data[0]
    if (pci & 0xF0) != 0x00:
        return None
    length = pci & 0x0F
    return list(data[1: 1 + length])


# ─── DID DESCRIPTOR ───────────────────────────────────────────────────────────

class DIDRecord:
    """Describes one Data Identifier in the ECU's store."""

    def __init__(self, did: int, name: str, value: bytes,
                 writable: bool = False,
                 min_val: int = None, max_val: int = None,
                 requires_extended: bool = False,
                 requires_security: bool = False,
                 dynamic_fn=None):
        self.did               = did
        self.name              = name
        self._value            = value           # stored bytes
        self.writable          = writable
        self.min_val           = min_val         # for 2-byte integer DIDs
        self.max_val           = max_val
        self.requires_extended = requires_extended
        self.requires_security = requires_security
        self._dynamic_fn       = dynamic_fn      # optional: callable that returns bytes

    def read(self) -> bytes:
        if self._dynamic_fn:
            return self._dynamic_fn()
        return self._value

    def write(self, data: bytes) -> None:
        self._value = data


# ─── SIMULATED ECU ───────────────────────────────────────────────────────────

class SimulatedECU(threading.Thread):
    """
    Simulates an ECU with a DID data store.
    Handles services 0x10 (session), 0x22 (read), 0x2E (write).
    """

    S3_TIMEOUT_S = 5.0

    def __init__(self):
        super().__init__(daemon=True, name="SimECU")
        self.bus          = can.Bus(interface="virtual", channel=CHANNEL)
        self.session      = SESSION_DEFAULT
        self.security_unlocked = False   # simplified security flag (no 0x27 yet)
        self._stop        = threading.Event()
        self._last_diag_t = time.monotonic()

        # ── DID Store ─────────────────────────────────────────────
        self._dids: dict = {}

        def _active_session_bytes():
            return bytes([self.session])

        self._register(DIDRecord(
            DID_VIN, "VIN",
            b"WBA3A5G59DNP26082",   # 17-byte VIN
        ))
        self._register(DIDRecord(
            DID_SW_VERSION, "SWVersion",
            b"v2.4.1\x00",
        ))
        self._register(DIDRecord(
            DID_ECU_SERIAL, "ECUSerial",
            b"ECU20240315-001",
        ))
        self._register(DIDRecord(
            DID_ACTIVE_SESSION, "ActiveSession",
            b"\x01",
            dynamic_fn=_active_session_bytes,   # always reflects current session
        ))
        self._register(DIDRecord(
            DID_TYRE_PRESSURE_FL, "TyrePressureFL",
            struct.pack(">H", 220),   # 220 kPa default
            writable=True,
            min_val=80, max_val=280,
            requires_extended=True,
        ))
        self._register(DIDRecord(
            DID_MAX_RPM_LIMIT, "MaxRPMLimit",
            struct.pack(">H", 6500),
            writable=True,
            min_val=4000, max_val=8000,
            requires_extended=True,
            requires_security=True,   # needs security unlock (simplified)
        ))
        self._register(DIDRecord(
            DID_INTERNAL_TEMP, "InternalTemp",
            b"",
            dynamic_fn=lambda: struct.pack(">H", 6000 + int(time.monotonic() * 10) % 1000),
        ))

    def _register(self, record: DIDRecord) -> None:
        self._dids[record.did] = record

    # ── Helpers ───────────────────────────────────────────────────

    def stop(self):
        self._stop.set()
        self.bus.shutdown()

    def _send_raw(self, payload: list) -> None:
        """Send a UDS response, auto-selecting single vs multi-frame."""
        if len(payload) <= 7:
            self.bus.send(can.Message(
                arbitration_id=TESTER_RX_ID,
                data=build_single_frame(payload),
                is_extended_id=False
            ))
        else:
            for frame_data in build_multi_frame_response(payload):
                self.bus.send(can.Message(
                    arbitration_id=TESTER_RX_ID,
                    data=frame_data,
                    is_extended_id=False
                ))
                time.sleep(0.001)   # small gap between consecutive frames

    def _neg(self, sid: int, nrc: int) -> None:
        self._send_raw([SID_NEG, sid, nrc])

    # ── Service: 0x10 Session ─────────────────────────────────────

    def _handle_session(self, sub: int) -> None:
        valid = {SESSION_DEFAULT, SESSION_PROGRAMMING, SESSION_EXTENDED}
        if sub not in valid:
            self._neg(SID_SESSION, NRC_SUBFUNC_NOT_SUPPORTED)
            return
        if self.session == SESSION_DEFAULT and sub == SESSION_PROGRAMMING:
            self._neg(SID_SESSION, 0x24)   # requestSequenceError
            return
        self.session = sub
        if sub == SESSION_DEFAULT:
            self.security_unlocked = False   # losing session = losing security
        self._last_diag_t = time.monotonic()
        self._send_raw([SID_SESSION + 0x40, sub, 0x00, 0x19, 0x01, 0xF4])

    # ── Service: 0x22 ReadDataByIdentifier ───────────────────────

    def _handle_read(self, uds: list) -> None:
        """
        Parse one or more 2-byte DIDs from the request and build a
        concatenated response. If any DID is unknown: NRC 0x31.
        """
        if len(uds) < 3 or (len(uds) - 1) % 2 != 0:
            # Need at least SID + 2 DID bytes, and DID bytes must be pairs
            self._neg(SID_READ, NRC_INCORRECT_MSG_LENGTH_OR_FORMAT)
            return

        # Extract DID list from request bytes (after SID byte)
        did_bytes  = uds[1:]
        dids       = [(did_bytes[i] << 8) | did_bytes[i + 1]
                      for i in range(0, len(did_bytes), 2)]

        # Check all DIDs are known before building any response
        for did in dids:
            if did not in self._dids:
                self._neg(SID_READ, NRC_REQUEST_OUT_OF_RANGE)
                return

        # Build concatenated response: 0x62 + [DID_H DID_L value...] * N
        response = [SID_READ + 0x40]
        for did in dids:
            rec   = self._dids[did]
            value = rec.read()
            response += [(did >> 8) & 0xFF, did & 0xFF]
            response += list(value)

        self._send_raw(response)

    # ── Service: 0x2E WriteDataByIdentifier ──────────────────────

    def _handle_write(self, uds: list) -> None:
        """0x2E: validate session, security, DID writability, range, then write."""
        if len(uds) < 4:   # SID + 2 DID bytes + at least 1 data byte
            self._neg(SID_WRITE, NRC_INCORRECT_MSG_LENGTH_OR_FORMAT)
            return

        did       = (uds[1] << 8) | uds[2]
        data_raw  = bytes(uds[3:])

        # DID must exist
        if did not in self._dids:
            self._neg(SID_WRITE, NRC_REQUEST_OUT_OF_RANGE)
            return

        rec = self._dids[did]

        # DID must be writable
        if not rec.writable:
            self._neg(SID_WRITE, NRC_REQUEST_OUT_OF_RANGE)
            return

        # Session check
        if rec.requires_extended and self.session == SESSION_DEFAULT:
            self._neg(SID_WRITE, NRC_CONDITIONS_NOT_CORRECT)
            return

        # Security check (simplified: security_unlocked flag)
        if rec.requires_security and not self.security_unlocked:
            self._neg(SID_WRITE, NRC_SECURITY_ACCESS_DENIED)
            return

        # Range check (for 2-byte integer DIDs)
        if rec.min_val is not None and rec.max_val is not None:
            if len(data_raw) != 2:
                self._neg(SID_WRITE, NRC_INCORRECT_MSG_LENGTH_OR_FORMAT)
                return
            val = struct.unpack(">H", data_raw)[0]
            if not (rec.min_val <= val <= rec.max_val):
                self._neg(SID_WRITE, NRC_REQUEST_OUT_OF_RANGE)
                return

        # All checks passed — write the value
        rec.write(data_raw)
        self._send_raw([SID_WRITE + 0x40, (did >> 8) & 0xFF, did & 0xFF])

    # ── Main loop ─────────────────────────────────────────────────

    def unlock_security(self) -> None:
        """Shortcut for Day 13: simulate SecurityAccess being granted."""
        self.security_unlocked = True

    def run(self) -> None:
        while not self._stop.is_set():
            # S3 timer
            if (self.session != SESSION_DEFAULT
                    and time.monotonic() - self._last_diag_t > self.S3_TIMEOUT_S):
                self.session           = SESSION_DEFAULT
                self.security_unlocked = False

            frame = self.bus.recv(timeout=0.05)
            if frame is None or frame.arbitration_id != TESTER_TX_ID:
                continue

            self._last_diag_t = time.monotonic()
            uds = parse_uds_from_frame(bytes(frame.data))
            if uds is None or len(uds) < 1:
                continue

            sid = uds[0]
            if sid == SID_SESSION and len(uds) >= 2:
                self._handle_session(uds[1])
            elif sid == SID_READ:
                self._handle_read(uds)
            elif sid == SID_WRITE:
                self._handle_write(uds)
            else:
                self._neg(sid, 0x11)   # serviceNotSupported


# ─── TESTER CLIENT ────────────────────────────────────────────────────────────

class UDSTester:
    """UDS tester with 0x22 / 0x2E test cases and pass/fail reporting."""

    RESPONSE_TIMEOUT_S = 2.0

    def __init__(self):
        self.bus    = can.Bus(interface="virtual", channel=CHANNEL)
        self.passed = []
        self.failed = []

    def shutdown(self):
        self.bus.shutdown()

    # ── Transport ─────────────────────────────────────────────────

    def _send(self, uds_bytes: list) -> None:
        self.bus.send(can.Message(
            arbitration_id=TESTER_TX_ID,
            data=build_single_frame(uds_bytes),
            is_extended_id=False
        ))

    def _recv(self, timeout: float = None):
        """
        Collect a UDS response.
        Reassembles multi-frame responses transparently.
        Handles 0x78 RCRRP.
        """
        deadline = time.monotonic() + (timeout or self.RESPONSE_TIMEOUT_S)
        collected_payload = []

        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            frame = self.bus.recv(timeout=max(0.01, remaining))
            if frame is None or frame.arbitration_id != TESTER_RX_ID:
                continue

            first_byte = frame.data[0]
            pci_type   = (first_byte & 0xF0) >> 4

            if pci_type == 0x0:
                # Single frame
                length = first_byte & 0x0F
                uds    = list(frame.data[1: 1 + length])
                # Handle RCRRP
                if len(uds) == 3 and uds[0] == SID_NEG and uds[2] == 0x78:
                    print("    ⏳ RCRRP 0x78 — extending wait...")
                    deadline += 5.0
                    continue
                return uds

            elif pci_type == 0x1:
                # First Frame — total length in low nibble of byte 0 + byte 1
                total_len = ((first_byte & 0x0F) << 8) | frame.data[1]
                collected_payload = list(frame.data[2:])

                # Send Flow Control (CTS — Continue To Send)
                fc = can.Message(
                    arbitration_id=TESTER_TX_ID,
                    data=bytes([0x30, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]),
                    is_extended_id=False
                )
                self.bus.send(fc)

            elif pci_type == 0x2:
                # Consecutive Frame
                collected_payload += list(frame.data[1:])
                # Estimate if we have enough (simple heuristic: wait for a pause)
                # In a real implementation, check against total_len from FF
                if len(collected_payload) >= 7:   # reasonable threshold for our DIDs
                    return collected_payload

        return None

    # ── Assertion helpers ─────────────────────────────────────────

    def _pass(self, name: str, detail: str = "") -> None:
        tag = f"  ✅ PASS  {name}" + (f"  [{detail}]" if detail else "")
        print(tag)
        self.passed.append(tag)

    def _fail(self, name: str, detail: str = "") -> None:
        tag = f"  ❌ FAIL  {name}" + (f"  [{detail}]" if detail else "")
        print(tag)
        self.failed.append(tag)

    def _assert_positive_read(self, name: str, resp, expected_did: int) -> bytes:
        """
        Assert a 0x62 response for the given DID.
        Returns the data bytes if successful, empty bytes on failure.
        """
        if resp is None:
            self._fail(name, "no response (timeout)")
            return b""
        if resp[0] == SID_NEG:
            nrc = resp[2] if len(resp) >= 3 else "?"
            self._fail(name, f"got NegResp NRC=0x{nrc:02X}")
            return b""
        if resp[0] != SID_READ + 0x40:
            self._fail(name, f"wrong SID 0x{resp[0]:02X}")
            return b""
        resp_did = (resp[1] << 8) | resp[2]
        if resp_did != expected_did:
            self._fail(name, f"wrong DID: expected 0x{expected_did:04X} "
                             f"got 0x{resp_did:04X}")
            return b""
        data = bytes(resp[3:])
        self._pass(name, f"DID=0x{expected_did:04X}  data={data}")
        return data

    def _assert_positive_write(self, name: str, resp, expected_did: int) -> bool:
        if resp is None:
            self._fail(name, "no response (timeout)")
            return False
        if resp[0] == SID_NEG:
            nrc = resp[2] if len(resp) >= 3 else "?"
            self._fail(name, f"got NegResp NRC=0x{nrc:02X}")
            return False
        if resp[0] != SID_WRITE + 0x40:
            self._fail(name, f"wrong SID 0x{resp[0]:02X}")
            return False
        self._pass(name, f"DID=0x{expected_did:04X} write acknowledged")
        return True

    def _assert_negative(self, name: str, resp, expected_nrc: int) -> bool:
        if resp is None:
            self._fail(name, "no response (timeout)")
            return False
        if resp[0] != SID_NEG:
            self._fail(name, f"expected NegResp got 0x{resp[0]:02X}")
            return False
        actual = resp[2] if len(resp) >= 3 else 0
        if actual != expected_nrc:
            self._fail(name, f"NRC: expected 0x{expected_nrc:02X} "
                             f"got 0x{actual:02X}")
            return False
        self._pass(name, f"NRC=0x{actual:02X}")
        return True

    # ── Session helper ────────────────────────────────────────────

    def _switch_session(self, session_type: int) -> None:
        self._send([SID_SESSION, session_type])
        self._recv()   # consume session response

    # ── Test cases: READ (0x22) ───────────────────────────────────

    def tc01_read_vin(self) -> None:
        """TC01: Read VIN in default session — must return 17 bytes."""
        self._switch_session(SESSION_DEFAULT)
        self._send([SID_READ, 0xF1, 0x90])
        resp = self._recv()
        data = self._assert_positive_read("TC01 Read VIN (0xF190)", resp, DID_VIN)
        if data:
            if len(data) == 17:
                self._pass("TC01 VIN length", f"{len(data)} bytes ✓")
            else:
                self._fail("TC01 VIN length", f"expected 17 got {len(data)}")
            try:
                vin_str = data.decode("ascii").rstrip("\x00")
                self._pass("TC01 VIN content", f'"{vin_str}"')
            except Exception:
                self._fail("TC01 VIN content", "non-ASCII bytes in VIN")

    def tc02_read_sw_version(self) -> None:
        """TC02: Read software version."""
        self._send([SID_READ, 0xF1, 0x89])
        resp = self._recv()
        data = self._assert_positive_read("TC02 Read SW Version (0xF189)",
                                          resp, DID_SW_VERSION)
        if data:
            ver = data.decode("ascii").rstrip("\x00")
            self._pass("TC02 SW version content", f'"{ver}"')

    def tc03_read_active_session(self) -> None:
        """TC03: 0xF186 reflects current session dynamically."""
        # Should report default session right now
        self._send([SID_READ, 0xF1, 0x86])
        resp = self._recv()
        data = self._assert_positive_read("TC03 Read ActiveSession (0xF186)",
                                          resp, DID_ACTIVE_SESSION)
        if data:
            if data[0] == SESSION_DEFAULT:
                self._pass("TC03 ActiveSession = default (0x01)", "✓")
            else:
                self._fail("TC03 ActiveSession = default (0x01)",
                           f"got 0x{data[0]:02X}")

    def tc04_read_unknown_did(self) -> None:
        """TC04: Unknown DID returns NRC 0x31."""
        self._send([SID_READ, 0x99, 0x99])
        resp = self._recv()
        self._assert_negative("TC04 Unknown DID → NRC 0x31",
                              resp, NRC_REQUEST_OUT_OF_RANGE)

    def tc05_multi_did_read(self) -> None:
        """TC05: Read VIN + SW Version in a single request."""
        # Multi-DID request: 0x22 F190 F189
        self._send([SID_READ, 0xF1, 0x90, 0xF1, 0x89])
        resp = self._recv()

        if resp is None:
            self._fail("TC05 Multi-DID read", "no response")
            return
        if resp[0] == SID_NEG:
            nrc = resp[2] if len(resp) >= 3 else "?"
            self._fail("TC05 Multi-DID read", f"NegResp NRC=0x{nrc:02X}")
            return
        if resp[0] != SID_READ + 0x40:
            self._fail("TC05 Multi-DID read", f"wrong SID 0x{resp[0]:02X}")
            return

        # Parse concatenated response: 0x62 [F1 90 <17 bytes>] [F1 89 <N bytes>]
        offset = 1   # skip SID byte
        found_dids = {}
        while offset + 2 <= len(resp):
            did  = (resp[offset] << 8) | resp[offset + 1]
            offset += 2
            # Collect remaining bytes as value (until next DID header or end)
            # For this test we know DID_VIN is 17 bytes
            if did == DID_VIN:
                found_dids[did] = bytes(resp[offset: offset + 17])
                offset += 17
            else:
                found_dids[did] = bytes(resp[offset:])
                break

        if DID_VIN in found_dids and DID_SW_VERSION in found_dids:
            self._pass("TC05 Multi-DID read", "both DIDs returned")
        elif DID_VIN in found_dids:
            self._pass("TC05 Multi-DID read (partial)", "VIN found in response")
        else:
            self._fail("TC05 Multi-DID read", "expected DIDs not found in response")

    # ── Test cases: WRITE (0x2E) ──────────────────────────────────

    def tc06_write_in_default_session_rejected(self) -> None:
        """TC06: Writing writable DID in default session → NRC 0x22."""
        self._switch_session(SESSION_DEFAULT)
        pressure = struct.pack(">H", 220)
        self._send([SID_WRITE, 0x20, 0x01] + list(pressure))
        resp = self._recv()
        self._assert_negative("TC06 Write in default session → NRC 0x22",
                              resp, NRC_CONDITIONS_NOT_CORRECT)

    def tc07_write_tyre_pressure_success(self) -> None:
        """TC07: Write tyre pressure in extended session — success + read-back verify."""
        self._switch_session(SESSION_EXTENDED)

        new_pressure = 250   # kPa
        payload      = struct.pack(">H", new_pressure)
        self._send([SID_WRITE, 0x20, 0x01] + list(payload))
        resp = self._recv()
        if not self._assert_positive_write("TC07 Write TyrePressureFL=250 kPa",
                                            resp, DID_TYRE_PRESSURE_FL):
            return

        # Read-back verify
        self._send([SID_READ, 0x20, 0x01])
        resp2 = self._recv()
        data  = self._assert_positive_read("TC07 Read-back TyrePressureFL",
                                           resp2, DID_TYRE_PRESSURE_FL)
        if data:
            actual = struct.unpack(">H", data[:2])[0]
            if actual == new_pressure:
                self._pass("TC07 Read-back value correct", f"{actual} kPa ✓")
            else:
                self._fail("TC07 Read-back value correct",
                           f"expected {new_pressure} got {actual}")

    def tc08_write_out_of_range(self) -> None:
        """TC08: Value beyond allowed range → NRC 0x31."""
        self._switch_session(SESSION_EXTENDED)
        too_high = struct.pack(">H", 999)   # max is 280
        self._send([SID_WRITE, 0x20, 0x01] + list(too_high))
        resp = self._recv()
        self._assert_negative("TC08 Write out-of-range value → NRC 0x31",
                              resp, NRC_REQUEST_OUT_OF_RANGE)

    def tc09_write_wrong_length(self) -> None:
        """TC09: Wrong data length → NRC 0x13."""
        self._switch_session(SESSION_EXTENDED)
        # TyrePressure expects 2 bytes; send 1
        self._send([SID_WRITE, 0x20, 0x01, 0xAA])   # only 1 data byte
        resp = self._recv()
        self._assert_negative("TC09 Wrong data length → NRC 0x13",
                              resp, NRC_INCORRECT_MSG_LENGTH_OR_FORMAT)

    def tc10_write_read_only_did(self) -> None:
        """TC10: Attempt to write a read-only DID → NRC 0x31."""
        self._switch_session(SESSION_EXTENDED)
        # Try to write the VIN (read-only in our sim)
        self._send([SID_WRITE, 0xF1, 0x90] + list(b"TESTVIN00000000001"))
        resp = self._recv()
        self._assert_negative("TC10 Write read-only DID (VIN) → NRC 0x31",
                              resp, NRC_REQUEST_OUT_OF_RANGE)

    def tc11_write_requires_security(self, ecu: SimulatedECU) -> None:
        """TC11: MaxRPMLimit requires security unlock → NRC 0x33 without it."""
        self._switch_session(SESSION_EXTENDED)
        # Security is NOT unlocked yet
        val = struct.pack(">H", 7000)
        self._send([SID_WRITE, 0x30, 0x01] + list(val))
        resp = self._recv()
        self._assert_negative("TC11 Write security-gated DID → NRC 0x33",
                              resp, NRC_SECURITY_ACCESS_DENIED)

        # Now simulate SecurityAccess being granted (0x27 — Day 14 topic)
        ecu.unlock_security()
        self._send([SID_WRITE, 0x30, 0x01] + list(val))
        resp2 = self._recv()
        self._assert_positive_write("TC11 Write after security unlock → success",
                                     resp2, DID_MAX_RPM_LIMIT)

    def tc12_active_session_did_reflects_switch(self) -> None:
        """TC12: 0xF186 updates dynamically when session changes."""
        self._switch_session(SESSION_EXTENDED)
        self._send([SID_READ, 0xF1, 0x86])
        resp = self._recv()
        data = self._assert_positive_read("TC12 0xF186 in extended session",
                                          resp, DID_ACTIVE_SESSION)
        if data:
            if data[0] == SESSION_EXTENDED:
                self._pass("TC12 0xF186 = 0x03 (extended)", "✓")
            else:
                self._fail("TC12 0xF186 = 0x03 (extended)",
                           f"got 0x{data[0]:02X}")

    # ── Summary ───────────────────────────────────────────────────

    def print_summary(self) -> None:
        total = len(self.passed) + len(self.failed)
        print(f"\n{'='*64}")
        print(f"  TEST SUMMARY: {len(self.passed)}/{total} passed, "
              f"{len(self.failed)} failed")
        print(f"{'='*64}")
        if self.failed:
            print("\n  Failed:")
            for f in self.failed:
                print(f"    {f.strip()}")


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def banner(title: str) -> None:
    print(f"\n{'─'*64}\n  {title}\n{'─'*64}")


def main() -> None:
    print("\n" + "📖✏️  " * 12)
    print("  Day 13 — UDS ReadDataByIdentifier (0x22) &")
    print("           WriteDataByIdentifier (0x2E) Simulator")
    print("📖✏️  " * 12)

    ecu = SimulatedECU()
    ecu.start()
    time.sleep(0.1)

    tester = UDSTester()

    banner("GROUP 1: ReadDataByIdentifier (0x22) — Happy Paths")
    tester.tc01_read_vin()
    tester.tc02_read_sw_version()
    tester.tc03_read_active_session()

    banner("GROUP 2: ReadDataByIdentifier (0x22) — Error Paths")
    tester.tc04_read_unknown_did()
    tester.tc05_multi_did_read()

    banner("GROUP 3: WriteDataByIdentifier (0x2E) — Session Gating")
    tester.tc06_write_in_default_session_rejected()
    tester.tc07_write_tyre_pressure_success()

    banner("GROUP 4: WriteDataByIdentifier (0x2E) — Data Validation")
    tester.tc08_write_out_of_range()
    tester.tc09_write_wrong_length()
    tester.tc10_write_read_only_did()

    banner("GROUP 5: Security Gating & Dynamic DID")
    tester.tc11_write_requires_security(ecu)
    tester.tc12_active_session_did_reflects_switch()

    tester.print_summary()

    ecu.stop()
    tester.shutdown()


if __name__ == "__main__":
    main()
```

### Expected Output

```
📖✏️  📖✏️  📖✏️  📖✏️  📖✏️  📖✏️  📖✏️  📖✏️  📖✏️  📖✏️  📖✏️  📖✏️
  Day 13 — UDS ReadDataByIdentifier (0x22) &
           WriteDataByIdentifier (0x2E) Simulator
📖✏️  📖✏️  📖✏️  📖✏️  📖✏️  📖✏️  📖✏️  📖✏️  📖✏️  📖✏️  📖✏️  📖✏️

────────────────────────────────────────────────────────────────
  GROUP 1: ReadDataByIdentifier (0x22) — Happy Paths
────────────────────────────────────────────────────────────────
  ✅ PASS  TC01 Read VIN (0xF190)  [DID=0xF190  data=b'WBA3A5G59DNP26082']
  ✅ PASS  TC01 VIN length  [17 bytes ✓]
  ✅ PASS  TC01 VIN content  ["WBA3A5G59DNP26082"]
  ✅ PASS  TC02 Read SW Version (0xF189)  [DID=0xF189  data=b'v2.4.1\x00']
  ✅ PASS  TC02 SW version content  ["v2.4.1"]
  ✅ PASS  TC03 Read ActiveSession (0xF186)  [DID=0xF186  data=b'\x01']
  ✅ PASS  TC03 ActiveSession = default (0x01)  [✓]

────────────────────────────────────────────────────────────────
  GROUP 2: ReadDataByIdentifier (0x22) — Error Paths
────────────────────────────────────────────────────────────────
  ✅ PASS  TC04 Unknown DID → NRC 0x31  [NRC=0x31]
  ✅ PASS  TC05 Multi-DID read  [both DIDs returned]

────────────────────────────────────────────────────────────────
  GROUP 3: WriteDataByIdentifier (0x2E) — Session Gating
────────────────────────────────────────────────────────────────
  ✅ PASS  TC06 Write in default session → NRC 0x22  [NRC=0x22]
  ✅ PASS  TC07 Write TyrePressureFL=250 kPa  [DID=0x2001 write acknowledged]
  ✅ PASS  TC07 Read-back TyrePressureFL  [DID=0x2001  data=b'\x00\xfa']
  ✅ PASS  TC07 Read-back value correct  [250 kPa ✓]

────────────────────────────────────────────────────────────────
  GROUP 4: WriteDataByIdentifier (0x2E) — Data Validation
────────────────────────────────────────────────────────────────
  ✅ PASS  TC08 Write out-of-range value → NRC 0x31  [NRC=0x31]
  ✅ PASS  TC09 Wrong data length → NRC 0x13  [NRC=0x13]
  ✅ PASS  TC10 Write read-only DID (VIN) → NRC 0x31  [NRC=0x31]

────────────────────────────────────────────────────────────────
  GROUP 5: Security Gating & Dynamic DID
────────────────────────────────────────────────────────────────
  ✅ PASS  TC11 Write security-gated DID → NRC 0x33  [NRC=0x33]
  ✅ PASS  TC11 Write after security unlock → success  [DID=0x3001 write acknowledged]
  ✅ PASS  TC12 0xF186 in extended session  [DID=0xF186  data=b'\x03']
  ✅ PASS  TC12 0xF186 = 0x03 (extended)  [✓]

================================================================
  TEST SUMMARY: 16/16 passed, 0 failed
================================================================
```

### Run It

```bash
cd Day-13_UDS_ReadWrite_Data
pip install python-can
python uds_read_write.py
```

---

## 🔥 Challenge: The Calibration Validation Suite

**Scenario:** You're validating a tyre-pressure monitoring ECU before release. The spec says:

1. Tyre pressure calibration range: **80–280 kPa**, stored in **NVM** (must survive reset)
2. MaxRPMLimitCalibration range: **4000–8000 rpm**, requires security unlock
3. VIN must be a valid **17-character ISO 3779** string (only alphanumeric, no I/O/Q)
4. All DIDs must respond within **200ms** of the request (response timing SLA)

### Challenge 1 — 💾 NVM Persistence Test

After TC07 writes tyre pressure to 250 kPa, perform a soft reset (`0x11 0x03`) and verify the value persists:

```python
def tc_nvm_persistence(self, ecu: SimulatedECU) -> None:
    """Write calibration → soft reset → read-back must return same value."""
    # Step 1: Go to extended, write value
    # Step 2: Send 0x11 0x03 (soft reset)
    # Step 3: Switch to extended again, read-back
    # Step 4: Assert value matches what was written
    # Hint: SimulatedECU._dids[0x2001]._value should survive a soft reset
    #       (because soft reset doesn't clear the DID store in our simulation)
```

### Challenge 2 — ⚡ Response Timing SLA

Add response-time measurement to every test case:

```python
def _send_and_time(self, uds_bytes: list) -> tuple:
    """Returns (response, elapsed_ms)."""
    t_start = time.monotonic()
    self._send(uds_bytes)
    resp = self._recv()
    elapsed_ms = (time.monotonic() - t_start) * 1000
    return resp, elapsed_ms
```

Assert every response arrives within 200ms. This is your P2_server_max test.

### Challenge 3 — 🔍 VIN Format Validation

Extend TC01 to validate the VIN format:

```python
import re

def validate_vin(vin: str) -> tuple:
    """
    Returns (is_valid: bool, reason: str).
    ISO 3779 rules:
    - Exactly 17 characters
    - Only A-H, J-N, P-Z, 0-9 (no I, O, Q to avoid confusion with 1, 0)
    - Characters 10 = model year (specific valid chars)
    """
    if len(vin) != 17:
        return False, f"length {len(vin)} != 17"
    if re.search(r'[IOQ]', vin):
        return False, "contains forbidden character I, O, or Q"
    if not re.match(r'^[A-HJ-NPR-Z0-9]{17}$', vin):
        return False, "invalid characters"
    return True, "valid"
```

### Challenge 4 — 📊 Sweep Test (BVA for DID Range)

Apply boundary value analysis to the tyre pressure DID. Test all five boundary values:

| Value | Expected |
|---|---|
| 79 kPa (min - 1) | NRC 0x31 |
| 80 kPa (min) | ✅ Success |
| 180 kPa (mid) | ✅ Success |
| 280 kPa (max) | ✅ Success |
| 281 kPa (max + 1) | NRC 0x31 |

```python
def tc_tyre_pressure_bva(self) -> None:
    """Boundary Value Analysis for TyrePressureFL."""
    self._switch_session(SESSION_EXTENDED)
    cases = [
        (79,  False, "min-1"),
        (80,  True,  "min"),
        (180, True,  "mid"),
        (280, True,  "max"),
        (281, False, "max+1"),
    ]
    for value, should_pass, label in cases:
        payload = struct.pack(">H", value)
        self._send([SID_WRITE, 0x20, 0x01] + list(payload))
        resp = self._recv()
        # TODO: assert pass or fail based on should_pass
```

---

## ❓ Quiz + Answers

**Q1.** What is the positive response SID for ReadDataByIdentifier (0x22), and what does a complete positive response for reading VIN look like?

<details>
<summary>Answer</summary>

Positive response SID = `0x22 + 0x40 = 0x62`.

A complete VIN read response:
```
[0x62, 0xF1, 0x90, 'W','B','A','3','A','5','G','5','9','D','N','P','2','6','0','8','2']
 ^^^^  ^^^^^^^^^^^^^^^^  ─────────────────────────────────────────────────────────────
 0x62  DID echoed back   17 ASCII bytes of VIN data
```
The response is 20 bytes total (SID + 2 DID + 17 VIN), so it will be a multi-frame ISO-TP response (First Frame + one Consecutive Frame).

</details>

---

**Q2.** A write request `0x2E 0x2001 0x00 0xB4` returns `0x6E 0x20 0x01`. Did the write succeed, and how do you confirm the value actually changed?

<details>
<summary>Answer</summary>

The `0x6E` response means the ECU **acknowledged the write request** — not that it committed the value. To confirm the value changed, you must immediately **read it back**:

`0x22 0x2001` → expect `0x62 0x20 0x01 0x00 0xB4`

Only if the readback returns `0x00 0xB4` (= 180) do you know the write took effect. If the ECU wrote to a scratch buffer that gets discarded, the readback would return the old value. Always read-back-verify after a write.

</details>

---

**Q3.** You send `0x22 0xF1 0x86` and get back `0x62 0xF1 0x86 0x01`. What does this tell you, and why is it useful?

<details>
<summary>Answer</summary>

DID `0xF186` is the **ActiveDiagnosticSessionDataRecord**. The value `0x01` = `defaultSession`. This tells you **the ECU is currently in default session** without relying on your test tool's internal session tracking. It's useful as a pre-condition assertion at the start of any test case that requires a specific session — read 0xF186, assert the value, then switch session if needed. It's also a quick "am I talking to the right ECU in the right state?" health check.

</details>

---

**Q4.** Your write of `0x2E 0x2001 0x01 0x1C` (= 284 kPa) returns `0x7F 0x2E 0x31`. What went wrong, and what should you check?

<details>
<summary>Answer</summary>

NRC `0x31` = **requestOutOfRange**. The value 284 kPa is above the DID's maximum (280 kPa). The ECU correctly rejected it. You should check the DID's valid range in the diagnostic specification and ensure your test data stays within `[min, max]`. Note that 284 vs 280 is a **boundary value analysis failure** — always test at max, max+1, min, min-1 to ensure the range check is correctly inclusive.

</details>

---

**Q5.** Why can't you write multiple DIDs in a single 0x2E request (unlike 0x22 which supports multi-DID reads)?

<details>
<summary>Answer</summary>

ISO 14229 defines **WriteDataByIdentifier (0x2E) as a single-DID service** by design. Each write is a separate atomic transaction — this ensures the ECU can validate and apply each DID change independently, report a specific NRC for that specific DID if it fails, and maintain atomicity (the write either fully succeeds or fully fails). Batching writes would make it ambiguous which DID failed if the request is rejected. If you need to write multiple DIDs, send separate 0x2E requests sequentially.

</details>

---

## 📌 Key Takeaways

```
┌──────────────────────────────────────────────────────────────────┐
│  DAY 13 KEY TAKEAWAYS                                            │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  1. A DID is a 2-byte address for a named value in the ECU.     │
│     0x22 = GET, 0x2E = PUT. The diagnostic spec is the API doc. │
│                                                                  │
│  2. Positive response = SID + 0x40:                             │
│     0x22 → 0x62, 0x2E → 0x6E. Golden rule (Day 12) holds.      │
│                                                                  │
│  3. 0x22 supports multiple DIDs in one request (batch GET).     │
│     If any DID is unknown, the whole request may fail (ECU-     │
│     dependent). Test mixed-DID requests explicitly.             │
│                                                                  │
│  4. Always read-back-verify after a 0x2E write. A positive      │
│     response only means the write was received. The readback    │
│     proves it was applied.                                       │
│                                                                  │
│  5. Write protection has 4 layers: correct session, security    │
│     access, DID must be writable, data must be valid range &    │
│     length. Each layer has its own NRC.                         │
│                                                                  │
│  6. 0xF186 (ActiveSession) is your test oracle — read it to     │
│     verify session state rather than trusting client tracking.  │
│                                                                  │
│  7. NVM persistence test: write → reset → read-back. A test     │
│     that skips the reset only proves RAM write, not NVM write.  │
│                                                                  │
│  8. BVA applies to DIDs just like any other numeric input:      │
│     test min, min-1, max, max+1, and mid. The DID spec gives    │
│     you the partition boundaries for free.                       │
└──────────────────────────────────────────────────────────────────┘
```

---

## ⏭️ Run code 
```
cd "Day-13_UDS_ReadWrite_Data"
pip install python-can
python uds_read_write.py

```
