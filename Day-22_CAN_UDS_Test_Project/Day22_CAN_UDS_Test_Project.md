# Day 22: CAN + UDS Test Automation Framework

> **Professor Embed says:** "You've learned every individual concept — CAN frames, DBC
> files, UDS sessions, DIDs, DTCs, timing, security, traceability. Today we don't learn
> a new concept. Today we **build**. A complete, layered test automation framework that
> wires all of those concepts together into a single, executable, self-reporting
> test suite. Because in industry, what gets you hired is not knowing the theory —
> it's being able to sit down at a keyboard and produce a framework that an ECU supplier
> can actually run."
>
> **Prerequisites:** Days 1–21 (all CAN/UDS/ASPICE/traceability concepts)

---

## What We're Building

A complete test automation framework — 7 layers, each building on the previous:

```
  can_matrix.dbc
       │
       ▼
  DBCParser ──► DBCMessage.encode()  ─────────────────────────────────────┐
               DBCMessage.decode()                                         │
                                                                           ▼
  SimulatedECU  ◄─────────────── EngineStatus broadcast (0x200, 10 ms) ◄─┘
       │  (UDS handler + S3 watchdog + SecurityAccess)
       │
  UDSTester  (ISO-TP request / response)
       │
       ├── TimingVerifier  (cycle-time + P2 latency)
       │
  TestRunner
       ├── run_tc()  (assert, catch, time, log)
       ├── generate_json()  → test_report_YYYYMMDD.json
       └── generate_html()  → test_report_YYYYMMDD.html
                                      │
                              9 test groups / 20 TCs
```

**Files produced by this project:**

| File | Type | Purpose |
|------|------|---------|
| `can_matrix.dbc` | DBC definition | 5 signals on EngineStatus (0x200) |
| `can_test_framework.py` | Python 3 | Framework + ECU simulator + all 20 TCs |
| `test_run_YYYYMMDD.log` | Log file | DEBUG-level hex trace of every UDS byte |
| `test_report_YYYYMMDD.json` | JSON report | Machine-readable results for CI/CD |
| `test_report_YYYYMMDD.html` | HTML report | Human-readable results for management |

---

## Layer 1: The DBC File

`can_matrix.dbc` defines the EngineStatus message (CAN ID `0x200`, 8 bytes DLC):

```
BO_ 512 EngineStatus: 8 ECU
 SG_ EngineRPM    :  0|16@1+ (0.25,0)      [0|16383.75]  "rpm"
 SG_ CoolantTemp  : 16|8@1+  (1,-40)        [-40|215]     "degC"
 SG_ EngineLoad   : 24|8@1+  (0.39216,0)   [0|100]       "%"
 SG_ ThrottlePos  : 32|8@1+  (0.39216,0)   [0|100]       "%"
 SG_ VehicleSpeed : 40|8@1+  (1,0)          [0|255]       "km/h"
```

**Reading a signal definition** — `SG_ EngineRPM : 0|16@1+ (0.25,0) [0|16383.75] "rpm"`:

| Part | Value | Meaning |
|------|-------|---------|
| `0` | start bit | LSBit is at bit 0 of the frame |
| `16` | length | 16 bits wide |
| `@1` | byte order | 1 = Intel (little-endian) |
| `+` | sign | unsigned |
| `0.25` | scale | raw × 0.25 = physical value (rpm) |
| `0` | offset | physical = raw × 0.25 + 0 |
| `[0\|16383.75]` | range | min=0, max=16383.75 rpm |

**Encoding example** (RPM = 3000):
```
raw = round((3000 - 0) / 0.25) = 12000 = 0x2EE0
Bits 0–15 of frame = 0x2EE0
→ byte[0] = 0xE0  byte[1] = 0x2E  (little-endian)
```

**Decoding example** (reading bytes `[0xE0, 0x2E, ...]`):
```
data_int = int.from_bytes(frame_bytes, 'little')
raw = (data_int >> 0) & 0xFFFF = 0x2EE0 = 12000
physical = 12000 × 0.25 + 0 = 3000.0 rpm  ✓
```

> 🌉 **From your world:** A DBC file is the schema definition for a CAN bus — analogous
> to a Protobuf schema or an OpenAPI spec. The DBCParser is the "deserializer."
> Just as you'd use a Protobuf parser to turn binary gRPC payloads into typed objects,
> you use the DBCParser to turn raw CAN bytes into named signal values.

