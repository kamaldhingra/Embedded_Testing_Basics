# 🚌📦 Day 16: ISO-TP Transport Protocol (ISO 15765-2) + Advanced DTC — Snapshot, Extended Data & Permanent DTCs

> **Course:** Embedded & IoT Testing Masterclass
> **Instructor:** Professor Embed
> **Prerequisites:** Day 1–15 (Full CAN + all UDS services including 0x10, 0x14, 0x19, 0x22, 0x27, 0x2E, 0x31, 0x3E)

---

## 📚 Table of Contents

1. [Recap: The Layer We've Been Using Without Looking At](#recap)
2. [Concept: Why ISO-TP Exists — The 7-Byte Problem](#concept-why-istp)
3. [Concept: The Four ISO-TP Frame Types](#concept-frame-types)
4. [Concept: Single Frame (SF) — The Easy Case](#concept-sf)
5. [Concept: First Frame (FF) — Opening a Segmented Transfer](#concept-ff)
6. [Concept: Flow Control (FC) — The Receiver Controls the Pace](#concept-fc)
7. [Concept: Consecutive Frame (CF) — Carrying the Remaining Data](#concept-cf)
8. [Concept: ISO-TP State Machine — Sender & Receiver Perspectives](#concept-state-machine)
9. [Concept: ISO-TP Timing Parameters — N_Bs, N_Cr, STmin, BlockSize](#concept-timing)
10. [Concept: DTC Snapshot / Freeze Frame (0x19 0x04)](#concept-snapshot)
11. [Concept: DTC Extended Data (0x19 0x06) — Occurrence Counter & Ageing](#concept-ext-data)
12. [Concept: Permanent DTCs (0x19 0x0B) — The Fault That Survives Clear](#concept-permanent)
13. [The Big Picture: ISO-TP in the Full CAN/UDS Stack](#the-big-picture)
14. [Where It's Used in the Real World](#where-its-used)
15. [How a Tester Thinks About It](#how-a-tester-thinks)
16. [Hands-On Exercise: ISO-TP + Advanced DTC Simulator](#hands-on-exercise)
17. [Quiz + Answers](#quiz--answers)
18. [Key Takeaways](#key-takeaways)

---

## 🔁 Recap: The Layer We've Been Using Without Looking At

Every UDS service in Days 12–15 worked. You sent bytes, you got bytes back. But there was always a magic helper in the code doing something suspicious:

```python
def build_single_frame(uds_bytes: list) -> bytes:
    pci    = len(uds_bytes)
    padded = [pci] + uds_bytes + [0x00] * (7 - len(uds_bytes))
    return bytes(padded)
```

And for responses longer than 7 bytes there was always this pattern:

```python
if pci_type == 0x1:   # First Frame
    total_expected    = ((first_byte & 0x0F) << 8) | frame.data[1]
    collected_payload = list(frame.data[2:])
    # Send Flow Control...
elif pci_type == 0x2:  # Consecutive Frame
    collected_payload += list(frame.data[1:])
    if len(collected_payload) >= total_expected:
        return collected_payload[:total_expected]
```

You've been using **ISO-TP (ISO 15765-2)** since Day 12. Today we open the black box.

And there are three DTC sub-functions from ISO 14229 that appeared in the Day 15 table but weren't implemented: **0x04 (freeze frame)**, **0x06 (extended data)**, and **0x0B (permanent DTCs)**. These are the data that actually let you diagnose *why* a DTC was set, *how often* it happened, and *which faults can never be hidden by clearing*. Today closes all three gaps.

> *"ISO-TP is the unsung hero of automotive diagnostics. It's the protocol that allows a scan tool to read a 2000-byte firmware version string or a multi-DTC extended data dump over a CAN bus that can only carry 8 bytes per frame. Without it, every UDS service would be limited to 7 bytes — and you'd never be able to flash firmware over CAN."*

---

## 🧠 Concept: Why ISO-TP Exists — The 7-Byte Problem

### Classical CAN's Hard Limit

A classical CAN frame has a maximum data field of **8 bytes**. Of those 8 bytes, UDS needs at least 1 byte for its own **PCI (Protocol Control Information)** overhead byte. That leaves **7 bytes maximum** for actual UDS payload per frame.

7 bytes is fine for short commands like `[0x10, 0x03]` (switch to extended session). But it is nowhere near enough for:

```
Firmware image:         100,000+ bytes
DTC extended data dump: 50–200 bytes
VIN string:             17 ASCII characters (= 17 bytes, already > 7)
DID multi-read:         variable, can be 100+ bytes
Freeze frame snapshot:  20–50 bytes
```

ISO-TP solves this by defining a **segmentation and reassembly protocol** that sits between the CAN physical/data link layer and UDS:

```
┌───────────────────────────────────────────────────────────────────────┐
│                                                                       │
│  UDS Application Layer    (ISO 14229)                                │
│  e.g., ReadDTCInformation response = 50 bytes                        │
│                         │                                             │
│                         ▼                                             │
│  ISO-TP Transport Layer   (ISO 15765-2)                              │
│  Segments 50 bytes into:                                             │
│    FF (6 bytes payload) + CF1 (7 bytes) + CF2 (7 bytes) + ...        │
│                         │                                             │
│                         ▼                                             │
│  CAN Data Link Layer    (ISO 11898-1)                                │
│  Each frame: max 8 bytes, transmitted individually on the bus        │
│                                                                       │
└───────────────────────────────────────────────────────────────────────┘
```

> 🌉 **From your world:** ISO-TP is **TCP segmentation** — breaking a large application message into packets that fit the network MTU (Maximum Transmission Unit). Just as TCP breaks a 10MB HTTP response into 1460-byte TCP segments, ISO-TP breaks a 50-byte UDS response into 8-byte CAN frames. The conceptual model is identical: segment on send, flow-control the pace, reassemble on receive.

### CAN FD Makes It Bigger But Doesn't Eliminate It

CAN FD increases the maximum data payload to 64 bytes per frame. ISO-TP is still needed for UDS messages > 62 bytes (64 bytes - 2 bytes PCI overhead for FF). In practice, CAN FD's larger frames reduce the number of segments needed but don't eliminate ISO-TP.

---

## 🧠 Concept: The Four ISO-TP Frame Types

ISO-TP defines exactly four frame types, identified by the upper nibble of the first byte:

```
┌──────────────────────────────────────────────────────────────────────┐
│  ISO-TP FRAME TYPES — IDENTIFIED BY UPPER NIBBLE OF BYTE 0          │
├────────────────┬─────────────────────────────────────────────────────┤
│  0x0x          │  Single Frame (SF)                                  │
│  (upper = 0)   │  Entire UDS payload fits in one CAN frame.          │
│                │  Used for payloads 1–7 bytes.                       │
├────────────────┼─────────────────────────────────────────────────────┤
│  0x1x          │  First Frame (FF)                                   │
│  (upper = 1)   │  Opens a multi-frame transfer.                      │
│                │  Carries total length + first 6 payload bytes.      │
├────────────────┼─────────────────────────────────────────────────────┤
│  0x2x          │  Consecutive Frame (CF)                             │
│  (upper = 2)   │  Carries subsequent payload bytes.                  │
│                │  Sequence number (SN) in lower nibble: 1→F→0→1→…   │
├────────────────┼─────────────────────────────────────────────────────┤
│  0x3x          │  Flow Control (FC)                                  │
│  (upper = 3)   │  Receiver → sender: "continue / wait / abort".     │
│                │  Includes STmin (gap between CFs) and BlockSize.    │
└────────────────┴─────────────────────────────────────────────────────┘
```

---

## 🧠 Concept: Single Frame (SF) — The Easy Case

```
┌──────────────────────────────────────────────────────────────────────┐
│  SINGLE FRAME (SF) — WIRE FORMAT                                    │
│                                                                      │
│  Byte 0:   0 | LEN    (upper nibble = 0x0, lower = payload length)  │
│  Bytes 1–7: UDS payload (zero-padded to fill 8-byte CAN frame)      │
│                                                                      │
│  Example: request extended session [0x10, 0x03]                     │
│                                                                      │
│  CAN frame data:  02  10  03  00  00  00  00  00                    │
│                   ^^  ^^  ^^  ─────────────────                     │
│                   │   ──────  padding (DLC=8 always used)            │
│                   │   UDS:    DiagnosticSessionControl + extended    │
│                   PCI: SF, length=2                                  │
│                                                                      │
│  Constraint: payload length 1–7 bytes.                              │
│  Length = 0: invalid SF. Length > 7: must use FF instead.           │
└──────────────────────────────────────────────────────────────────────┘
```

**Why always DLC=8?** CAN frames can have shorter DLC values, but automotive UDS always transmits DLC=8 and zero-pads. This is a deliberate protocol choice: constant frame length makes bus load analysis predictable and avoids edge cases in physical layer implementations.

> 🌉 **From your world:** This is like HTTP always sending full 1460-byte TCP segments even if your request is only 200 bytes (with the rest as padding/null). Constant-size packets are easier for network analysers to parse and for bus monitors to profile.

---

## 🧠 Concept: First Frame (FF) — Opening a Segmented Transfer

```
┌──────────────────────────────────────────────────────────────────────┐
│  FIRST FRAME (FF) — WIRE FORMAT                                     │
│                                                                      │
│  Byte 0:  0x1 | (total_len >> 8)   high nibble = 0x1, low nibble   │
│                                    = bits 8–11 of total length      │
│  Byte 1:  total_len & 0xFF         bits 0–7 of total length         │
│  Bytes 2–7: first 6 bytes of UDS payload                            │
│                                                                      │
│  Total length field is 12 bits → max = 4095 bytes per segment.     │
│                                                                      │
│  Example: 50-byte response starting with 0x59 0x0A …               │
│                                                                      │
│  CAN frame data:  10  32  59  0A  FF  03  00  01  28  01  C1  00   │
│  (shown as 12 bytes but each CAN frame is max 8 bytes — here        │
│   only first 8 matter:  10 32 59 0A FF 03 00 01)                   │
│                          ^^  ^^  ─────────────────                  │
│                          │   │   first 6 UDS payload bytes           │
│                          │   │                                       │
│                          1x  32 → total = 0x032 = 50 bytes          │
│                                                                      │
│  After receiving FF, receiver MUST send a Flow Control frame.       │
│  If no FC arrives within N_Bs_max (1000ms): sender aborts.          │
└──────────────────────────────────────────────────────────────────────┘
```

### The 4095-Byte Limit — and the Escape Hatch

Classic ISO-TP's 12-bit length field caps messages at 4095 bytes. ISO 15765-2:2016 added an escape sequence for longer messages:

```
Escape FF (for payloads > 4095 bytes):
  Byte 0: 0x10
  Byte 1: 0x00   ← signals "escape: use 4-byte length in bytes 2–5"
  Bytes 2–5: uint32 total length (big-endian) → up to 4 GB
  Bytes 6–7: first 2 bytes of payload

Used for: large firmware images, big log dumps
In practice: CAN FD with 64-byte frames makes this more common
```

---

## 🧠 Concept: Flow Control (FC) — The Receiver Controls the Pace

Flow Control is the most important ISO-TP concept for test engineers. It's how the **receiver** tells the **sender** how fast to transmit:

```
┌──────────────────────────────────────────────────────────────────────┐
│  FLOW CONTROL (FC) — WIRE FORMAT                                    │
│                                                                      │
│  Byte 0:  0x30 | FC_flag                                            │
│           0x30 = ContinueToSend (CTS)  — "send all remaining CFs"  │
│           0x31 = Wait              — "pause, I'm not ready yet"     │
│           0x32 = Overflow          — "too much data, abort"         │
│                                                                      │
│  Byte 1:  BlockSize (BS)                                            │
│           0x00 = send all remaining CFs without pausing             │
│           0x0N = send N CFs, then wait for another FC               │
│                                                                      │
│  Byte 2:  STmin (Separation Time minimum)                           │
│           0x00–0x7F = 0–127 ms  (direct encoding)                  │
│           0xF1–0xF9 = 100–900 µs  (for CAN FD high-speed)         │
│           0x80–0xF0 = RESERVED                                      │
│                                                                      │
│  Bytes 3–7: padding (0x00)                                          │
└──────────────────────────────────────────────────────────────────────┘
```

### FC_Flag States Explained

```
CTS (0x30):  "Ready. Send BlockSize CFs, then pause (or all if BS=0)."
             Normal flow. ECU is ready to receive.

WAIT (0x31): "I'm busy. Wait for me to send another FC."
             Tester must wait. ECU will send a second FC (CTS or WAIT again).
             Tester has N_Bs_max (1000ms) after the last FC before aborting.

OVERFLOW (0x32): "I can't buffer your message. Too big. Abort."
             Tester aborts immediately. Does NOT retry.
             Root cause: ECU's receive buffer is too small for this payload.
```

### BlockSize Example

```
Tester sends a 30-byte UDS request (rare but valid):
  FF → ECU responds FC(CTS, BlockSize=2, STmin=0ms)
         ← send 2 CFs, then pause
  CF1 → 
  CF2 → ECU responds FC(CTS, BlockSize=2, STmin=0ms)
         ← send next 2 CFs
  CF3 →
  CF4 → done (30 bytes = FF:6 + 4×CF:7 = 34, trimmed to 30)

BlockSize=0 means "send everything at once" — the most common case.
BlockSize>0 is used when the ECU's buffer is small and needs to process
in chunks. Test this with a payload > (BlockSize × 7 + 6) bytes.
```

> 🌉 **From your world:** Flow Control is TCP's **receive window** + **congestion control** in one. BlockSize = receive window (how many segments allowed before ACK). STmin = enforced inter-packet gap (like pacing in HTTP/2 or TCP's delayed ACK timer). WAIT = TCP's zero window advertisement. OVERFLOW = TCP RST. The analogy is remarkably precise.

---

## 🧠 Concept: Consecutive Frame (CF) — Carrying the Remaining Data

```
┌──────────────────────────────────────────────────────────────────────┐
│  CONSECUTIVE FRAME (CF) — WIRE FORMAT                               │
│                                                                      │
│  Byte 0:  0x20 | (SN & 0x0F)   upper nibble = 0x2, lower = SN     │
│  Bytes 1–7: next 7 bytes of UDS payload (zero-padded on last CF)   │
│                                                                      │
│  Sequence Number (SN):                                              │
│  • First CF after FF: SN = 0x01                                    │
│  • Increments: 0x01, 0x02, …, 0x0F, 0x00, 0x01, 0x02 …            │
│  • WRAPS at 0x0F → 0x00 (NOT back to 0x01)                        │
│  • Only 4 bits → counts 0x0–0xF (16 values)                       │
│                                                                      │
│  Example: 50-byte message, after FF carried bytes 0–5:              │
│  CF1 (SN=0x01): 0x21 + bytes 6–12   (7 bytes)                      │
│  CF2 (SN=0x02): 0x22 + bytes 13–19  (7 bytes)                      │
│  CF3 (SN=0x03): 0x23 + bytes 20–26  (7 bytes)                      │
│  CF4 (SN=0x04): 0x24 + bytes 27–33  (7 bytes)                      │
│  CF5 (SN=0x05): 0x25 + bytes 34–40  (7 bytes)                      │
│  CF6 (SN=0x06): 0x26 + bytes 41–47  (7 bytes)                      │
│  CF7 (SN=0x07): 0x27 + bytes 48–49 + 0x00×5  (2 bytes + padding)  │
│                                                                      │
│  Total CAN frames: 1 FF + 7 CF = 8 frames for 50 bytes             │
│  vs. 50 ÷ 8 = 7 frames if CAN allowed 8 bytes (but needs PCI)     │
└──────────────────────────────────────────────────────────────────────┘
```

### The SN Wrap Bug — A Classic Integration Failure

The SN wraps at 0x0F → 0x00. A common test-tool implementation bug is wrapping 0x0F → 0x01 instead:

```
ECU transmits:   0x21 0x22 0x23 … 0x2F 0x20 0x21 0x22 …
                  ^SN=1                  ^SN=0 wraps here

Buggy receiver expects:  … 0x2F 0x21 … (expects SN=1 after SN=F)
ECU sends:               … 0x2F 0x20 … (sends SN=0)

Result: receiver sees "wrong SN" → aborts → message truncated

Only manifests for messages requiring > 15 CFs (> 6 + 15×7 = 111 bytes)
Symptoms: all short UDS reads work; long firmware version reads fail.
```

This is a real integration bug found in scan tools. The test for it: send a request that triggers a 112+ byte response (e.g., 0x19 0x0A with a large DTC catalogue), and verify the response is fully received.

---

## 🧠 Concept: ISO-TP State Machine — Sender & Receiver Perspectives

### Sender State Machine (ECU sending a long response)

```
┌───────────────────────────────────────────────────────────────────────────┐
│  ISO-TP SENDER STATE MACHINE                                              │
│                                                                           │
│  ┌─────────────┐                                                          │
│  │    IDLE     │  payload ≤ 7 bytes: send SF → done                      │
│  └──────┬──────┘  payload > 7 bytes: send FF → wait_for_FC               │
│         │                                                                 │
│         ▼ (payload > 7)                                                   │
│  ┌─────────────────────────────────────────────────────────────────────┐ │
│  │  WAIT_FOR_FC                                                        │ │
│  │  Timer = N_Bs_max (1000ms)                                          │ │
│  │  On FC(CTS):   → SENDING_CF                                         │ │
│  │  On FC(WAIT):  → WAIT_FOR_FC (timer resets)                         │ │
│  │  On FC(OFLOW): → ABORT (do not retry)                               │ │
│  │  On timeout:   → ABORT                                              │ │
│  └─────────────────────────────────────────────────────────────────────┘ │
│         │ CTS                                                             │
│         ▼                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────┐ │
│  │  SENDING_CF                                                         │ │
│  │  Send CFs, respecting STmin gap between each.                       │ │
│  │  If BlockSize > 0: after N CFs, pause → WAIT_FOR_FC                 │ │
│  │  If BlockSize = 0: send all CFs → DONE                              │ │
│  └─────────────────────────────────────────────────────────────────────┘ │
└───────────────────────────────────────────────────────────────────────────┘
```

### Receiver State Machine (Tester receiving a long response)

```
┌───────────────────────────────────────────────────────────────────────────┐
│  ISO-TP RECEIVER STATE MACHINE                                            │
│                                                                           │
│  ┌─────────────┐                                                          │
│  │    IDLE     │  On SF: extract payload → deliver to UDS layer → IDLE  │
│  └──────┬──────┘  On FF: record total_length, save first 6 bytes,        │
│         │               send FC(CTS) → RECEIVING                         │
│         ▼                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────┐ │
│  │  RECEIVING                                                          │ │
│  │  Timer = N_Cr_max (1000ms) per CF                                   │ │
│  │  On CF(correct SN): append 7 bytes.                                 │ │
│  │    If len(payload) ≥ total_length: → deliver to UDS → IDLE          │ │
│  │    Else: keep waiting, next CF expected within N_Cr_max             │ │
│  │  On CF(wrong SN): → ABORT (message corrupted)                       │ │
│  │  On timeout (no CF within N_Cr_max): → ABORT                        │ │
│  └─────────────────────────────────────────────────────────────────────┘ │
└───────────────────────────────────────────────────────────────────────────┘
```

---

## 🧠 Concept: ISO-TP Timing Parameters — N_Bs, N_Cr, STmin, BlockSize

The four timing parameters that every tester must understand:

```
┌───────────────────────────────────────────────────────────────────────┐
│  ISO-TP TIMING PARAMETERS                                            │
├────────────┬──────────────────────────────────────────────────────────┤
│  N_Bs      │  Network layer timeout: sender waits for FC             │
│  (max 1s)  │  "How long I'll wait for a Flow Control after I sent    │
│            │   a First Frame."                                        │
│            │  If FC doesn't arrive in time: sender ABORTS.           │
│            │  Test: don't send FC, verify ECU aborts gracefully.     │
├────────────┼──────────────────────────────────────────────────────────┤
│  N_Cr      │  Network layer timeout: receiver waits for CF            │
│  (max 1s)  │  "How long I'll wait for the next Consecutive Frame."  │
│            │  If CF doesn't arrive in time: receiver ABORTS.         │
│            │  Test: drop a CF, verify receiver aborts and next       │
│            │  request works (receiver recovered to IDLE cleanly).    │
├────────────┼──────────────────────────────────────────────────────────┤
│  STmin     │  Minimum Separation Time between CFs                   │
│            │  Advertised by receiver in the FC frame.                │
│            │  Sender must wait at least STmin between each CF.       │
│            │  STmin=0: as fast as possible (no artificial gap)       │
│            │  STmin=10ms: 10ms gap (typical for ECUs with slow CPU)  │
│            │  Test: send CFs faster than STmin → ECU may drop them  │
│            │  (implementation-defined — some ECUs abort, some drop) │
├────────────┼──────────────────────────────────────────────────────────┤
│  BS        │  Block Size — set by receiver in FC                     │
│            │  BS=0: send all CFs without pausing (most common)       │
│            │  BS=N: send N CFs, wait for another FC                  │
│            │  Test: BS=1 forces the sender to request FC after every │
│            │  single CF — maximum flow control, minimum throughput.  │
└────────────┴──────────────────────────────────────────────────────────┘
```

### STmin Encoding Rules

```
┌─────────────┬────────────────────────────────────────────────────────┐
│  Raw value  │  Meaning                                               │
├─────────────┼────────────────────────────────────────────────────────┤
│  0x00       │  0 ms (no gap required — send back-to-back)            │
│  0x01–0x7F  │  1–127 ms (direct: value = milliseconds)              │
│  0x80–0xF0  │  RESERVED — treat as 0                                │
│  0xF1–0xF9  │  100–900 µs (100µs × (value - 0xF0))                 │
│             │  CAN FD high-speed mode only                           │
│  0xFA–0xFF  │  RESERVED                                              │
└─────────────┴────────────────────────────────────────────────────────┘

Your test tool should encode: desired_ms → if ≤ 127: use direct encoding
                              → if 100µs–900µs: use 0xF1–0xF9
                              → otherwise: cap at 0x7F (127ms)
```

---

## 🧠 Concept: DTC Snapshot / Freeze Frame (0x19 0x04)

### What Is a Freeze Frame?

A freeze frame is a **snapshot of sensor values captured at the exact instant a DTC was confirmed**. The ECU stores this snapshot in non-volatile memory alongside the DTC, so it survives power cycles.

```
┌────────────────────────────────────────────────────────────────────────┐
│  0x19 0x04 — reportDTCSnapshotRecordByDTCNumber                       │
│                                                                        │
│  Request:  [0x19, 0x04, DTC_high, DTC_low, record_number]            │
│  Example:  [0x19, 0x04, 0x03, 0x00, 0x01]   ← P0300, record 1        │
│                                                                        │
│  Response:                                                             │
│  [0x59, 0x04,                                                         │
│   DTC_high, DTC_low, DTC_status,                                      │
│   number_of_snapshot_records,                                          │
│   record_number,                                                       │
│   snapshot_data_bytes…]                                               │
│                                                                        │
│  If DTC has no snapshot (not yet confirmed): number_of_records = 0    │
│  If DTC not in ECU: NRC 0x31 requestOutOfRange                       │
└────────────────────────────────────────────────────────────────────────┘
```

### Typical Freeze Frame Content

```
DTC: P0300 Random Misfire Detected
Freeze Frame Record #1 (captured when CDTC bit was set):

  Engine RPM           : 2500 rpm   ← high RPM, under load
  Vehicle Speed        : 45 km/h    ← moving (not at idle)
  Engine Coolant Temp  : 92°C       ← engine warm (not cold-start)
  Engine Load          : 85%        ← heavily loaded
  Short-Term Fuel Trim : +15%       ← ECU adding 15% extra fuel (running lean)

Diagnostic conclusion from freeze frame:
  ● High-load misfire (not idle/cold-start)
  ● Fuel trim +15% suggests lean condition at load
  ● Suspect: weak fuel pump (can't maintain pressure at load),
    clogged fuel injectors, or vacuum leak under load
  ● NOT a MAF sensor cold-start issue (temp=92°C, engine warm)
```

> 🌉 **From your world:** Freeze frame = **application crash dump** or **thread dump at the moment of exception**. You've spent years looking at `java.lang.NullPointerException at line 245` and then reading the stack trace and the last 20 log lines before the crash. Reading a DTC freeze frame is identical: the DTC is the exception class, the status byte is the stack frame, and the sensor values are the variable values at crash time. Same analytical skill — different domain.

### The Freeze Frame Tester Workflow

```
CORRECT TEST for DTC + freeze frame:

  1. Pre-condition: ECU in clean state (no DTCs)
  2. Inject fault condition (HIL signal or ECU write)
  3. Run monitoring cycle (wait for CDTC to be set)
  4. 0x19 0x01 mask=0x08 → verify CDTC count > 0
  5. 0x19 0x04 [DTC code] 0x01 → request freeze frame
  6. Verify:
     a. Correct DTC code echoed in response
     b. Expected sensor values match injected fault scenario
     c. RPM/speed values are within the test's operating range
  7. Clear DTCs
  8. Verify freeze frame is gone (0x19 0x04 → 0 records or NRC 0x31)
```

---

## 🧠 Concept: DTC Extended Data (0x19 0x06) — Occurrence Counter & Ageing

### What Is Extended Data?

Extended data records provide **fault history statistics** — not the sensor state at fault time (that's the freeze frame), but rather how many times the fault has occurred and how close it is to being "aged out."

```
┌────────────────────────────────────────────────────────────────────────┐
│  0x19 0x06 — reportDTCExtDataRecordByDTCNumber                        │
│                                                                        │
│  Request:  [0x19, 0x06, DTC_high, DTC_low, ext_record_number]        │
│  Example:  [0x19, 0x06, 0x03, 0x00, 0x01]   ← P0300, record 1        │
│                                                                        │
│  Response: [0x59, 0x06, DTC_high, DTC_low, DTC_status,               │
│             record_number, occurrence_count, ageing_counter,          │
│             failed_cycles, …OEM-specific additional fields…]          │
└────────────────────────────────────────────────────────────────────────┘
```

### The Extended Data Fields Explained

```
┌───────────────────────────────────────────────────────────────────────┐
│  EXTENDED DATA RECORD (OEM-specific, but typical fields)              │
├──────────────────────┬────────────────────────────────────────────────┤
│  occurrence_count    │  How many drive cycles had CDTC confirmed.     │
│                      │  1 = first time confirmed. 255 = saturates.    │
│                      │  Helps triage: occ=1 could be transient;       │
│                      │  occ=7 is a persistent recurring fault.        │
├──────────────────────┼────────────────────────────────────────────────┤
│  ageing_counter      │  Drive cycles since the fault was last active  │
│                      │  (TF cleared). Counts UP toward the OEM's      │
│                      │  age-out threshold (commonly 40–80 cycles).    │
│                      │  When ageing_counter ≥ threshold: ECU clears   │
│                      │  CDTC automatically (age-out).                 │
│                      │  ageing=0: fault still active (TF bit set)     │
│                      │  ageing=5: 5 clean cycles — healing in progress│
│                      │  ageing=40: at threshold — will age out next   │
├──────────────────────┼────────────────────────────────────────────────┤
│  failed_cycles       │  Drive cycles where TF was set.                │
│                      │  Together with occurrence_count tells you:     │
│                      │  "Confirmed 4 times, failed in 8 cycles" =     │
│                      │  intermittent fault that fails sporadically.   │
└───────────────────────────────────────────────────────────────────────┘
```

### The Ageing Counter and the "40 Clean Drives" Rule

Most OBD-compliant ECUs implement an automatic age-out mechanism:

```
Scenario: P0420 Catalyst Efficiency Low — confirmed 2 years ago.
  Catalyst was replaced. Fault is no longer active (TF=0).

Drive cycle 1 (post-repair):  CDTC still set, ageing_counter = 1
Drive cycle 10:                CDTC still set, ageing_counter = 10
Drive cycle 40:                ageing_counter = 40 → ECU auto-clears CDTC
                               DTC disappears from confirmed list naturally

Without ClearDiagnosticInformation, the DTC self-heals after 40 clean drives.

With ClearDiagnosticInformation: immediate clear regardless of ageing.
                                 But if fault recurs, CDTC comes back.
```

> 🌉 **From your world:** The ageing counter is a **dead man's switch** on test failures. You've probably implemented something like "auto-close bugs that haven't been reproduced in 90 days." The ageing counter is the ECU's equivalent: "if we go N drive cycles without seeing this fault, it was probably transient and we'll self-heal." Same pattern — automated expiry based on elapsed successful cycles.

---

## 🧠 Concept: Permanent DTCs (0x19 0x0B) — The Fault That Survives Clear

### Why Permanent DTCs Exist

This concept comes from **OBD-II regulations** (US EPA, CARB). Before permanent DTCs were introduced, a shady practice existed:

1. Car fails emissions test due to DTC + MIL on
2. Owner/mechanic clears all DTCs with a scan tool
3. Car passes emissions test (no DTCs, no MIL)
4. DTCs come back within days/weeks

To prevent this fraud, OBD-II (ISO 15031 / SAE J1979) mandates **permanent DTCs** that cannot be cleared by any diagnostic tool command. They can only be cleared by the ECU itself, after a successful monitoring cycle confirms the fault is gone:

```
┌────────────────────────────────────────────────────────────────────────┐
│  PERMANENT DTC LIFECYCLE                                               │
│                                                                        │
│  1. Fault detected → DTC confirmed (CDTC set)                         │
│  2. MIL illuminated → permanent DTC stored in separate NVM partition  │
│  3. Scan tool sends ClearDiagnosticInformation (0x14 0xFF 0xFF 0xFF)  │
│  4. All regular DTCs cleared.                                          │
│     Permanent DTC: STATUS UNCHANGED. MIL remains on (emissions test   │
│     will still fail).                                                  │
│  5. Vehicle drives N successful monitoring cycles with NO fault        │
│  6. ECU self-clears the permanent DTC → MIL turns off                 │
│  7. ONLY NOW does the permanent DTC disappear from 0x19 0x0B          │
│                                                                        │
│  Sub-function: 0x0B reportDTCWithPermanentStatus                      │
│  Request:  [0x19, 0x0B]                                               │
│  Response: same format as 0x02 — list of [DTC_H, DTC_L, status]      │
└────────────────────────────────────────────────────────────────────────┘
```

### What "Permanent" Means in Testing

```
For a test engineer, permanent DTC means:

  BEFORE test: 0x19 0x0B → should return 0 DTCs (clean ECU)
  Inject fault that triggers OBD-monitored DTC with MIL
  After confirmation: 0x19 0x0B → returns the DTC
  Call 0x14 0xFF 0xFF 0xFF (clear all)
  AFTER clear: 0x19 0x02 0xFF → 0 regular DTCs  ✓
               0x19 0x0B → STILL returns the DTC  ← this is the test assertion
  Simulate N successful clean drive cycles
  0x19 0x0B → now returns 0 DTCs  ← ECU self-cleared it

If 0x19 0x0B returns 0 after ClearDiagnosticInformation:
  Bug! The ECU is clearing permanent DTCs on tool command.
  This is an OBD compliance failure and an emissions regulation violation.
```

> **Which DTCs become permanent?** Only OBD-monitored emission-related DTCs (P0xxx, certain P1xxx). Network DTCs (U-codes), body DTCs (B-codes), and chassis DTCs (C-codes) are typically not permanent. U0100 (Lost Comm with ECM) in our simulation is permanent as an example — some OEMs do make network faults permanent.

---

## 🧩 The Big Picture: ISO-TP in the Full CAN/UDS Stack

```
┌──────────────────────────────────────────────────────────────────────────┐
│  COMPLETE PROTOCOL STACK — WHERE ISO-TP SITS                            │
│                                                                          │
│  Layer 7 (Application):  UDS ISO 14229                                  │
│  ● Session control, DTC read/write, security, routines                  │
│  ● Max message size: unlimited (segmented by ISO-TP)                    │
│                         │                                               │
│  Layer 4 (Transport):   ISO-TP ISO 15765-2                              │
│  ● Segments UDS messages > 7 bytes into CAN frames                      │
│  ● Provides: flow control, sequence numbering, error detection           │
│  ● NOT: retransmission, encryption, authentication (UDS handles those)  │
│                         │                                               │
│  Layer 2 (Data Link):   CAN ISO 11898-1                                 │
│  ● Individual frames: max 8 bytes classical CAN, 64 bytes CAN FD        │
│  ● Arbitration, error frames, TEC/REC counters (Day 3)                  │
│                         │                                               │
│  Layer 1 (Physical):    CAN bus                                         │
│  ● Differential signalling, termination, bit timing (Days 5–6)          │
└──────────────────────────────────────────────────────────────────────────┘
```

### The CAN Addressing Model with ISO-TP

ISO-TP uses **normal addressing** in our simulations:

```
Normal (11-bit ID) addressing:
  Tester → ECU:  CAN ID 0x7E0  (physical addressing: single ECU)
  ECU → Tester:  CAN ID 0x7E8  (0x7E0 + 8)

  0x7E0–0x7E7: 8 tester CAN IDs
  0x7E8–0x7EF: matching 8 ECU response IDs

  Each {tester_id, ecu_id} pair = one diagnostic channel (one ECU).
  In a car with 50 ECUs, each has its own pair:
  Engine ECU: 0x7E0 / 0x7E8
  Transmission: 0x7E1 / 0x7E9
  ABS: 0x7D0 / 0x7D8
  etc.

Functional addressing (broadcast):
  CAN ID 0x7DF: sends to ALL ECUs simultaneously
  Used by OBD requests that should be answered by any matching ECU
  E.g., OBD Mode 3 "read all DTCs" — every ECU with emission DTCs responds
```

---

## 🌍 Where It's Used in the Real World

| Context | ISO-TP Relevance | Key Test Point |
|---|---|---|
| **OBD-II scan tools** | Every OBD query uses ISO-TP (SF for requests, SF/MF for responses) | Verify tool handles 15+ CF responses for large DTC lists |
| **Firmware flash (OTA)** | Entire flash sequence is ISO-TP multi-frame (10KB+ per TransferData block) | Verify BlockSize handling, N_Bs timeout on FC loss |
| **Freeze frame reading** | 0x19 0x04 response is always multi-frame (20–50 bytes) | Verify freeze frame sensor values match fault injection scenario |
| **DTC extended data** | 0x19 0x06 per-DTC extended data for full DTC catalogue = large MF response | Verify ageing counter resets correctly after ECU reset |
| **Emissions inspection** | 0x19 0x0B permanent DTC check — if any present, car fails inspection | Verify permanent DTC survives ClearDiagnosticInformation |
| **HIL regression** | Every test setup/teardown involves multi-frame DTC reads | Verify ISO-TP state machine recovers cleanly after dropped CF |
| **Fleet telematics** | Remote 0x19 0x02 reads over cellular — long latency affects N_Bs | Tune N_Bs_max for cellular-to-CAN gateway round-trip time |

---

## 🔬 How a Tester Thinks About It

```
┌────────────────────────────────────────────────────────────────────┐
│  TESTER'S CHECKLIST — ISO-TP                                       │
├────────────────────────────────────────────────────────────────────┤
│  FRAME STRUCTURE                                                   │
│  ✓ SF: PCI = length, max 7 UDS bytes, zero-padded to DLC=8?       │
│  ✓ FF: 12-bit total length correct? First 6 payload bytes OK?     │
│  ✓ CF: SN starts at 0x01, wraps 0x0F → 0x00 (not → 0x01)?       │
│  ✓ FC: CTS=0x30, WAIT=0x31, OVERFLOW=0x32? STmin encoded right?  │
│                                                                    │
│  MULTI-FRAME TRANSFER                                              │
│  ✓ Tester sends FC(CTS) within N_Bs_max after FF?                │
│  ✓ CFs arrive within N_Cr_max of each other?                     │
│  ✓ STmin gap between CFs respected (sender side)?                │
│  ✓ BlockSize=0 → all CFs sent without FC pause?                  │
│  ✓ BlockSize=N → FC requested after every N CFs?                 │
│  ✓ Wrong SN → receiver resets to IDLE (doesn't hang)?            │
│  ✓ Dropped CF → receiver times out cleanly, next request works?  │
│                                                                    │
│  FREEZE FRAME (0x19 0x04)                                          │
│  ✓ Confirmed DTC has freeze frame (number_of_records ≥ 1)?       │
│  ✓ Pending-only DTC has no freeze frame (records = 0)?           │
│  ✓ Freeze frame sensor values match fault injection conditions?   │
│  ✓ Unknown DTC code → NRC 0x31?                                   │
│  ✓ Freeze frame cleared after ClearDTC?                          │
│                                                                    │
│  EXTENDED DATA (0x19 0x06)                                         │
│  ✓ Occurrence count increments on each new confirmation?          │
│  ✓ Ageing counter increments on each clean cycle (TF=0)?         │
│  ✓ Ageing counter resets to 0 when TF is set again?              │
│  ✓ Occurrence count NOT reset by ClearDTC (historical record)?   │
│  ✓ Unknown DTC code → NRC 0x31?                                   │
│                                                                    │
│  PERMANENT DTCs (0x19 0x0B)                                        │
│  ✓ Before fault injection: 0x19 0x0B returns 0 DTCs?             │
│  ✓ After MIL-on DTC confirmed: 0x0B returns it?                  │
│  ✓ After ClearDiagnosticInformation: 0x0B STILL returns it?      │
│  ✓ After N clean monitoring cycles: 0x0B returns 0?              │
│  ✓ 0x19 0x02 0xFF after clear: permanent DTC has non-zero status?│
└────────────────────────────────────────────────────────────────────┘
```

### The ISO-TP Debug Flow

When a multi-frame UDS exchange fails, use this systematic approach:

```
Step 1: Count the frames.
  Was the FF received? Did the FC get sent? How many CFs arrived?
  Tools: CAN logger, python-can trace mode.

Step 2: Check the SN sequence.
  Log the lower nibble of each CF's first byte.
  Expected: 1, 2, 3, … F, 0, 1, 2 …
  Any gap → dropped CF. Any repeat → duplicate CF.

Step 3: Check N_Bs and N_Cr timings.
  Did the FC arrive within 1000ms of the FF?
  Did each CF arrive within 1000ms of the previous?

Step 4: Check STmin.
  Did your sender pause at least STmin_ms between CFs?
  If ECU says STmin=25ms and you send back-to-back: some ECUs drop frames.

Step 5: Check BlockSize.
  If BS=N, did you send a fresh FC after every N CFs?
  Missed FC → sender aborts after N_Bs_max.
```

---

## 🛠️ Hands-On Exercise: ISO-TP + Advanced DTC Simulator

### What You'll Build

```
Day-16_ISO_TP_DTC_Advanced/
├── iso_tp_dtc_advanced.py   ← full ISO-TP state machine + 20 test cases
└── Day16_ISO_TP_DTC_Advanced.md
```

**ECU's DTC store (5 DTCs with full extended data):**

```
┌────────┬──────────────────────────────────┬──────┬──────────────────────────────────────┐
│  Code  │  Name                            │Status│  Extended Data                       │
├────────┼──────────────────────────────────┼──────┼──────────────────────────────────────┤
│ 0x0300 │ P0300 Random Misfire             │ 0xAF │ occ=4, ageing=0, failed=8, FF: yes   │
│ 0x0128 │ P0128 Thermostat Stuck Open      │ 0x24 │ occ=1, ageing=0, failed=1, FF: no    │
│ 0x0420 │ P0420 Catalyst Efficiency Low    │ 0x28 │ occ=2, ageing=5, failed=3, FF: yes   │
│ 0xC100 │ U0100 Lost Comm with ECM         │ 0xAF │ occ=7, ageing=0, failed=7, FF: yes   │
│        │                                  │      │ PERMANENT = True                      │
│ 0x0171 │ P0171 Fuel System Too Lean Bank1 │ 0x2C │ occ=3, ageing=2, failed=5, FF: yes   │
└────────┴──────────────────────────────────┴──────┴──────────────────────────────────────┘
```

**ISO-TP implementation highlights:**
- `ISOTPReceiver` state machine class — explicit SF/FF/CF/FC handling
- `build_sf()`, `build_ff()`, `build_cf()`, `build_fc()` — all four frame types
- `segment_message()` — complete segmentation
- ECU sends multi-frame using correct FC wait + STmin pacing
- Tester sends FC(CTS, BS=0, STmin=10ms) after FF

---

### Expected Output

```
🚌📦  🚌📦  🚌📦  🚌📦  🚌📦  🚌📦  🚌📦  🚌📦  🚌📦  🚌📦
  Day 16 — ISO-TP Transport Protocol (ISO 15765-2)
           + Advanced DTC: Snapshot, ExtData, Permanent
🚌📦  🚌📦  🚌📦  🚌📦  🚌📦  🚌📦  🚌📦  🚌📦  🚌📦  🚌📦

────────────────────────────────────────────────────────────────
  GROUP 1: ISO-TP Frame Structure (Unit Tests)
────────────────────────────────────────────────────────────────
  ✅ PASS  TC01 SF structure: PCI=0x02, padding=0x00  [0210030000000000]
  ✅ PASS  TC02 FF structure: total_len=20 encoded in 12-bit  [0x1014...]
  ✅ PASS  TC03 CF SN wrapping: 0x0F → 0x00 ✓
  ✅ PASS  TC04 FC CTS: [0x30, 0x00, 0x0A] ✓  [0x3000...]
  ✅ PASS  TC05 FC WAIT: byte0=0x31 ✓  [0x3100...]
  ✅ PASS  TC06 Segment/reassemble roundtrip 20 bytes ✓
  ✅ PASS  TC07 50-byte payload: FF + 7 CFs ✓  [total=50, CFs=7]

────────────────────────────────────────────────────────────────
  GROUP 2: Live Multi-Frame UDS Exchange
────────────────────────────────────────────────────────────────
  ✅ PASS  TC08 0x19 0x0A multi-frame response  [SID=0x59]
  ✅ PASS  TC08 5 supported DTCs returned via multi-frame ✓
  ✅ PASS  TC09 0x19 0x02 mask=0xFF multi-frame  [SID=0x59]
  ✅ PASS  TC09 5 DTCs via multi-frame mask=0xFF ✓

────────────────────────────────────────────────────────────────
  GROUP 3: DTC Snapshot / Freeze Frame (0x19 0x04)
────────────────────────────────────────────────────────────────
  ✅ PASS  TC10 0x19 0x04 P0300 freeze frame  [SID=0x59]
  ✅ PASS  TC10 P0300 freeze frame: RPM=2500, speed=45  [temp=92°C, load=85%, ftrim=15%]
  ✅ PASS  TC11 0x19 0x04 P0128 no freeze frame  [SID=0x59]
  ✅ PASS  TC11 P0128 snapshot count = 0 (pending, no freeze frame) ✓
  ✅ PASS  TC12 0x19 0x04 unknown DTC → NRC 0x31  [NRC=0x31]

────────────────────────────────────────────────────────────────
  GROUP 4: DTC Extended Data (0x19 0x06)
────────────────────────────────────────────────────────────────
  ✅ PASS  TC13 0x19 0x06 P0300 extended data  [SID=0x59]
  ✅ PASS  TC13 P0300 ext data: occ=4, ageing=0, failed=8 ✓
  ✅ PASS  TC14 0x19 0x06 P0420 ageing counter  [SID=0x59]
  ✅ PASS  TC14 P0420 ageing counter = 5 (counting to age-out) ✓
  ✅ PASS  TC15 0x19 0x06 unknown DTC → NRC 0x31  [NRC=0x31]

────────────────────────────────────────────────────────────────
  GROUP 5: Permanent DTCs (0x19 0x0B)
────────────────────────────────────────────────────────────────
  ✅ PASS  TC16 0x19 0x0B permanent DTCs  [SID=0x59]
  ✅ PASS  TC16 Permanent DTC = U0100 only ✓  [code=0xC100]
  ✅ PASS  TC17 0x19 0x0B after clear  [SID=0x59]
  ✅ PASS  TC17 Permanent DTC survived clear ✓  [U0100 still present]
  ✅ PASS  TC18 0x19 0x02 after clear  [SID=0x59]
  ✅ PASS  TC18 After clear: only permanent U0100 has non-zero status ✓  [count=1]

────────────────────────────────────────────────────────────────
  GROUP 6: ISO-TP BlockSize and STmin
────────────────────────────────────────────────────────────────
  ✅ PASS  TC19 FC blockSize=0: all 5 CFs received with STmin gap ✓  [DTCs=5]
  ✅ PASS  TC20 STmin encoding: 0x00=0ms, 0x14=20ms, 0x7F=127ms ✓

================================================================
  TEST SUMMARY: 25/25 passed, 0 failed
================================================================
```

### Run It

```bash
cd "Day-16_ISO_TP_DTC_Advanced"
pip install python-can
python iso_tp_dtc_advanced.py
```

---

## 🔥 Challenge

### Challenge 1 — 💥 SN Error Detection

Simulate a dropped CF by modifying the ECU to skip CF3 on a large response. Verify that the tester's `ISOTPReceiver` detects the wrong SN and aborts cleanly, then recovers for the next request:

```python
def tc_cf_sn_error_recovery(tester):
    """
    1. Patch ECU to transmit CF with wrong SN (simulate frame loss)
    2. Send 0x19 0x0A (triggers multi-frame response)
    3. Verify: tester returns None (or truncated payload) — not a crash
    4. Send next request: 0x3E 0x00
    5. Verify: tester receives [0x7E, 0x00] — state machine recovered to IDLE
    """
```

### Challenge 2 — ⏱️ N_Bs Timeout Test

Simulate N_Bs timeout by not sending the Flow Control after a First Frame:

```python
def tc_n_bs_timeout(bus_pair):
    """
    1. Send a 20-byte request from tester (triggers FF → ECU waits for FC)
    2. Deliberately do NOT send FC from tester
    3. Wait N_Bs_max + 100ms
    4. Verify: ECU abandoned the transfer (next request works normally)
    5. Measure: did ECU abort within N_Bs_max (1000ms)?
    """
```

### Challenge 3 — 📊 BlockSize=1 Round-Trip

Change the tester's `_recv()` to advertise `BlockSize=1` in the FC:

```python
# In _recv(), change FC to BlockSize=1:
fc_data = build_fc(FC_CTS, 0x01, STMIN_MS)  # BS=1: send 1 CF, then pause

# The ECU must: send CF1, wait for FC, receive FC, send CF2, etc.
# Test: verify the complete response is still received correctly
# (should take longer due to extra FC round-trips)
```

### Challenge 4 — 🔁 DTC Ageing Simulation

Add an `age_one_cycle()` method to `SimulatedECU` that simulates one successful drive cycle:

```python
def age_one_cycle(ecu: SimulatedECU) -> None:
    """
    For each DTC:
    - If TF bit is 0 (fault not active): increment ageing_counter
    - If TF bit is 1 (fault active): reset ageing_counter to 0
    - If ageing_counter >= 40: auto-clear CDTC (age-out)
    """

def tc_ageing_simulation(tester, ecu):
    """
    1. Verify P0420 ageing_counter = 5
    2. Call age_one_cycle() × 35 (total = 40 cycles clean)
    3. 0x19 0x06 P0420 → ageing_counter = 40
    4. age_one_cycle() once more → ageing hits threshold → CDTC cleared
    5. 0x19 0x02 mask=0x08 → P0420 no longer in confirmed list
    """
```

### Challenge 5 — 🔍 Freeze Frame Complete Decode

Write a `decode_snapshot()` function that takes a raw `0x19 0x04` response and returns a human-readable dictionary:

```python
def decode_snapshot(resp: list) -> dict:
    """
    Parse a raw 0x19 0x04 response and return:
    {
        'dtc_code': 0x0300,
        'dtc_status': 0xAF,
        'record_number': 1,
        'engine_rpm': 2500,
        'vehicle_speed_kmh': 45,
        'coolant_temp_c': 92,
        'engine_load_pct': 85,
        'fuel_trim_pct': 15
    }
    """
```

---

## ❓ Quiz + Answers

**Q1.** An ECU is transmitting a 35-byte UDS response. How many CAN frames does this require, and describe each one?

<details>
<summary>Answer</summary>

35 bytes requires **6 CAN frames**: 1 First Frame + 5 Consecutive Frames.

Frame breakdown:
- **FF** (1 frame): PCI = 0x10 0x23 (total length = 35), payload bytes 0–5 (6 bytes)
- **CF1** (SN=0x01): payload bytes 6–12 (7 bytes)
- **CF2** (SN=0x02): payload bytes 13–19 (7 bytes)
- **CF3** (SN=0x03): payload bytes 20–26 (7 bytes)
- **CF4** (SN=0x04): payload bytes 27–33 (7 bytes)
- **CF5** (SN=0x05): payload bytes 34–34 (1 byte + 6 bytes zero padding)

Total transferred: 6 + 5×7 = 41 bytes. The receiver trims to the first 35 bytes.

Plus 1 additional frame: the **Flow Control** sent by the receiver to the sender between FF and CF1. So 7 frames total on the bus (6 data + 1 FC).

</details>

---

**Q2.** The tester receives CF frames with SNs: `0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F, 0x01`. The last SN is `0x01`. Is this correct or a bug?

<details>
<summary>Answer</summary>

This is a **bug**. The Consecutive Frame SN wraps from `0x0F` to `0x00`, NOT to `0x01`.

The correct sequence after `0x0F` should be: `0x0F, 0x00, 0x01, 0x02, …`

Receiving `0x01` after `0x0F` means either:
1. The receiver implementation wraps to `0x01` instead of `0x00` (tester bug)
2. The ECU sent `0x00` (correct) but the tester misread it (parsing bug)
3. Frame `0x20` (SN=0x00) was actually lost on the bus, and the next frame is `0x21` (SN=0x01) — a genuine loss scenario

Either way, the receiver should detect `expected_SN=0x00` vs `received_SN=0x01` as a sequence error and abort the reassembly. This is exactly the bug that causes scan tools to truncate responses for long DTC lists (> 15 CFs = > 111 bytes).

</details>

---

**Q3.** An ECU's Flow Control advertises `BlockSize=3, STmin=20ms`. Describe exactly what the sender (ECU) must do when transmitting a 50-byte message.

<details>
<summary>Answer</summary>

50 bytes = FF (6 bytes) + 7 CFs (6×7=42 + 1×2=2, total 50).

The exchange:
1. ECU sends **FF** (bytes 0–5)
2. Tester sends **FC(CTS, BS=3, STmin=20ms)**
3. ECU sends **CF1** (SN=0x01, bytes 6–12), waits ≥20ms
4. ECU sends **CF2** (SN=0x02, bytes 13–19), waits ≥20ms
5. ECU sends **CF3** (SN=0x03, bytes 20–26) — block of 3 complete, **pause**
6. Tester sends **FC(CTS, BS=3, STmin=20ms)** — second flow control
7. ECU sends **CF4** (SN=0x04, bytes 27–33), waits ≥20ms
8. ECU sends **CF5** (SN=0x05, bytes 34–40), waits ≥20ms
9. ECU sends **CF6** (SN=0x06, bytes 41–47) — another block of 3 complete, **pause**
10. Tester sends **FC(CTS, BS=3, STmin=20ms)** — third FC
11. ECU sends **CF7** (SN=0x07, bytes 48–49, padded) — done

Total CAN frames on bus: 1 FF + 7 CFs + 3 FCs = **11 frames** for 50 bytes.
Compare to BlockSize=0: 1 FF + 7 CFs + 1 FC = **9 frames** (less overhead).

</details>

---

**Q4.** A mechanic clears all DTCs on a vehicle. The next day, the customer returns: the MIL is still on and an emissions test still shows a failure. How is this possible, and which 0x19 sub-function should the mechanic check?

<details>
<summary>Answer</summary>

The vehicle has a **permanent DTC** (ISO 15031 / OBD-II mandated). `ClearDiagnosticInformation` (0x14) clears regular DTCs but cannot clear permanent DTCs. They can only be self-cleared by the ECU after a successful OBD monitoring drive cycle confirms the fault is truly gone.

The mechanic should check `0x19 0x0B` (reportDTCWithPermanentStatus). If it returns one or more DTCs, those are permanent faults that explain the continued MIL illumination.

Next steps:
1. Check `0x19 0x06` extended data for the permanent DTC — how many occurrences? What's the ageing counter?
2. Check `0x19 0x04` freeze frame — what conditions triggered the fault?
3. Actually repair the root cause (e.g., replace the catalyst if P0420)
4. Complete the OBD drive cycle (manufacturer-specific procedure)
5. 0x19 0x0B should then return 0 (ECU self-cleared after successful cycle)

If the mechanic only clears and returns the car without repair: the permanent DTC will persist indefinitely and the car will always fail emissions.

</details>

---

**Q5.** What is the difference between the `ageing_counter` and the `occurrence_count` in DTC extended data, and what does each tell you about a fault?

<details>
<summary>Answer</summary>

**`occurrence_count`** (also called "confirmed fault counter"):
- Counts how many times this DTC has been **confirmed** (CDTC bit set) across separate fault events
- Increments each time the fault is newly confirmed after being absent
- Does NOT reset on ClearDiagnosticInformation (it's a permanent history counter in most implementations)
- Tells you: "This isn't the first time — it's happened 4 times before"
- High occurrence = recurring fault; occ=1 = first-time fault (possibly transient)

**`ageing_counter`**:
- Counts **drive cycles since the fault was last active** (since TF bit was last 1)
- Increments each drive cycle where TF=0 (fault not currently failing)
- Resets to 0 whenever TF=1 (fault becomes active again)
- When ageing_counter ≥ threshold (typically 40): ECU auto-clears CDTC
- Tells you: "5 clean drives since the last failure — could be self-healing" vs. "0 clean drives — still actively failing"

Together they tell the full story:
- `occ=4, ageing=0`: fault confirmed 4 times, still currently failing
- `occ=4, ageing=5`: fault confirmed 4 times, was last active 5 drives ago (healing)
- `occ=1, ageing=35`: happened once 35 drives ago — close to aging out automatically

</details>

---

## 📌 Key Takeaways

```
┌──────────────────────────────────────────────────────────────────┐
│  DAY 16 KEY TAKEAWAYS                                            │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  1. ISO-TP (ISO 15765-2) segments UDS messages > 7 bytes        │
│     into CAN frames. 4 frame types: SF, FF, CF, FC.              │
│     The receiver controls pacing via Flow Control.               │
│                                                                  │
│  2. CF SN wraps 0x0F → 0x00 (not → 0x01).                      │
│     Wrong-SN detection is mandatory — abort and reset to IDLE.  │
│     This bug breaks scan tools for DTC lists > 15 CFs.           │
│                                                                  │
│  3. Flow Control parameters:                                    │
│     FC_flag: CTS=0, WAIT=1, OVERFLOW=2.                         │
│     BlockSize=0 → send all CFs. STmin = minimum CF gap in ms.   │
│     N_Bs_max = 1000ms for sender. N_Cr_max = 1000ms per CF.     │
│                                                                  │
│  4. DTC freeze frame (0x19 0x04) = sensor state at fault time. │
│     Available only for confirmed DTCs. Pending DTCs: 0 records. │
│     This is your crash dump for embedded fault diagnosis.        │
│                                                                  │
│  5. DTC extended data (0x19 0x06) = fault history statistics.  │
│     occurrence_count = how many times confirmed.                 │
│     ageing_counter = clean cycles since last failure.            │
│     Together they tell intermittent vs. persistent vs. healing.  │
│                                                                  │
│  6. Permanent DTCs (0x19 0x0B) survive ClearDiagnosticInfo.    │
│     Only ECU self-clears them after N successful monitoring      │
│     cycles. OBD compliance requirement — test it explicitly.     │
│                                                                  │
│  7. After Day 16, you understand the complete UDS stack:        │
│     CAN physical → CAN framing → ISO-TP segmentation → UDS      │
│     application. No layer is a black box anymore.                │
└──────────────────────────────────────────────────────────────────┘
```

---

```bash
cd "Day-16_ISO_TP_DTC_Advanced"
pip install python-can
python iso_tp_dtc_advanced.py
```

---

*Generated from a live mentoring session with Professor Embed. 🚗⚡📦*
