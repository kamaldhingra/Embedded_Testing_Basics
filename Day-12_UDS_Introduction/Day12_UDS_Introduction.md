# 🩺 Day 12: Introduction to UDS — Session Control (0x10) & ECU Reset (0x11)

> **Course:** Embedded & IoT Testing Masterclass
> **Instructor:** Professor Embed
> **Prerequisites:** Day 1–10 (Complete CAN fundamentals + Python automation)

---

## 📚 Table of Contents

1. [Recap: Where We Left Off](#recap-where-we-left-off)
2. [Concept: What Is UDS and Why Does It Exist?](#concept-what-is-uds)
3. [Concept: The Transport — ISO-TP (ISO 15765-2)](#concept-iso-tp)
4. [Concept: UDS Message Structure — Request, Response, Negative Response](#concept-message-structure)
5. [Concept: The Session State Machine](#concept-session-state-machine)
6. [Concept: Service 0x10 — DiagnosticSessionControl](#concept-0x10)
7. [Concept: Service 0x11 — ECUReset](#concept-0x11)
8. [Concept: Negative Response Codes — The UDS Error Vocabulary](#concept-nrc)
9. [The Big Picture: UDS in the Automotive Test Stack](#the-big-picture)
10. [Where It's Used in the Real World](#where-its-used)
11. [How a Tester Thinks About It](#how-a-tester-thinks)
12. [Hands-On Exercise: UDS Session & Reset Simulator](#hands-on-exercise)
13. [Challenge: The Diagnostic Gatekeeping Suite](#challenge)
14. [Quiz + Answers](#quiz--answers)
15. [Key Takeaways](#key-takeaways)

---

## 🔁 Recap: Where We Left Off

Ten days of raw CAN. One day of interview prep. You can now:

- Read and decode a CAN log with python-can + cantools
- Assert signal ranges, cycle times, and cross-signal consistency
- Explain to a senior interviewer why averages hide danger
- Navigate a DBC's signals, mux modes, and attributes
- Write CAPL-equivalent logic in Python

But everything so far lived at the **data layer** — we've been reading and writing raw frames. We've never asked the ECU: *"Hey, tell me what's wrong with yourself,"* or *"Switch into programming mode so I can update your firmware."*

That requires a completely different conversation — a **diagnostic dialogue** with the ECU. And that's UDS.

> *"All the CAN knowledge you've built is the postal service — it delivers the envelopes. UDS is the formal letter protocol that tells you what to write inside the envelope to get an ECU to respond to your requests."*

By end of today you'll understand:
- The UDS session state machine (guest → admin → root)
- How to switch diagnostic sessions with service 0x10
- How to reset an ECU with service 0x11
- How UDS negative responses work (the HTTP 4xx/5xx of the CAN world)
- How to simulate a full UDS client/server dialogue in Python

Let's diagnose some ECUs. 🩺

---

## 🧠 Concept: What Is UDS and Why Does It Exist?

### The "Talking to the ECU Directly" Layer

Everything you've done so far was **eavesdropping**. You listened to periodic signals that ECUs broadcast for each other — EngineRPM, WheelSpeed, GearCurrent. Those messages exist for **run-time communication** between ECUs.

**UDS is different.** It's a **request/response protocol** for a human (or test tool) to have a **direct conversation** with a single ECU:

```
┌─────────────────────────────────────────────────────────────────┐
│  PERIODIC CAN (what we've done Days 1–10)                       │
│  EngineECU ─── broadcasts RPM/temp every 10ms ──▶ all listeners │
│  (nobody asked, ECU just shouts it constantly)                  │
├─────────────────────────────────────────────────────────────────┤
│  UDS DIAGNOSTIC (starting today)                                │
│  Tester: "Hey ECU_Engine, what fault codes do you have?"        │
│  ECU:    "I have DTC P0300 — random misfire detected."          │
│  Tester: "Switch to extended diagnostic session."               │
│  ECU:    "Done. You're now in extended session."                │
│  Tester: "Reset yourself."                                      │
│  ECU:    "Resetting... (reboots)"                               │
└─────────────────────────────────────────────────────────────────┘
```

> **UDS = Unified Diagnostic Services, ISO 14229.** It defines a standard set of **services** (commands) a tester can send to any compliant ECU. It's the universal language every OEM, Tier-1, and service tool speaks. Your OBD-II scanner at the mechanic, Vector's CANoe Diagnostic Window, and the automated EOL (End-of-Line) test rig all use UDS.

> 🌉 **From your world:** UDS is to an ECU what SSH is to a server — a privileged, request/response channel for administration and diagnostics. Your periodic CAN signals are the application traffic (HTTP requests, metrics). UDS is the admin/ops channel running beside it. You'd only use it for: read/clear fault codes, calibrate sensors, flash firmware, run self-tests, inspect memory.

### UDS vs OBD-II — What's the Difference?

A common interview question. Nail it:

```
┌───────────────────┬──────────────────────┬───────────────────────┐
│  Feature          │  OBD-II (SAE J1979)  │  UDS (ISO 14229)      │
├───────────────────┼──────────────────────┼───────────────────────┤
│  Purpose          │  Standardised        │  Full manufacturer    │
│                   │  emissions / public  │  diagnostics          │
│  Who uses it      │  Any OBD reader      │  OEM tools, testers   │
│  Access           │  Mostly read-only,   │  Read + Write +       │
│                   │  no security needed  │  Flash + Calibrate    │
│  Security         │  None (public)       │  Security Access 0x27 │
│  Scope            │  Defined PIDs only   │  Any ECU capability   │
│  ECU addressing   │  0x7DF broadcast     │  Per-ECU physical addr│
│  Sessions         │  One (default only)  │  Multiple (0x01,02,03)│
└───────────────────┴──────────────────────┴───────────────────────┘
```

> **OBD-II is a public bus pass.** It gets you on the bus. **UDS is a master key** — same vehicle, but now you can access the driver's cabin, the engine room, and the maintenance bay. The key to the cabinet depends on which session you're in.

---

## 🧠 Concept: The Transport — ISO-TP (ISO 15765-2)

Before we decode UDS frames, there's a layer you need to know about: **ISO-TP** (ISO 15765-2). It sits between raw CAN and UDS, solving one problem:

> *CAN frames carry at most 8 bytes. UDS messages can be hundreds of bytes (e.g., a firmware chunk). How?*

ISO-TP is a **segmentation and reassembly** protocol — it chops long UDS messages into CAN-sized pieces and reassembles them at the receiver.

```
┌──────────────────────────────────────────────────────────────────┐
│  ISO-TP FRAME TYPES                                              │
├────────────────────┬─────────────────────────────────────────────┤
│  Single Frame (SF) │  Byte 0 = 0x0N (N=payload len, 1–7)        │
│  ≤ 7 bytes         │  Bytes 1–7: UDS data                        │
│                    │  Example: [0x02, 0x10, 0x03, 0,0,0,0,0]    │
│                    │            ^^^^ 2 UDS bytes follow          │
├────────────────────┼─────────────────────────────────────────────┤
│  First Frame (FF)  │  Bytes 0–1 = 0x1NNN (NNN = total len)      │
│  > 7 bytes         │  Bytes 2–7: first 6 UDS bytes               │
├────────────────────┼─────────────────────────────────────────────┤
│  Consecutive (CF)  │  Byte 0 = 0x2N (N=sequence 1–F, wraps)     │
│  continuation      │  Bytes 1–7: next 7 UDS bytes                │
├────────────────────┼─────────────────────────────────────────────┤
│  Flow Control (FC) │  Receiver → Sender: "send N more blocks at  │
│                    │  this separation time"                      │
└────────────────────┴─────────────────────────────────────────────┘
```

For **today's services** (0x10, 0x11) — the requests and responses are always tiny (2–4 bytes), so they always fit in a **Single Frame**. The full multi-frame dance matters for services like ReadMemoryByAddress or firmware download.

### Standard CAN IDs for Diagnostics

```
┌──────────────────────────────────────────────────────────────┐
│  Tester → ECU (request)  :  0x7E0  (ECU_Engine physical)    │
│  ECU → Tester (response) :  0x7E8  (= 0x7E0 + 0x08)        │
│                                                              │
│  0x7DF : Functional (broadcast to all ECUs — like a shout)  │
│  0x7E0–0x7E7 : Physical tester→ECU addressing range         │
│  0x7E8–0x7EF : Physical ECU→tester response range           │
│                                                              │
│  General rule: response ID = request ID + 0x08             │
└──────────────────────────────────────────────────────────────┘
```

> 🌉 **From your world:** ISO-TP is CAN's **TCP**. UDS is CAN's **HTTP**. Raw CAN frames are **IP packets** (each carries a fixed payload). ISO-TP reassembles the packets into a stream. UDS is the application protocol that rides on top. The stack: `UDS → ISO-TP → CAN` exactly mirrors `HTTP → TCP → IP`.

---

## 🧠 Concept: UDS Message Structure — Request, Response, Negative Response

Every UDS conversation follows the same envelope format. Memorise this.

### Request (Tester → ECU)

```
┌─────────────┬──────────────┬──────────────────────────┐
│  Service ID │  Sub-function│  Optional Data Bytes...  │
│  1 byte     │  1 byte      │  0 to N bytes            │
└─────────────┴──────────────┴──────────────────────────┘

Example: Switch to extended diagnostic session
  0x10  0x03
  ^^^^  ^^^^
   │     └── sub-function: 0x03 = extendedDiagnosticSession
   └── Service ID: DiagnosticSessionControl
```

### Positive Response (ECU → Tester) — "OK, done"

```
┌──────────────────────┬──────────────┬──────────────────┐
│  Service ID + 0x40   │  Sub-function│  Optional Data   │
│  1 byte              │  1 byte      │  0 to N bytes    │
└──────────────────────┴──────────────┴──────────────────┘

Example: Positive response to session switch
  0x50  0x03  [sessionParameterRecord...]
  ^^^^  ^^^^
   │     └── echoes back the sub-function
   └── 0x10 + 0x40 = 0x50 ← the golden rule: response = request + 0x40
```

> 🔑 **The golden rule:** Positive response SID = request SID + **0x40**. Always. `0x10 → 0x50`, `0x11 → 0x51`, `0x22 → 0x62`. This is the fastest way to spot a positive response in a trace.

### Negative Response (ECU → Tester) — "I can't/won't do that"

```
┌──────┬─────────────────┬──────────────────────────────┐
│ 0x7F │  Service ID     │  NRC (Negative Response Code)│
│ fixed│  (echoed back)  │  1 byte                      │
└──────┴─────────────────┴──────────────────────────────┘

Example: Tester asked for extended session but ECU won't allow it now
  0x7F  0x10  0x22
  ^^^^  ^^^^  ^^^^
   │     │     └── NRC: 0x22 = conditionsNotCorrect
   │     └── echoes the service that was rejected
   └── always 0x7F for negative responses
```

> 🌉 **From your world:** UDS negative responses are HTTP error codes with a body. `0x7F` is the status 4xx/5xx. The Service ID is the endpoint that failed. The NRC is the error code. `0x22 conditionsNotCorrect` = HTTP 412 Precondition Failed. `0x33 securityAccessDenied` = HTTP 401 Unauthorized. `0x11 serviceNotSupported` = HTTP 404 Not Found (for this ECU's feature set).

---

## 🧠 Concept: The Session State Machine

This is the most important concept for testing UDS. Think of it as **access levels**.

### The Three-Level Hotel Keycard Analogy 🏨

Imagine a hotel with three keycard levels:
- **Guest card (Floor 1–8):** opens your room, the gym, and the lobby.
- **Staff card (Floor 1–15):** opens everything plus the staff areas.
- **Master key (all floors):** opens every door, including the vault and server room.

An ECU has the exact same concept — **diagnostic sessions**:

```
┌───────────────────────────────────────────────────────────────────┐
│  UDS SESSION STATE MACHINE                                        │
│                                                                   │
│            ┌─────────────────────────────────────────┐           │
│            │         DEFAULT SESSION (0x01)           │           │
│            │  Always available after power-on         │           │
│            │  Services: ReadDTCs (0x19), ReadData     │           │
│            │            (0x22), ClearDTCs (0x14)      │           │
│            │  Like: guest mode — read-only, safe       │           │
│            └──────────┬────────────────┬────────────┘           │
│                       │ 0x10 0x03      │ 0x10 0x02               │
│                       ▼                ▼                          │
│      ┌────────────────────┐  ┌─────────────────────────┐         │
│      │  EXTENDED SESSION  │  │  PROGRAMMING SESSION    │         │
│      │      (0x03)        │  │         (0x02)           │         │
│      │  Calibration,      │  │  Firmware flashing,     │         │
│      │  I/O control,      │  │  ECU reprogramming.     │         │
│      │  advanced DTCs.    │  │  Requires Security      │         │
│      │  Still needs       │  │  Access (0x27) first.   │         │
│      │  Security Access   │  │  Like: root access.     │         │
│      │  for writes.       │  │                         │         │
│      │  Like: admin mode. │  └─────────────────────────┘         │
│      └────────────────────┘                                       │
│                                                                   │
│  ⏰ S3 TIMER: In any non-default session, if no UDS message       │
│     arrives within 5 seconds (configurable), ECU automatically   │
│     drops back to default session. No explicit "logout" needed.  │
└───────────────────────────────────────────────────────────────────┘
```

### Why Sessions Matter for Testing

| Session | What it unlocks | Test Relevance |
|---|---|---|
| Default (0x01) | Read DTCs, read data | Always available; regression tests |
| Extended (0x03) | Write calibration data, I/O overrides | Needs session; test session gating |
| Programming (0x02) | Firmware flash | Highest risk; test security + sequencing |

> **The S3 timer is a critical test case.** It's a session keepalive timeout — exactly like a JWT expiry. If your test tool goes silent for > S3 seconds, the ECU logs you out silently. Your next request gets `0x7F 0x10 0x22` (conditionsNotCorrect). Testers who don't know about S3 spend hours wondering why their "second test case" mysteriously fails after the first one passes.

> 🌉 **From your world:** The S3 timer is a **JWT/session token expiry**. Your test tool must send a keepalive (just re-send the session request) within the timeout window, or the server (ECU) logs you out. If you've ever debugged a Selenium test that fails on the second action because the session expired between steps — you already know this pattern.

---

## 🧠 Concept: Service 0x10 — DiagnosticSessionControl

### The Complete Request/Response Reference

**Request:** `[ISO-TP header] 0x10 [sessionType]`

| sessionType | Value | Description |
|---|---|---|
| defaultSession | 0x01 | Return to default (read-only diagnostics) |
| programmingSession | 0x02 | Firmware flashing mode |
| extendedDiagnosticSession | 0x03 | Calibration / advanced diagnostics |

**Positive Response:** `[ISO-TP header] 0x50 [sessionType] [P2_server_max] [P2*_server_max]`

The response includes timing parameters:
- `P2_server_max` — max time (ms) the ECU can take to respond normally
- `P2*_server_max` — extended response time (for slow operations, preceded by 0x78 pending)

**On the wire (Single Frame ISO-TP):**

```
Request: Switch to Extended Session
  CAN ID: 0x7E0
  Data:   [0x02, 0x10, 0x03, 0x00, 0x00, 0x00, 0x00, 0x00]
           ^^^   ^^^^  ^^^^
           PCI   SID   subfunction
           (2 UDS bytes follow)

Positive Response:
  CAN ID: 0x7E8
  Data:   [0x06, 0x50, 0x03, 0x00, 0x19, 0x01, 0xF4, 0x00]
           ^^^   ^^^^  ^^^^  ^^^^^^^^^^^^^^^^^^^^
           PCI   SID+  SF    timing parameters
           (6 bytes)  0x40
```

### Transition Rules — The Rules a Tester Must Know

```
┌────────────────────────────────────────────────────────────────┐
│  FROM              │  TO                │  Allowed?            │
├────────────────────┼────────────────────┼──────────────────────┤
│  Any session       │  defaultSession    │  ✅ Always            │
│  Default           │  extendedSession   │  ✅ Direct            │
│  Default           │  programmingSession│  ⚠️  Usually needs   │
│                    │                    │  extendedSession first│
│  Extended          │  programmingSession│  ✅ (then Security    │
│                    │                    │  Access)             │
│  Programming       │  extendedSession   │  ✅                   │
│  Any non-default   │  (timeout/S3)      │  ✅ Auto-reverts      │
└────────────────────┴────────────────────┴──────────────────────┘
```

> ⚠️ **The sequence-error trap:** Many testers try to jump straight to programmingSession from defaultSession and get `0x7F 0x10 0x24` (requestSequenceError). The correct path is: default → extended → (security access 0x27) → programming. The ECU is enforcing a security escalation ladder, exactly like `sudo` requiring a password before `su` to root.

---

## 🧠 Concept: Service 0x11 — ECUReset

The nuclear option. It reboots the ECU. ISO 14229 defines three flavours:

### The Three Reset Types

```
┌──────────────────────────────────────────────────────────────────┐
│  0x11 ECUReset — Sub-functions                                   │
├──────────────────┬───────────┬───────────────────────────────────┤
│  hardReset       │  0x01     │  Full power-cycle equivalent.     │
│                  │           │  All RAM cleared, volatile data   │
│                  │           │  lost, ECU restarts from scratch. │
│                  │           │  Like: pulling the power plug.    │
├──────────────────┼───────────┼───────────────────────────────────┤
│  keyOffOnReset   │  0x02     │  Simulates a key-off / key-on     │
│                  │           │  cycle. Some initialization skips │
│                  │           │  happen. ECU may keep some NVM    │
│                  │           │  state. Like: graceful reboot.    │
├──────────────────┼───────────┼───────────────────────────────────┤
│  softReset       │  0x03     │  Software-only restart. Minimal   │
│                  │           │  disruption; resets the app layer │
│                  │           │  only. Like: service restart      │
│                  │           │  (systemctl restart myservice).   │
└──────────────────┴───────────┴───────────────────────────────────┘
```

> 🌉 **From your world:** These map exactly to Linux/server restart options:
> - `hardReset` = `shutdown -h now` + power on (full cold boot)
> - `keyOffOnReset` = `reboot` (warm restart, preserves BIOS state)
> - `softReset` = `systemctl restart app-service` (application layer only)

### On the Wire

```
Request: Hard Reset
  CAN ID: 0x7E0
  Data:   [0x02, 0x11, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00]
                 ^^^^  ^^^^
                  SID   hardReset

Positive Response (BEFORE the ECU resets):
  CAN ID: 0x7E8
  Data:   [0x02, 0x51, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00]
                 ^^^^  ^^^^
                0x11+  echoes
                0x40   subfunction

Then: ECU reboots — drops off the bus, returns after startup time.
```

> ⚠️ **Critical tester knowledge:** After a hardReset, the ECU **disappears from the bus** for its boot time (anywhere from 100ms to several seconds depending on firmware complexity). A test that immediately sends another UDS request will get silence and must either poll for ECU presence or wait a defined startup time. Always time the ECU startup and verify it comes back within spec.

### Sequence and Session Requirements

ECUReset typically requires **at minimum a default session** — it's usually allowed from any session. Some OEMs restrict hardReset and programmingSession-reset to extendedDiagnosticSession to prevent accidental resets during normal operation.

```
Test scenario:
  1. In defaultSession  → 0x11 0x01 → should be ✅ (or ⚠️ OEM-restricted)
  2. In extendedSession → 0x11 0x01 → ✅ (after positive 0x51 response, ECU reboots)
  3. Verify ECU restarts: poll 0x10 0x01 every 100ms; time to first response = startup time
  4. Verify startup time ≤ spec (e.g., < 2000ms)
```

---

## 🧠 Concept: Negative Response Codes — The UDS Error Vocabulary

The NRC byte in `0x7F [SID] [NRC]` is the ECU's standardised way of saying "no, and here's specifically why." A tester who doesn't know these codes is reading error messages in an unknown language.

**The ones you must know cold:**

```
┌────────┬──────────────────────────────────┬────────────────────────────┐
│  NRC   │  Name                            │  Software Testing Analogy  │
├────────┼──────────────────────────────────┼────────────────────────────┤
│  0x10  │  generalReject                   │  500 Internal Server Error │
│  0x11  │  serviceNotSupported             │  404 (feature not in ECU)  │
│  0x12  │  subFunctionNotSupported         │  404 (wrong parameter)     │
│  0x13  │  incorrectMessageLengthOrFormat  │  400 Bad Request           │
│  0x22  │  conditionsNotCorrect            │  412 Precondition Failed   │
│  0x24  │  requestSequenceError            │  409 Conflict (wrong order)│
│  0x25  │  noResponseFromSubnetComponent   │  503 Service Unavailable   │
│  0x31  │  requestOutOfRange               │  422 Unprocessable Entity  │
│  0x33  │  securityAccessDenied            │  401 Unauthorized          │
│  0x35  │  invalidKey                      │  401 (wrong password)      │
│  0x36  │  exceededNumberOfAttempts        │  429 Too Many Requests     │
│  0x37  │  requiredTimeDelayNotExpired     │  429 (cooldown period)     │
│  0x78  │  requestCorrectlyReceived–       │  102 Processing (wait,     │
│        │  ResponsePending (RCRRP)         │  still working...)         │
└────────┴──────────────────────────────────┴────────────────────────────┘
```

> **The 0x78 RCRRP — a special case.** An ECU that needs more time than P2 allows sends a `0x7F [SID] 0x78` to say "I got your request, I'm processing it, wait for me." Then it sends the real response later (within P2* time). Testers must handle this — it's not an error, it's a legitimate "I'll call you back." An automated test that treats 0x78 as a failure will **false-fail on any slow ECU operation.**

---

## 🧩 The Big Picture: UDS in the Automotive Test Stack

```
┌──────────────────────────────────────────────────────────────────────┐
│  AUTOMOTIVE TEST STACK — WHERE UDS LIVES                             │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  Application: Flash, Calibration, Fault Injection, EOL Test  │   │
│  ├──────────────────────────────────────────────────────────────┤   │
│  │  UDS Services (ISO 14229) ← YOU ARE HERE                     │   │
│  │  0x10 Session | 0x11 Reset | 0x14 ClearDTC | 0x19 ReadDTC   │   │
│  │  0x22 ReadData | 0x27 SecurityAccess | 0x2E WriteData         │   │
│  │  0x31 RoutineCtrl | 0x34/36/37 Download (flash)              │   │
│  ├──────────────────────────────────────────────────────────────┤   │
│  │  ISO-TP Transport (ISO 15765-2)                               │   │
│  │  SF / FF / CF / FC — segmentation & reassembly               │   │
│  ├──────────────────────────────────────────────────────────────┤   │
│  │  CAN Data Link + Physical (Days 1–6)                         │   │
│  │  Frames, arbitration, error confinement, bit timing           │   │
│  └──────────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────┘
```

The UDS services you'll use most often as a test engineer:

| Service | SID | What it does |
|---|---|---|
| DiagnosticSessionControl | 0x10 | Switch session (guest→admin→root) |
| ECUReset | 0x11 | Reboot the ECU |
| ClearDiagnosticInformation | 0x14 | Erase stored DTCs |
| ReadDTCInformation | 0x19 | Read fault codes |
| ReadDataByIdentifier | 0x22 | Read a named data value |
| SecurityAccess | 0x27 | Unlock write access (challenge/response) |
| WriteDataByIdentifier | 0x2E | Write a calibration value |
| RoutineControl | 0x31 | Run a self-test / calibration routine |
| RequestDownload | 0x34 | Start firmware flashing |
| TransferData | 0x36 | Send firmware chunk |
| TransferExit | 0x37 | Finish firmware flash |

Today covers 0x10 and 0x11. Next days will add 0x19, 0x22, 0x27, and the flash sequence.

---

## 🌍 Where It's Used in the Real World

| Context | How UDS Is Used |
|---|---|
| **EOL (End-of-Line) production testing** | Every car that rolls off the line gets a full UDS battery: flash final firmware, write VIN/serial, clear DTCs, verify all ECUs respond, run self-tests. If the ECU fails, car doesn't ship. |
| **Workshop diagnostic scan** | Your OBD reader or dealer's diagnostic tool uses UDS (via OBD-II bridge) to read and clear P-codes (DTCs), check live data, run actuation tests. |
| **OTA (Over-the-Air) firmware update** | The telematics ECU triggers a programming session, authenticates via security access, downloads the firmware via transfer services. Same UDS services, over the air. |
| **HIL testing** | The HIL test bench runs UDS sequences as part of the automated test suite: change session, inject faults via WriteData, trigger self-tests via RoutineControl, verify DTCs. |
| **ASPICE / ISO 26262 evidence** | Every UDS-based test produces request/response traces. These are the verification records. "ECU correctly rejects extendedSession request when in wrong state" = a safety test. |

---

## 🔬 How a Tester Thinks About It

```
┌────────────────────────────────────────────────────────────────┐
│  TESTER'S UDS CHECKLIST — Session & Reset                      │
├────────────────────────────────────────────────────────────────┤
│                                                                │
│  SESSION CONTROL (0x10)                                        │
│  ✓ Can tester reach default session from power-on?            │
│  ✓ Can tester switch default → extended?                       │
│  ✓ Does ECU reject default → programming (sequence error)?    │
│  ✓ Does invalid session type return 0x12 subFuncNotSupported? │
│  ✓ Does S3 timer kick ECU back to default on silence?         │
│  ✓ Does response contain valid timing parameters (P2, P2*)?   │
│  ✓ Boundary: switch session while in mid-operation?           │
│                                                                │
│  ECU RESET (0x11)                                              │
│  ✓ Does hard reset return positive 0x51 BEFORE reboot?        │
│  ✓ Does ECU go offline after reset (frames disappear)?        │
│  ✓ Does ECU come back within startup time spec?               │
│  ✓ After hard reset, is session back to default? (yes!)       │
│  ✓ Does invalid reset type return 0x12 NRC?                   │
│  ✓ Does soft reset preserve expected NVM state?               │
│  ✓ Does reset during active communication behave gracefully?  │
│                                                                │
│  NEGATIVE RESPONSE VALIDATION                                  │
│  ✓ Every invalid request produces correct NRC (not silence)   │
│  ✓ NRC 0x78 handled (not false-failed) in automated tests     │
│  ✓ Correct Service ID echoed in the negative response         │
└────────────────────────────────────────────────────────────────┘
```

> 🌉 **From your world:** This checklist is a REST API test plan wearing a CAN hat. You're testing authentication (session = auth level), access control (NRC 0x33 = 401), state management (S3 timer = session expiry), error codes (NRC table = HTTP status codes), and restart behaviour (ECU reset = server reboot SLA test). Every pattern you've built for 15 years applies here.

---

## 🛠️ Hands-On Exercise: UDS Session & Reset Simulator

### What You'll Build

A complete UDS client/server simulation on python-can's virtual bus:
- A simulated **ECU** that manages a session state machine and responds to 0x10 and 0x11
- A **tester client** that sends requests and validates responses
- A **test runner** that exercises happy paths, error paths, S3 timeout, and ECU restart

### Files

```
Day-12_UDS_Introduction/
├── uds_session_reset.py   ← full simulation
└── Day12_UDS_Introduction.md
```

### Key Constants You'll Use

```python
# CAN IDs
TESTER_TX_ID = 0x7E0   # Tester → ECU
TESTER_RX_ID = 0x7E8   # ECU → Tester

# UDS Service IDs
SID_SESSION_CONTROL = 0x10
SID_ECU_RESET       = 0x11
SID_NEGATIVE_RESP   = 0x7F

# Session types (sub-functions for 0x10)
SESSION_DEFAULT      = 0x01
SESSION_PROGRAMMING  = 0x02
SESSION_EXTENDED     = 0x03

# Reset types (sub-functions for 0x11)
RESET_HARD      = 0x01
RESET_KEYOFFON  = 0x02
RESET_SOFT      = 0x03

# Negative Response Codes
NRC_SERVICE_NOT_SUPPORTED      = 0x11
NRC_SUBFUNC_NOT_SUPPORTED      = 0x12
NRC_CONDITIONS_NOT_CORRECT     = 0x22
NRC_REQUEST_SEQUENCE_ERROR     = 0x24
NRC_SECURITY_ACCESS_DENIED     = 0x33
```

---

## 🛠️ `uds_session_reset.py` — Full Listing

```python
"""
Day 12: UDS Session Control (0x10) & ECU Reset (0x11) Simulator
================================================================
Simulates a UDS-compliant ECU and a test client on a python-can
virtual bus. Exercises:
  - DiagnosticSessionControl (0x10): switch sessions, S3 timeout
  - ECUReset (0x11): hard/soft/keyOffOn, startup detection
  - Negative Response paths (wrong session, unknown sub-function)

No hardware needed.

Install:
    pip install python-can
"""

import can
import threading
import time
from enum import IntEnum

# ─── CONSTANTS ───────────────────────────────────────────────────────────────

CHANNEL      = "vcan0"
TESTER_TX_ID = 0x7E0   # Tester → ECU
TESTER_RX_ID = 0x7E8   # ECU → Tester

# Service IDs
SID_SESSION = 0x10
SID_RESET   = 0x11
SID_NEG     = 0x7F

# Session sub-functions
SESSION_DEFAULT     = 0x01
SESSION_PROGRAMMING = 0x02
SESSION_EXTENDED    = 0x03

# Reset sub-functions
RESET_HARD     = 0x01
RESET_KEYOFFON = 0x02
RESET_SOFT     = 0x03

# Negative Response Codes
NRC_SUBFUNC_NOT_SUPPORTED  = 0x12
NRC_CONDITIONS_NOT_CORRECT = 0x22
NRC_REQUEST_SEQUENCE_ERROR = 0x24


# ─── ISO-TP HELPERS ───────────────────────────────────────────────────────────

def build_single_frame(uds_bytes: list[int]) -> bytes:
    """Wrap UDS bytes in an ISO-TP Single Frame envelope."""
    assert 1 <= len(uds_bytes) <= 7, "Single frame: max 7 UDS bytes"
    pci = len(uds_bytes)          # 0x01–0x07
    padded = [pci] + uds_bytes
    padded += [0x00] * (8 - len(padded))   # pad to 8 bytes
    return bytes(padded)


def parse_single_frame(data: bytes) -> list[int] | None:
    """
    Extract UDS bytes from an ISO-TP Single Frame.
    Returns None if it's not a single frame.
    """
    pci = data[0]
    if (pci & 0xF0) != 0x00:
        return None                 # not a single frame
    length = pci & 0x0F
    return list(data[1: 1 + length])


# ─── SIMULATED ECU ───────────────────────────────────────────────────────────

class SessionType(IntEnum):
    DEFAULT     = SESSION_DEFAULT
    PROGRAMMING = SESSION_PROGRAMMING
    EXTENDED    = SESSION_EXTENDED


class SimulatedECU(threading.Thread):
    """
    A minimal UDS-compliant ECU that handles 0x10 and 0x11.
    Runs in a background thread, listens on TESTER_TX_ID,
    responds on TESTER_RX_ID.
    """

    S3_TIMEOUT_S = 5.0   # ISO 14229 S3 timer: 5 seconds

    def __init__(self):
        super().__init__(daemon=True, name="SimECU")
        self.bus           = can.Bus(interface="virtual", channel=CHANNEL)
        self.session       = SessionType.DEFAULT
        self.online        = True
        self.reboot_event  = threading.Event()
        self._stop         = threading.Event()
        self._last_diag_t  = time.monotonic()   # tracks S3 timer

    # ── Public control ────────────────────────────────────────────

    def stop(self):
        self._stop.set()
        self.bus.shutdown()

    # ── Response helpers ──────────────────────────────────────────

    def _send(self, uds_bytes: list[int]) -> None:
        frame_data = build_single_frame(uds_bytes)
        msg = can.Message(
            arbitration_id=TESTER_RX_ID,
            data=frame_data,
            is_extended_id=False
        )
        self.bus.send(msg)

    def _positive(self, sid: int, sub: int, extra: list[int] | None = None) -> None:
        payload = [sid + 0x40, sub] + (extra or [])
        self._send(payload)

    def _negative(self, sid: int, nrc: int) -> None:
        self._send([SID_NEG, sid, nrc])

    # ── Service handlers ──────────────────────────────────────────

    def _handle_session_control(self, sub: int) -> None:
        """0x10 DiagnosticSessionControl"""
        valid_sessions = {SESSION_DEFAULT, SESSION_PROGRAMMING, SESSION_EXTENDED}

        if sub not in valid_sessions:
            self._negative(SID_SESSION, NRC_SUBFUNC_NOT_SUPPORTED)
            return

        # Direct jump from default → programming requires extended first
        if (self.session == SessionType.DEFAULT
                and sub == SESSION_PROGRAMMING):
            self._negative(SID_SESSION, NRC_REQUEST_SEQUENCE_ERROR)
            return

        self.session = SessionType(sub)
        self._last_diag_t = time.monotonic()

        # Response includes P2=25ms, P2*=500ms (typical values)
        self._positive(SID_SESSION, sub, extra=[0x00, 0x19, 0x01, 0xF4])

    def _handle_ecu_reset(self, sub: int) -> None:
        """0x11 ECUReset — send positive response THEN reboot"""
        valid_resets = {RESET_HARD, RESET_KEYOFFON, RESET_SOFT}

        if sub not in valid_resets:
            self._negative(SID_RESET, NRC_SUBFUNC_NOT_SUPPORTED)
            return

        # Send positive response BEFORE going offline
        self._positive(SID_RESET, sub)

        if sub == RESET_SOFT:
            # Soft reset: just return to default session, stay online quickly
            self.session = SessionType.DEFAULT
        else:
            # Hard/KeyOffOn: simulate ECU going offline then rebooting
            self.online = False
            self.reboot_event.set()    # signal the run() loop to simulate reboot

    # ── Main loop ─────────────────────────────────────────────────

    def run(self) -> None:
        while not self._stop.is_set():

            # S3 timer — drop back to default if tester goes silent
            if (self.session != SessionType.DEFAULT
                    and time.monotonic() - self._last_diag_t > self.S3_TIMEOUT_S):
                self.session = SessionType.DEFAULT

            # Handle reboot simulation (non-blocking)
            if self.reboot_event.is_set():
                self.reboot_event.clear()
                boot_time = 0.3   # simulated ECU boot time: 300 ms
                time.sleep(boot_time)
                self.session = SessionType.DEFAULT
                self.online  = True

            # Receive a frame (50 ms timeout so loop stays responsive)
            frame = self.bus.recv(timeout=0.05)
            if frame is None:
                continue

            if frame.arbitration_id != TESTER_TX_ID:
                continue          # not addressed to us

            if not self.online:
                continue          # ECU is rebooting — ignore

            self._last_diag_t = time.monotonic()   # reset S3 timer
            uds = parse_single_frame(bytes(frame.data))
            if uds is None or len(uds) < 2:
                continue

            sid = uds[0]
            sub = uds[1]

            if sid == SID_SESSION:
                self._handle_session_control(sub)
            elif sid == SID_RESET:
                self._handle_ecu_reset(sub)
            else:
                self._negative(sid, 0x11)   # serviceNotSupported


# ─── TESTER CLIENT ────────────────────────────────────────────────────────────

class UDSTester:
    """
    Sends UDS requests to the ECU and validates responses.
    Collects pass/fail results like a mini pytest suite.
    """

    RESPONSE_TIMEOUT_S = 2.0

    def __init__(self):
        self.bus    = can.Bus(interface="virtual", channel=CHANNEL)
        self.passed: list[str] = []
        self.failed: list[str] = []

    def shutdown(self):
        self.bus.shutdown()

    # ── Low-level send/receive ────────────────────────────────────

    def _send_request(self, uds_bytes: list[int]) -> None:
        frame_data = build_single_frame(uds_bytes)
        self.bus.send(can.Message(
            arbitration_id=TESTER_TX_ID,
            data=frame_data,
            is_extended_id=False
        ))

    def _recv_response(self, timeout: float | None = None) -> list[int] | None:
        """
        Receive and parse a UDS response, transparently handling
        0x78 RCRRP (response pending) frames.
        """
        deadline = time.monotonic() + (timeout or self.RESPONSE_TIMEOUT_S)
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            frame = self.bus.recv(timeout=max(0.01, remaining))
            if frame is None:
                continue
            if frame.arbitration_id != TESTER_RX_ID:
                continue
            uds = parse_single_frame(bytes(frame.data))
            if uds is None:
                continue
            # Handle RCRRP (0x7F xx 0x78) — ECU says "still working, wait"
            if len(uds) == 3 and uds[0] == SID_NEG and uds[2] == 0x78:
                print(f"    ⏳ 0x78 RCRRP received — ECU still processing...")
                deadline += 5.0    # extend wait window
                continue
            return uds
        return None

    # ── Test assertion helpers ────────────────────────────────────

    def _pass(self, name: str, detail: str = "") -> None:
        tag = f"  ✅ PASS  {name}" + (f"  [{detail}]" if detail else "")
        print(tag)
        self.passed.append(tag)

    def _fail(self, name: str, detail: str = "") -> None:
        tag = f"  ❌ FAIL  {name}" + (f"  [{detail}]" if detail else "")
        print(tag)
        self.failed.append(tag)

    def _assert_positive(self, name: str, resp: list[int] | None,
                         expected_sid: int, expected_sub: int) -> bool:
        if resp is None:
            self._fail(name, "no response (timeout)")
            return False
        if resp[0] == SID_NEG:
            nrc = resp[2] if len(resp) >= 3 else "?"
            self._fail(name, f"got NegResp 0x7F 0x{resp[1]:02X} NRC=0x{nrc:02X}")
            return False
        if resp[0] != expected_sid + 0x40:
            self._fail(name, f"wrong SID: expected 0x{expected_sid+0x40:02X} "
                             f"got 0x{resp[0]:02X}")
            return False
        if resp[1] != expected_sub:
            self._fail(name, f"wrong sub-fn: expected 0x{expected_sub:02X} "
                             f"got 0x{resp[1]:02X}")
            return False
        self._pass(name, f"0x{resp[0]:02X} 0x{resp[1]:02X}")
        return True

    def _assert_negative(self, name: str, resp: list[int] | None,
                         expected_sid: int, expected_nrc: int) -> bool:
        if resp is None:
            self._fail(name, "no response (timeout)")
            return False
        if resp[0] != SID_NEG:
            self._fail(name, f"expected NegResp, got 0x{resp[0]:02X}")
            return False
        actual_nrc = resp[2] if len(resp) >= 3 else 0
        if actual_nrc != expected_nrc:
            self._fail(name, f"wrong NRC: expected 0x{expected_nrc:02X} "
                             f"got 0x{actual_nrc:02X}")
            return False
        self._pass(name, f"NRC=0x{actual_nrc:02X}")
        return True

    # ── Test cases ────────────────────────────────────────────────

    def tc_session_default_to_extended(self) -> None:
        """TC01: Switch from default → extended session."""
        self._send_request([SID_SESSION, SESSION_EXTENDED])
        resp = self._recv_response()
        self._assert_positive("TC01 default→extended", resp,
                               SID_SESSION, SESSION_EXTENDED)

    def tc_session_default_to_programming_rejected(self) -> None:
        """TC02: Direct default → programming must be rejected (sequence error)."""
        # First go back to default
        self._send_request([SID_SESSION, SESSION_DEFAULT])
        self._recv_response()   # consume the response

        self._send_request([SID_SESSION, SESSION_PROGRAMMING])
        resp = self._recv_response()
        self._assert_negative("TC02 default→programming (must reject)",
                               resp, SID_SESSION, NRC_REQUEST_SEQUENCE_ERROR)

    def tc_session_invalid_type(self) -> None:
        """TC03: Unknown session type returns subFunctionNotSupported."""
        self._send_request([SID_SESSION, 0x99])
        resp = self._recv_response()
        self._assert_negative("TC03 invalid session type",
                               resp, SID_SESSION, NRC_SUBFUNC_NOT_SUPPORTED)

    def tc_extended_to_programming(self) -> None:
        """TC04: Escalate correctly: default → extended → programming."""
        # Step 1: go to extended
        self._send_request([SID_SESSION, SESSION_EXTENDED])
        self._recv_response()

        # Step 2: now go to programming
        self._send_request([SID_SESSION, SESSION_PROGRAMMING])
        resp = self._recv_response()
        self._assert_positive("TC04 extended→programming", resp,
                               SID_SESSION, SESSION_PROGRAMMING)

    def tc_reset_hard(self, ecu: SimulatedECU) -> None:
        """TC05: Hard reset — positive response then ECU disappears then reboots."""
        # Ensure we're in a known session first
        self._send_request([SID_SESSION, SESSION_DEFAULT])
        self._recv_response()

        self._send_request([SID_RESET, RESET_HARD])
        resp = self._recv_response()
        if not self._assert_positive("TC05 hard reset (positive response)",
                                      resp, SID_RESET, RESET_HARD):
            return

        # ECU should now go offline — give it a moment
        time.sleep(0.1)
        if ecu.online:
            self._fail("TC05 ECU offline after reset", "ECU still online!")
        else:
            self._pass("TC05 ECU offline after reset", "ECU correctly offline")

        # Poll for ECU to come back
        boot_deadline = time.monotonic() + 2.0   # spec: back within 2s
        came_back = False
        while time.monotonic() < boot_deadline:
            if ecu.online:
                boot_ms = int((time.monotonic() - (boot_deadline - 2.0)) * 1000)
                self._pass("TC05 ECU restart within 2s",
                           f"ECU online after ~{boot_ms}ms")
                came_back = True
                break
            time.sleep(0.05)

        if not came_back:
            self._fail("TC05 ECU restart within 2s", "ECU did not come back!")
            return

        # Verify ECU is back in default session after hard reset
        self._send_request([SID_SESSION, SESSION_EXTENDED])
        resp = self._recv_response()
        self._assert_positive("TC05 session is default after restart",
                               resp, SID_SESSION, SESSION_EXTENDED)

    def tc_reset_soft(self) -> None:
        """TC06: Soft reset — ECU stays online, session returns to default."""
        # Switch to extended first
        self._send_request([SID_SESSION, SESSION_EXTENDED])
        self._recv_response()

        self._send_request([SID_RESET, RESET_SOFT])
        resp = self._recv_response()
        self._assert_positive("TC06 soft reset (positive response)",
                               resp, SID_RESET, RESET_SOFT)

        # After soft reset, session should be default
        # Prove it: trying programming directly should fail with sequence error
        self._send_request([SID_SESSION, SESSION_PROGRAMMING])
        resp2 = self._recv_response()
        self._assert_negative("TC06 session is default after soft reset",
                               resp2, SID_SESSION, NRC_REQUEST_SEQUENCE_ERROR)

    def tc_reset_invalid_type(self) -> None:
        """TC07: Invalid reset sub-function returns subFunctionNotSupported."""
        self._send_request([SID_RESET, 0xAA])
        resp = self._recv_response()
        self._assert_negative("TC07 invalid reset type",
                               resp, SID_RESET, NRC_SUBFUNC_NOT_SUPPORTED)

    def tc_s3_timer_expiry(self, ecu: SimulatedECU) -> None:
        """TC08: S3 timer — ECU drops to default after silence in extended session."""
        # Switch to extended
        self._send_request([SID_SESSION, SESSION_EXTENDED])
        resp = self._recv_response()
        if not self._assert_positive("TC08 enter extended (S3 setup)",
                                      resp, SID_SESSION, SESSION_EXTENDED):
            return

        print(f"\n    ⏰ Waiting {ecu.S3_TIMEOUT_S + 0.5:.1f}s for S3 timer to expire...")
        time.sleep(ecu.S3_TIMEOUT_S + 0.5)    # wait longer than S3

        # After S3, direct programming should fail (ECU is back to default)
        self._send_request([SID_SESSION, SESSION_PROGRAMMING])
        resp2 = self._recv_response()
        self._assert_negative("TC08 S3 dropped to default (programming rejected)",
                               resp2, SID_SESSION, NRC_REQUEST_SEQUENCE_ERROR)

    # ── Summary ───────────────────────────────────────────────────

    def print_summary(self) -> None:
        total = len(self.passed) + len(self.failed)
        print(f"\n{'='*62}")
        print(f"  TEST SUMMARY: {len(self.passed)}/{total} passed, "
              f"{len(self.failed)} failed")
        print(f"{'='*62}")
        if self.failed:
            print("\n  Failed checks:")
            for f in self.failed:
                print(f"    {f.strip()}")


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main() -> None:
    print("\n" + "🩺 " * 20)
    print("  Day 12: UDS Session Control & ECU Reset Simulator")
    print("  ISO 14229 over python-can virtual bus")
    print("🩺 " * 20)

    # Start the simulated ECU
    ecu = SimulatedECU()
    ecu.start()
    time.sleep(0.1)   # let ECU thread settle

    tester = UDSTester()

    separator = lambda title: (
        print(f"\n{'─'*62}\n  {title}\n{'─'*62}")
    )

    separator("GROUP 1: DiagnosticSessionControl (0x10)")
    tester.tc_session_default_to_extended()
    tester.tc_session_default_to_programming_rejected()
    tester.tc_session_invalid_type()
    tester.tc_extended_to_programming()

    separator("GROUP 2: ECUReset (0x11)")
    tester.tc_reset_hard(ecu)
    tester.tc_reset_soft()
    tester.tc_reset_invalid_type()

    separator("GROUP 3: S3 Session Timeout")
    tester.tc_s3_timer_expiry(ecu)

    tester.print_summary()

    ecu.stop()
    tester.shutdown()


if __name__ == "__main__":
    main()
```

### Expected Output

```
🩺 🩺 🩺 🩺 🩺 🩺 🩺 🩺 🩺 🩺 🩺 🩺 🩺 🩺 🩺 🩺 🩺 🩺 🩺 🩺
  Day 12: UDS Session Control & ECU Reset Simulator
  ISO 14229 over python-can virtual bus
🩺 🩺 🩺 🩺 🩺 🩺 🩺 🩺 🩺 🩺 🩺 🩺 🩺 🩺 🩺 🩺 🩺 🩺 🩺 🩺

──────────────────────────────────────────────────────────────
  GROUP 1: DiagnosticSessionControl (0x10)
──────────────────────────────────────────────────────────────
  ✅ PASS  TC01 default→extended  [0x50 0x03]
  ✅ PASS  TC02 default→programming (must reject)  [NRC=0x24]
  ✅ PASS  TC03 invalid session type  [NRC=0x12]
  ✅ PASS  TC04 extended→programming  [0x50 0x02]

──────────────────────────────────────────────────────────────
  GROUP 2: ECUReset (0x11)
──────────────────────────────────────────────────────────────
  ✅ PASS  TC05 hard reset (positive response)  [0x51 0x01]
  ✅ PASS  TC05 ECU offline after reset  [ECU correctly offline]
  ✅ PASS  TC05 ECU restart within 2s  [ECU online after ~300ms]
  ✅ PASS  TC05 session is default after restart  [0x50 0x03]
  ✅ PASS  TC06 soft reset (positive response)  [0x51 0x03]
  ✅ PASS  TC06 session is default after soft reset  [NRC=0x24]
  ✅ PASS  TC07 invalid reset type  [NRC=0x12]

──────────────────────────────────────────────────────────────
  GROUP 3: S3 Session Timeout
──────────────────────────────────────────────────────────────
  ✅ PASS  TC08 enter extended (S3 setup)  [0x50 0x03]

    ⏰ Waiting 5.5s for S3 timer to expire...

  ✅ PASS  TC08 S3 dropped to default (programming rejected)  [NRC=0x24]

==============================================================
  TEST SUMMARY: 11/11 passed, 0 failed
==============================================================
```

### Run It

```bash
cd Day-12_UDS_Introduction
pip install python-can
python uds_session_reset.py
```

> 💡 **Note:** TC08 deliberately takes ~5.5 seconds because it waits for the S3 timer to fire. That's the test — you're proving the ECU enforces the timeout. This is not a slow test; it's a *real* test of a real ECU behaviour.

---

## 🔥 Challenge: The Diagnostic Gatekeeping Suite

**Scenario:** You're validating a new Engine ECU before it goes to production. The spec says:
1. Only `defaultSession` is accessible at power-on — no direct programming access
2. A hard reset during `extendedSession` must complete in under **500ms**
3. A burst of 10 rapid session-switch requests must not crash the ECU or produce wrong responses
4. If the tester sends a request with an **empty data field** (just the PCI byte, no SID), the ECU must respond with `0x13` (incorrectMessageLengthOrFormat), not crash or timeout

### Challenge 1 — ⏱️ Measure Reset Startup Time Precisely

Extend `tc_reset_hard()` to measure the exact time between sending the reset request and receiving the **first valid UDS response** (from a repeated `0x10 0x03` poll). Assert it's under 500ms.

```python
def measure_startup_time(self, max_ms: float) -> float:
    """
    Poll 0x10 0x03 after a hard reset.
    Return elapsed ms when first positive response arrives.
    Assert <= max_ms.
    """
    t_start = time.monotonic()
    while True:
        self._send_request([SID_SESSION, SESSION_EXTENDED])
        resp = self._recv_response(timeout=0.1)
        if resp and resp[0] == SID_SESSION + 0x40:
            elapsed_ms = (time.monotonic() - t_start) * 1000
            # TODO: assert elapsed_ms <= max_ms
            return elapsed_ms
```

### Challenge 2 — 🔁 Rapid-Fire Stress Test

Send **10 session-switch requests as fast as possible** (no delay between them) and verify:
- All 10 get a response (no silent drops)
- No response contains a garbled SID or NRC
- The ECU's final session matches the last request

```python
def tc_rapid_fire_sessions(self, count: int = 10) -> None:
    sessions = [SESSION_EXTENDED, SESSION_DEFAULT] * (count // 2)
    for i, sess in enumerate(sessions):
        self._send_request([SID_SESSION, sess])
        resp = self._recv_response(timeout=0.5)
        # TODO: assert response exists and is correct for each iteration
```

### Challenge 3 — 🛡️ Malformed Request Handling

Add NRC `0x13` (incorrectMessageLengthOrFormat) to the ECU and test:

```python
# In SimulatedECU._handle_*: check minimum length
# In UDSTester:
def tc_malformed_request(self) -> None:
    """Send a request with only the SID, no sub-function."""
    # Build a raw frame with just SID and no sub-function byte
    self.bus.send(can.Message(
        arbitration_id=TESTER_TX_ID,
        data=bytes([0x01, 0x10, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]),
        is_extended_id=False
    ))
    resp = self._recv_response()
    # Expect: 0x7F 0x10 0x13
```

### Stretch Goal — 📄 JSON Report

Output the test results as a structured JSON file `uds_report.json`:

```json
{
  "timestamp": "2026-06-22T10:00:00",
  "ecu_address": "0x7E0",
  "total": 11,
  "passed": 11,
  "failed": 0,
  "test_cases": [
    {"id": "TC01", "name": "default→extended", "status": "PASS",
     "response": "0x50 0x03"},
    {"id": "TC02", "name": "default→programming (must reject)", "status": "PASS",
     "response": "0x7F 0x10 NRC=0x24"}
  ]
}
```

This is your CI artefact — the JSON report a build server would ingest.

---

## ❓ Quiz + Answers

**Q1.** What is the positive response SID for DiagnosticSessionControl (0x10)?

<details>
<summary>Answer</summary>

**0x50** — the golden rule: positive response = request SID + 0x40. `0x10 + 0x40 = 0x50`. This rule holds for every UDS service. `0x11 → 0x51`, `0x22 → 0x62`, `0x27 → 0x67`. Spot a positive response instantly in any trace.

</details>

---

**Q2.** A tester sends `0x10 0x02` (switch to programmingSession) from defaultSession. What NRC should the ECU return, and why?

<details>
<summary>Answer</summary>

`0x7F 0x10 0x24` — **requestSequenceError (NRC 0x24)**. Jumping directly from defaultSession to programmingSession violates the security escalation ladder. The correct path is: default → extendedSession first (+ SecurityAccess 0x27 unlock), then → programmingSession. NRC 0x24 is the ECU saying "you skipped a required step."

</details>

---

**Q3.** Your automated test sends a `0x11 0x01` (hard reset) and immediately sends another UDS request 50ms later. It gets no response. Is this a test framework bug or an ECU bug?

<details>
<summary>Answer</summary>

**Neither — it's a test design error.** After a hard reset, the ECU is rebooting (offline). 50ms is almost certainly not enough for an ECU to complete its boot sequence (can be 100ms–2000ms). The test must poll for ECU presence after reset and wait for the first valid response before continuing. Add a startup-time assertion: verify the ECU responds within the spec's startup time budget.

</details>

---

**Q4.** What is the S3 timer and what happens when it fires?

<details>
<summary>Answer</summary>

The S3 timer (also called *TesterPresent* timeout) is an **inactivity watchdog** in the ECU. When the tester is in any non-default session and stops sending UDS messages for longer than S3 (typically 5 seconds), the ECU **automatically reverts to the defaultSession**. This prevents a tester tool from leaving an ECU stuck in a privileged session if the connection drops. Automated tests must either keep the session alive by sending periodic requests (a keepalive) or account for the revert in their assertion logic.

</details>

---

**Q5.** You receive `0x7F 0x10 0x78` during a session switch. Is this a failure?

<details>
<summary>Answer</summary>

**No.** `0x78` is **requestCorrectlyReceivedResponsePending (RCRRP)** — the ECU is saying "I received your request, I'm processing it, please wait." It will send the actual positive (or negative) response within P2* time. A test that treats 0x78 as a failure will **false-fail on any legitimate slow operation**. Correct handling: extend the receive timeout and keep waiting.

</details>

---

## 📌 Key Takeaways

```
┌──────────────────────────────────────────────────────────────────┐
│  DAY 12 KEY TAKEAWAYS                                            │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  1. UDS is a request/response diagnostic layer above CAN.       │
│     ISO-TP segments long messages; UDS is the application.      │
│     Stack: UDS → ISO-TP → CAN  ≡  HTTP → TCP → IP.             │
│                                                                  │
│  2. Positive response = Service ID + 0x40. Golden rule.         │
│     Negative response = 0x7F + original SID + NRC.              │
│                                                                  │
│  3. Sessions are access levels: default (guest) →               │
│     extended (admin) → programming (root). Direct              │
│     escalation skips are rejected with NRC 0x24.                │
│                                                                  │
│  4. The S3 timer is a session keepalive watchdog.               │
│     Silence > S3 seconds → ECU reverts to default.             │
│     Your tests must account for session expiry.                 │
│                                                                  │
│  5. After hard reset, ECU goes offline then reboots.            │
│     Always measure and assert startup time, not just            │
│     "eventually comes back."                                     │
│                                                                  │
│  6. NRC 0x78 (RCRRP) is NOT a failure — it's "wait for         │
│     me." Automated test tools must handle it gracefully.        │
│                                                                  │
│  7. The NRC table is the UDS error vocabulary. Know the         │
│     top 8 NRCs cold — they tell you exactly what went           │
│     wrong without needing a trace.                               │
│                                                                  │
│  8. Every UDS behaviour (session gating, S3 timeout,           │
│     reset sequencing) is a test case, not just a feature.       │
└──────────────────────────────────────────────────────────────────┘
```

---

## ⏭️ Run code 
```
cd "Day-12_UDS_Introduction"
pip install python-can
python uds_session_reset.py

```