---

## Layer 2: DBC Parser

The `DBCParser` class reads `can_matrix.dbc` and produces a `Dict[int, DBCMessage]`
mapping CAN IDs to message objects. Each `DBCMessage` has two key methods:

```python
msg = dbc[0x200]                       # EngineStatus message

# Encode signals → raw bytes
data = msg.encode({
    "EngineRPM":    3000.0,
    "CoolantTemp":  90.0,
    "VehicleSpeed": 80.0,
})
# → bytearray [0xE0, 0x2E, 0x82, ..., 0x50, 0x00, 0x00]

# Decode raw bytes → signal values
signals = msg.decode(bytes([0xE0, 0x2E, 0x82, 0xBF, 0x40, 0x50, 0x00, 0x00]))
# → {"EngineRPM": 3000.0, "CoolantTemp": 90.0, "EngineLoad": 74.9, ...}
```

**TC01–TC03** verify the parser, encoder, and decoder independently before trusting them
in the integration test.

**TC04** runs the full round-trip on a live virtual bus: set ECU broadcast values → wait
for next EngineStatus frame → decode with DBC → verify the values match.

---

## Layer 3: CAN Simulator (EngineStatus Broadcast)

The `SimulatedECU` does two things simultaneously in one thread:

```
  ECU Thread (runs at ~200 Hz)
  ├── Every 10 ms: broadcast EngineStatus on CAN ID 0x200
  │     Values: ecu.test_rpm, ecu.test_temperature, ecu.test_speed
  │
  ├── Every iteration: check S3 watchdog
  │     if session != defaultSession AND time > last_diag + 2.0s:
  │       → drop to defaultSession (0x01)
  │
  └── Every iteration: recv UDS frame (timeout=5ms)
        if new frame on 0x7E0: dispatch UDS handler
```

**Why periodic broadcasting matters:**
In a real vehicle, every ECU on the bus broadcasts its status messages continuously while
the engine is running. The diagnostic tester sends UDS requests *on top of* this live
traffic. A robust tester must handle interleaved normal traffic and UDS responses. Our
framework uses separate buses on the same virtual channel to model this accurately.

---

## Layer 4: The Timing Verifier

The `TimingVerifier` class has two measurement tools:

### Tool 1: `capture_cycle_times()`

```
  timing_bus.recv() × N  ──► [t₀, t₁, t₂, ..., tₙ]
                                    │
                                    ▼
               deltas = [(tᵢ₊₁ - tᵢ) × 1000 ms for i in range(N-1)]
                                    │
                    ┌───────────────┼───────────────────┐
                   min             mean                 max
```

TC05 captures 10 frames and asserts mean ∈ [5, 20] ms.
The tolerance is generous (±100%) to handle OS scheduler jitter on macOS/Linux VMs.
In production with a PEAK PCAN or KVASER device, you'd tighten this to ±2%.

### Tool 2: `measure_uds_response_ms()`

```
  timing_bus.send(ReadDID request)   → t₀ = now
  timing_bus.recv(ReadDID response)  → elapsed = (now - t₀) × 1000 ms
```

TC06 asserts elapsed < 50 ms (P2 timeout). On a virtual bus, it's typically < 1 ms.
On real hardware, P2 can be anywhere from 5 ms to 25 ms depending on ECU load.

**Why a separate timing_bus for measurements?**
If we used the same bus as the UDS tester (uds_bus), the measurement response would
sit in the uds_bus receive queue and corrupt subsequent UDS transactions.
Using a separate bus for timing measurements keeps the tester's bus clean.

> 🌉 **From your world:** This is the same reason you use separate HTTP clients for
> performance measurements vs. functional tests — you don't want latency measurement
> artefacts leaking into the assertion layer.

---

## Layer 5: The Logging Architecture

The framework uses Python's `logging` module with two handlers:

```
  TestRunner._setup_logger()
       │
       ├── FileHandler (DEBUG level)
       │     Format: "2026-06-22 11:43:00.001  [DEBUG  ]  UDS TX  [22 F1 89]"
       │     File:   test_run_20260622_114300.log
       │     Contains: every UDS byte TX/RX, cycle times, latency values
       │
       └── [Console output via print() for clean TC pass/fail display]
             Format: "  ✅ PASS  TC01  DBC parsed: EngineStatus has correct ID..."
```

**What the log file captures:**
```
2026-06-22 11:43:00.012  [INFO   ]  Framework started: run_id=20260622_114300
2026-06-22 11:43:00.013  [INFO   ]  DBC loaded: 3 messages (7 signals)
2026-06-22 11:43:00.041  [INFO   ]  START  TC01  DBC parsed: EngineStatus...
2026-06-22 11:43:00.042  [DEBUG  ]  EngineStatus: ID=0x200  DLC=8  signals=[...]
2026-06-22 11:43:00.042  [INFO   ]  END    TC01  PASS  (0.1 ms)
...
2026-06-22 11:43:00.215  [DEBUG  ]  UDS TX  [22 F1 89]
2026-06-22 11:43:00.217  [DEBUG  ]  UDS RX  [62 F1 89 44 61 79 32 32 2D 45 43 55 2D 76 31 2E 30]
2026-06-22 11:43:00.217  [INFO   ]  SW Version (0xF189): 'Day22-ECU-v1.0'
```

**Why this matters:** In automotive test automation, the log file is **evidence**.
An ASPICE SWE.6 audit requires that test results are recorded with sufficient detail
to reproduce the test execution. The hex trace of UDS TX/RX bytes is that evidence.

> 🌉 **From your world:** This is equivalent to Playwright trace files or Cypress video
> recordings — not needed when tests pass, invaluable when they fail at 2AM in CI.

---

## Layer 6: The Test Runner

The `TestRunner.run_tc()` method is the core execution engine:

```python
def run_tc(self, tc_id, group, title, fn) -> TestResult:
    self.log.info(f"START  {tc_id}  {title}")
    start = time.monotonic()
    try:
        fn()               # ← call the test function
        status = "PASS"
        detail = ""
    except AssertionError as exc:
        status = "FAIL"    # ← explicit test failure
        detail = str(exc)
    except Exception as exc:
        status = "ERROR"   # ← unexpected exception
        detail = f"{type(exc).__name__}: {exc}"
    duration_ms = (time.monotonic() - start) * 1000
    self.results.append(TestResult(tc_id, group, title, status, ...))
```

**Why `AssertionError` vs `Exception` are handled separately:**
- `AssertionError` (from `assert` statements) = deliberate test verdict
- Other exceptions = framework bug, network error, ECU crash

Mixing them obscures whether a failure is "the ECU returned the wrong NRC" (expected)
vs "the CAN bus timed out" (infrastructure problem). Separating them makes triage faster.

---

## Layer 7: The Report Generator

### JSON Report

Machine-readable output for CI/CD pipelines, dashboards, and traceability tools:

```json
{
  "run_id":    "20260622_114300",
  "project":   "CAN/UDS Test Automation",
  "total":     20,
  "passed":    20,
  "failed":    0,
  "pass_rate": "100%",
  "duration_s": 0.7,
  "test_cases": [
    {
      "tc_id":       "TC01",
      "group":       "CAN Infrastructure",
      "title":       "DBC parsed: EngineStatus has correct ID/DLC/signals",
      "status":      "PASS",
      "duration_ms": 0.12,
      "timestamp":   "2026-06-22T11:43:00",
      "detail":      ""
    }, ...
  ]
}
```

This JSON can be consumed directly by:
- Jenkins/GitLab CI: fail the pipeline if `"failed" > 0`
- Polarion or Jira: import test results against requirement IDs
- Grafana: graph test pass rates over time

### HTML Report

Self-contained HTML with inline CSS — no external dependencies, opens in any browser:

```
┌──────────────────────────────────────────────────────────────────┐
│  🚗 CAN/UDS Test Automation                                      │
│  Run ID: 20260622_114300  |  2026-06-22 11:43:00  |  v1.0.0     │
├────────┬─────────┬─────────┬──────────┬────────────┤
│ Total  │ Passed  │ Failed  │ Pass Rate│ Duration   │
│   20   │   20    │    0    │   100%   │   0.7 s    │
├────────┴─────────┴─────────┴──────────┴────────────┤
│ TC ID │ Group              │ Title          │ Status │ Duration │
│ TC01  │ CAN Infrastructure │ DBC parsed...  │ ✅ PASS│ 0.1 ms  │
│ TC02  │ CAN Infrastructure │ Signal encode..│ ✅ PASS│ 0.2 ms  │
│ ...                                                              │
└──────────────────────────────────────────────────────────────────┘
```

Rows are green for PASS, red for FAIL. The HTML is generated in-process — no templating
engine needed.

> 🌉 **From your world:** Playwright's HTML report, Allure reports, or JUnit XML are
> the web testing equivalents. Same concept — structured results consumed by humans
> and machines. The difference: automotive reports must also be linked to requirement
> IDs and ASIL levels (which is what Day 21 traceability adds on top).

---

## Test Cases Overview

| TC | Group | Technique | Key Assertion |
|----|-------|-----------|---------------|
| TC01 | CAN Infrastructure | Parse DBC | `dbc[0x200].dlc == 8`, all signals present |
| TC02 | CAN Infrastructure | Signal encode | `data[0] == 0xE0`, `data[1] == 0x2E` for RPM=3000 |
| TC03 | CAN Infrastructure | Signal decode | Decoded RPM ≈ 3000, Temp ≈ 90°C, Speed ≈ 80 |
| TC04 | CAN Infrastructure | Round-trip | ECU broadcasts RPM=4000 → decoded == 4000 |
| TC05 | Timing | Cycle time | 10 EngineStatus frames, mean ∈ [5, 20] ms |
| TC06 | Timing | P2 latency | UDS response < 50 ms |
| TC07 | Timing | P2/P2* bytes | Session resp bytes 3–5 = P2=25ms, P2*=5000ms |
| TC08 | ECU Health | TesterPresent | `resp[0] == 0x7E` (ECU alive) |
| TC09 | ECU Health | ECUReset | After reset, session = 0x01 (default) |
| TC10 | Session Control | programmingSession | `resp[0] == 0x50`, `resp[1] == 0x02` |
| TC11 | Session Control | NRC in default | SecurityAccess → NRC 0x22 |
| TC12 | ReadDID | 0xF189 SW version | Response is printable ASCII |
| TC13 | ReadDID | 0xF405 temp sensor | Decoded temperature == 25.0°C |
| TC14 | Negative Response | Unknown DID | `resp[2] == 0x31` (requestOutOfRange) |
| TC15 | Negative Response | Short request | `resp[2] == 0x13` (incorrectMessageLength) |
| TC16 | DTC Lifecycle | Set fault | DTC P0217 confirmed at 0xAF after 110°C |
| TC17 | DTC Lifecycle | Clear fault | DTC P0217 absent after ClearDTC |
| TC18 | Security Access | Seed/Key | Key = seed XOR 0xDEADBEEF → positive resp |
| TC19 | Reports | JSON verify | File exists, valid JSON, ≥18 TCs |
| TC20 | Reports | HTML verify | File exists, contains TC01–TC18, "PASS" present |

---

## The Bus Contamination Problem (and its Fix)

One of the subtlest bugs in embedded test automation — reproduced and fixed in Day 22.

**What happened in the first run:**

```
  TC06: timing_bus.send([ReadDID 0xF186])
         ECU responds → frame goes to BOTH timing_bus AND uds_bus
         timing_bus: consumes it  ✓
         uds_bus:    buffers it   ⚠️  (stale response!)

  TC07: t.switch_session(0x01) → _recv() on uds_bus
         Gets the stale ReadDID response [0x62 0xF1 0x86 0x03]
         Discards it (wrong SID but switch_session doesn't assert)

  TC07: t.sr([0x10 0x03]) → _recv() on uds_bus
         Gets the stale session response to switch_session(0x01)
         By coincidence: P2/P2* bytes are identical for any session → TC07 PASSES

  TC08: t.sr([TesterPresent]) → _recv() on uds_bus
         Gets the stale session response to [0x10 0x03] from TC07
         resp[0] = 0x50 ≠ 0x7E  → FAIL ❌
```

**The fix:** Two changes:
1. `TimingVerifier.measure_uds_response_ms()` uses `self.bus` (timing_bus) for TX,
   not uds_bus. Fewer buses touch the request.
2. `_drain_bus(uds_bus)` called between GROUP 2 and GROUP 3. Any residual frames
   from timing measurements are discarded before the UDS tester takes over.

**The lesson:** In a shared virtual bus environment, **every frame sent by anyone
goes to every bus on that channel**. This is how real vehicle CAN works too
(everyone hears everything). Your test infrastructure must account for this — either
by draining queues at known transition points or by using separate channels for
different test layers.

---

## Signal Encoding Math Reference

For a signal with `scale=S, offset=O, little-endian, start_bit=b, length=L`:

$$\text{physical} = \text{raw} \times S + O$$

$$\text{raw} = \text{round}\!\left(\frac{\text{physical} - O}{S}\right)$$

**Encoding into frame bytes (Intel byte order):**
$$\text{frame\_int}\ \mathrel{|}=\ (\text{raw}\ \&\ \text{mask}_L) \ll b$$

**Decoding from frame bytes:**
$$\text{raw} = \frac{\text{frame\_int} \gg b}{\&\ \text{mask}_L}$$

where $\text{mask}_L = (1 \ll L) - 1$

**Example — CoolantTemp at bit 16, length 8, scale 1, offset −40:**
- Physical 90°C → raw = round((90 − (−40)) / 1) = 130 = 0x82
- Byte 2 of frame = 0x82
- Verify: 0x82 = 130 → 130 × 1 + (−40) = 90°C ✓

---

## What the Log File Tells a Senior Engineer

Open `test_run_YYYYMMDD.log` after any run. It tells you:

```
2026-06-22 11:43:00.215  [DEBUG  ]  UDS TX  [22 F1 89]
```
→ Tester sent ReadDID(0xF189) — 3-byte SF PDU

```
2026-06-22 11:43:00.217  [DEBUG  ]  UDS RX  [62 F1 89 44 61 79...]
```
→ ECU responded in 2ms with positive response, data starts at byte 3

```
2026-06-22 11:43:00.218  [INFO   ]  SW Version (0xF189): 'Day22-ECU-v1.0'
```
→ Test layer decoded the ASCII string

If TC12 FAILS, the log shows you **exactly what bytes the ECU sent**, so you know
whether the problem is the parser, the encoder, or the ECU firmware. No guessing.

On real hardware, this same log would show you:
- Was the request even sent? (TX line)
- Did the ECU respond? (RX line)
- What was the exact byte the ECU sent? (NRC code, wrong DID echo, wrong length)

> 🌉 **From your world:** This is the browser DevTools Network tab — but for CAN.
> The hex bytes are your request/response headers. The timing is your TTFB.
> The NRC code is your HTTP 4xx. The log is your HAR file.

---

## Quiz

**Q1.** TC02 encodes RPM=3000 and asserts `data[0] == 0xE0, data[1] == 0x2E`.
If a developer changes the EngineRPM scale from 0.25 to 0.5 in the DBC, what will TC02
do, and what does that tell you about the test design?

<details><summary>Answer</summary>

**TC02 will FAIL.** With scale=0.5, encoding RPM=3000 gives:
```
raw = round(3000 / 0.5) = 6000 = 0x1770
byte[0] = 0x70  byte[1] = 0x17
```
But TC02 asserts `data[0] == 0xE0` (the old encoding). AssertionError is raised.

**What this tells you:**
TC02 is a "snapshot test" — it detects any change in encoding, intentional or not.
The failure doesn't tell you if the DBC change was correct (maybe the spec changed to
scale=0.5). It just raises a flag that you need to review:
1. Was the DBC change intentional?
2. Was the test expectation updated to match?
3. If the DBC changed without updating the test, the test caught an undocumented
   interface change — which is exactly what TC02 is for.

This is equivalent to a Cypress snapshot test catching a UI layout change.
The test doesn't know if the change was right or wrong. It just says: "something changed."
A human must then decide if the change was intentional and update the snapshot.

</details>

---

**Q2.** TC06 measures UDS P2 response time on a virtual bus and gets 0.3 ms.
On real hardware with the same ECU firmware, P2 is measured at 42 ms.
The test limit is 50 ms so both pass. Is there a problem?

<details><summary>Answer</summary>

**Yes, there is a latent risk.** The 42 ms on real hardware is dangerously close to
the 50 ms P2 limit. On a real bus with:
- CAN bus load at 70%+ (many ECUs broadcasting simultaneously)
- ECU under firmware stress test (flash write in progress)
- Higher ambient temperature (ECU clock speed reduced by thermal protection)

...the P2 response time could exceed 50 ms. The test passes with a 0.3 ms "virtual bus
shortcut" — it gives you false confidence that timing is fine.

**Proper mitigation:**
1. Tighten the P2 limit to 30 ms in the test (leaves 20 ms margin)
2. Run the P2 test WITH background CAN bus load (simulate vehicle traffic)
3. Run P2 test during ECU boot, during flash, and at max temperature
4. If hardware response is consistently > 30 ms, raise a performance defect

**Industry reality:** P2 timeouts are a common cause of ECU diagnostic failures in
the field. A car in a dealer with 15 ECUs on the bus has dramatically different P2
timing than the same ECU on a lab bench with only the tester connected.

</details>

---

**Q3.** TC15 sends `[0x22, 0xF1]` (1-byte DID instead of 2) and expects NRC 0x13.
What would happen if the ECU instead responded with a positive response `[0x62, 0xF1, ...]`?
What does this indicate about the ECU firmware?

<details><summary>Answer</summary>

If the ECU responds positively to a malformed request, TC15 **FAILS** with:
```
AssertionError: Expected NRC 0x7F, got 0x62 (service was not rejected)
```

**What this indicates:**
The ECU firmware is parsing incomplete DID requests incorrectly. Specifically:
- The firmware is reading `uds[1] = 0xF1` as a complete 1-byte DID
- It happens to find or fabricate a response for DID `0xF1`
- Or it's treating the missing byte as `0x00`, so it serves DID `0xF100`

**Security implications:**
This is an out-of-specification behavior that can be exploited:
1. An attacker might discover DIDs by sending partial DID bytes and observing which
   get positive responses
2. The ECU might return unexpected data from the wrong DID
3. If DID `0xF100` contains sensitive calibration data, it leaks via malformed request

**ISO 14229-1 compliance:**
Per the standard, if the ReadDataByIdentifier request does not contain exactly 2 bytes
for the DID, the ECU SHALL respond with NRC 0x13 (incorrectMessageLengthOrInvalidFormat).
The ECU is non-compliant.

**Action:** File a firmware defect, severity = High (incorrect negative response behavior).
Reference: ISO 14229-1, Section 10.1, Table 51, Row "incorrectMessageLength".

</details>

---

**Q4.** The `_drain_bus()` function is called between GROUP 2 (Timing) and GROUP 3
(ECU Health) in `main()`. A developer says: "Why not call `_drain_bus()` at the start
of every TC function to be safe?" What are the tradeoffs?

<details><summary>Answer</summary>

**Arguments for draining at the start of every TC:**
- Maximum isolation — each TC starts with a guaranteed clean bus state
- Easier to debug failures (no inter-TC contamination possible)
- Simpler mental model — no need to trace which TC left what in the queue

**Arguments against (why we don't do it):**
1. **Timing cost:** `_drain_bus(window_s=0.05)` takes 50 ms. For 20 TCs, that's 1 extra
   second of drain overhead — 5× longer than the actual test execution (0.7 s total).
   In production test suites with thousands of TCs, this adds hours.

2. **Hides state coupling bugs:** If you drain before every TC, you mask problems
   where TC-A leaves the ECU in a bad state that TC-B depends on. Finding that coupling
   is actually valuable — it tells you the tests are stateful and need a reset.

3. **Not realistic:** In real HIL testing, you can't afford to drain the entire CAN
   bus buffer before every test case. The vehicle keeps transmitting. Your tester must
   be robust enough to handle that.

**Best practice (what we use):**
Drain surgically — only at known "layer transition" points where different test
infrastructure (timing bus vs. UDS bus) has contaminated the shared channel.
Keep TCs within a group using the same bus convention so no drain is needed between them.

</details>

---

**Q5.** TC18 (Security Access) computes `key = seed XOR SEC_SECRET`. The `SEC_SECRET`
is defined as `0xDEADBEEF` in the test code. What is the security problem with this,
and how would a real ECU supplier handle it?

<details><summary>Answer</summary>

**The security problem:**
`SEC_SECRET = 0xDEADBEEF` is a well-known placeholder. By having it hardcoded in the
test framework (which may be committed to a public Git repository or sent to suppliers),
you have effectively published the ECU's key derivation secret. Anyone who reads the
test code can unlock any ECU using this algorithm.

**Real ECU suppliers handle this in several ways:**

1. **Separate key files from test code:** The `SEC_SECRET` is loaded from an encrypted
   HSM (Hardware Security Module) or a password-protected key store at runtime.
   The test code just calls `compute_key(seed)` without knowing how it works.

2. **Supplier-provided DLL/library:** BMW's SGS, Daimler's CESAR, etc. use a
   standardized "SecurityAccess DLL" interface (defined in AUTOSAR). The supplier
   provides a compiled DLL that computes the key — you never see the algorithm.

3. **ASIL-decomposed security:** For ASIL-C/D functions behind security access,
   the key algorithm is reviewed by functional safety engineers and may require
   formal verification (not just a single XOR).

4. **Time-limited seeds:** Real ECUs often include a timestamp in the seed so that
   a captured seed/key pair cannot be replayed hours later.

5. **Anti-replay counters:** Some ECUs increment a counter with each security access
   attempt. Seed replay (reusing an old seed with a precomputed key) is rejected.

**In TC18's context:** The hardcoded `0xDEADBEEF` is fine for a simulation that teaches
the mechanism. In production, it would be a CRITICAL security finding.

</details>

---

## Key Takeaways

1. **The framework is in layers because bugs are in layers.** When TC06 fails, you know
   it's a P2 timing issue. When TC15 fails, you know it's an NRC handling bug. When TC04
   fails, it's an encoding issue. Each layer can fail independently.

2. **Bus contamination is real and subtle.** On a shared virtual CAN channel, every
   frame goes to every bus. `_drain_bus()` is not a hack — it's the canonical way to
   reset bus state between test phases that use different buses.

3. **The log file IS the test evidence.** The hex bytes logged for every UDS TX/RX are
   what an ASPICE auditor or an ECU supplier's integration engineer will look at when
   a test fails in CI at 2AM. Design your logging for the post-failure investigation,
   not the success case.

4. **JSON output enables CI/CD integration.** A 30-line Python job in Jenkins can parse
   `test_report.json` and fail the pipeline if `"failed" > 0` or `"pass_rate" != "100%"`.
   The test framework becomes a gate in the development pipeline.

5. **Separation of concerns makes tests maintainable.** The `SimulatedECU`, `UDSTester`,
   `TimingVerifier`, and `TestRunner` are independent classes. You can replace the
   simulated ECU with a real PCAN device by changing just the `UDSTester` bus creation —
   the test functions themselves don't change.

6. **Run everything in < 1 second.** The full 20-TC suite runs in 0.7 seconds. This is
   fast enough to run on every commit, every merge, every CI trigger. A test suite that
   takes 30 minutes gets run less often — and catches bugs later.

---

## From Simulation to Hardware

To run this framework against a real ECU instead of the simulation:

```python
# Instead of:
ecu   = SimulatedECU()          # virtual
uds_bus = can.Bus(interface="virtual", channel="vcan0")

# Use:
uds_bus = can.Bus(interface="pcan", channel="PCAN_USBBUS1", bitrate=500000)
# or:
uds_bus = can.Bus(interface="socketcan", channel="can0")

# Skip ecu.start()
# Run the same test_run_tc() calls — nothing else changes.
```

The only tests that need real ECU interaction are TC04–TC20 (the ECU-dependent ones).
TC01–TC03 (DBC codec) don't need the bus at all.

---

## Running the Framework

```bash
cd "Day-22_CAN_UDS_Test_Project"
pip install python-can
python can_test_framework.py
```

**Expected output:**
- 20/20 TCs pass in ~0.7 seconds
- Three output files created: `.log`, `.json`, `.html`
- Open `test_report_YYYYMMDD.html` in any browser for the visual report

**To intentionally break a test** (learning exercise):
```python
# In can_matrix.dbc, change EngineRPM scale from 0.25 to 0.5
# Re-run → TC02 fails: "data[0]: expected 0xE0, got 0x70"
# Demonstrates that TC02 detects DBC encoding changes
```

---

## Project Architecture Summary

```
can_test_framework.py
│
├── DBCParser          LAYER 1  Parses can_matrix.dbc → Dict[CAN_ID, DBCMessage]
├── DBCMessage         LAYER 1  .encode(signals) → bytes   .decode(bytes) → signals
│
├── TimingVerifier     LAYER 2  capture_cycle_times()  measure_uds_response_ms()
│
├── TestResult         LAYER 3  Dataclass: tc_id, status, duration_ms, detail
├── TestRunner         LAYER 3  run_tc(), generate_json(), generate_html(), logging
│
├── SimulatedECU       LAYER 4  EngineStatus broadcast + full UDS handler
├── UDSTester          LAYER 5  ISO-TP SR helper + UDS service wrappers
│
├── _drain_bus()       UTIL     Flush receive queue between test phases
│
└── TC01–TC20          TESTS    9 groups, each tests one architectural layer
```

---

*Generated from a live mentoring session with Professor Embed. 🚗⚡🔥*
