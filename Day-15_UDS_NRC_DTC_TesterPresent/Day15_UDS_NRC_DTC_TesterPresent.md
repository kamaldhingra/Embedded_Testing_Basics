# 🩺🔔 Day 15: UDS — Complete NRC Reference, TesterPresent (0x3E), ReadDTCInformation (0x19) & ClearDiagnosticInformation (0x14)

> **Course:** Embedded & IoT Testing Masterclass
> **Instructor:** Professor Embed
> **Prerequisites:** Day 1–14 (Full CAN + UDS Services 0x10, 0x11, 0x22, 0x2E, 0x27, 0x31)

---

## 📚 Table of Contents

1. [Recap: The UDS Services You Already Know](#recap)
2. [Concept: The Complete NRC Reference — Every Code Explained](#concept-nrc-complete)
3. [Concept: NRC Testing Methodology — Systematic Negative Path](#concept-nrc-methodology)
4. [Concept: The suppressPosRspMsgIndicationBit — Bit 7 of Sub-Functions](#concept-suppress)
5. [Concept: TesterPresent (0x3E) — The Heartbeat That Keeps Sessions Alive](#concept-tester-present)
6. [Concept: The DTC Lifecycle — From First Fault to Cleared](#concept-dtc-lifecycle)
7. [Concept: The DTC Status Byte — Eight Bits, Eight Questions](#concept-status-byte)
8. [Concept: Service 0x19 — ReadDTCInformation](#concept-0x19)
9. [Concept: Service 0x14 — ClearDiagnosticInformation](#concept-0x14)
10. [Concept: CommunicationControl (0x28) — Muting the Bus](#concept-0x28)
11. [Concept: InputOutputControl (0x2F) — Forcing ECU Outputs](#concept-0x2f)
12. [The Big Picture: Complete UDS Service Map](#the-big-picture)
13. [Where It's Used in the Real World](#where-its-used)
14. [How a Tester Thinks About It](#how-a-tester-thinks)
15. [Hands-On Exercise: DTC & TesterPresent Simulator](#hands-on-exercise)
16. [Quiz + Answers](#quiz--answers)
17. [Key Takeaways](#key-takeaways)

---

## 🔁 Recap: The UDS Services You Already Know

After Days 12–14 you can control a full UDS diagnostic session:

| Day | Services | What you can do |
|---|---|---|
| 12 | 0x10, 0x11 | Switch sessions, reset ECU, understand S3 timer |
| 13 | 0x22, 0x2E | Read and write named data (DIDs) |
| 14 | 0x27, 0x31 | Unlock security, trigger routines |

One important pattern has been lurking since Day 12 without getting its own lesson: **what do you do when you need a session to stay alive for minutes** (e.g., a long calibration sequence)? And the Day 3 equivalent for UDS — **how do you read, filter, and clear fault codes** the way every OBD tool in the world does?

Today closes both gaps and also delivers something that applies to *everything* you've done so far: the **complete NRC reference** — the definitive answer to "what does this error code actually mean?"

> *"DTCs are what the customer sees on the dashboard. Understanding how to read, interpret, and clear them — and how to keep your test session alive long enough to actually do it — is table stakes for any embedded test engineer."*

---

## 🧠 Concept: The Complete NRC Reference — Every Code Explained

### Every NRC You Will Ever Encounter

The negative response `[0x7F, SID, NRC]` is the ECU's error vocabulary. After Days 12–14 you've seen NRCs 0x12, 0x13, 0x22, 0x24, 0x31, 0x33, 0x35, 0x36, 0x37, and 0x78. Now the complete table:

```
┌────────┬─────────────────────────────────────────────────┬────────────────────────────┐
│  NRC   │  ISO 14229 Name                                 │  HTTP / SW Analogy         │
├────────┼─────────────────────────────────────────────────┼────────────────────────────┤
│  0x10  │  generalReject                                  │  500 Internal Server Error  │
│        │  ECU received the request but can't process it  │                            │
│        │  for an unspecified reason. A catch-all.        │                            │
├────────┼─────────────────────────────────────────────────┼────────────────────────────┤
│  0x11  │  serviceNotSupported                            │  404 Not Found             │
│        │  The SID doesn't exist in this ECU at all.      │  (feature not implemented) │
├────────┼─────────────────────────────────────────────────┼────────────────────────────┤
│  0x12  │  subFunctionNotSupported                        │  404 on sub-resource       │
│        │  The SID exists but the sub-function byte       │                            │
│        │  value is not valid.                            │                            │
├────────┼─────────────────────────────────────────────────┼────────────────────────────┤
│  0x13  │  incorrectMessageLengthOrInvalidFormat          │  400 Bad Request           │
│        │  Wrong number of bytes, or malformed payload.   │  (schema validation fail)  │
├────────┼─────────────────────────────────────────────────┼────────────────────────────┤
│  0x14  │  responseTooLong                                │  413 Payload Too Large     │
│        │  ECU's response would exceed ISO-TP max.        │                            │
├────────┼─────────────────────────────────────────────────┼────────────────────────────┤
│  0x21  │  busyRepeatRequest                              │  429 Too Many Requests     │
│        │  ECU is processing a previous request.          │  (server busy, retry)      │
│        │  Resend the exact same request after a delay.   │                            │
├────────┼─────────────────────────────────────────────────┼────────────────────────────┤
│  0x22  │  conditionsNotCorrect                           │  412 Precondition Failed   │
│        │  The right precondition is not met. Usually:    │  (state dependency failed) │
│        │  wrong session, wrong vehicle state, or wrong   │                            │
│        │  sequence of operations.                        │                            │
├────────┼─────────────────────────────────────────────────┼────────────────────────────┤
│  0x24  │  requestSequenceError                           │  409 Conflict              │
│        │  Operations were done in the wrong order.       │  (state machine conflict)  │
│        │  Most common: sendKey (0x27 0x02) without       │                            │
│        │  requestSeed (0x27 0x01) first.                 │                            │
├────────┼─────────────────────────────────────────────────┼────────────────────────────┤
│  0x25  │  noResponseFromSubnetComponent                  │  503 Service Unavailable   │
│        │  ECU tried to communicate with an internal      │  (gateway upstream timeout)│
│        │  sub-network node and got no response.          │                            │
├────────┼─────────────────────────────────────────────────┼────────────────────────────┤
│  0x26  │  failurePreventsExecOfRequestedAction           │  503 (upstream dependency) │
│        │  An active fault prevents this operation.       │                            │
│        │  E.g., can't start calibration with active      │                            │
│        │  engine misfire.                                │                            │
├────────┼─────────────────────────────────────────────────┼────────────────────────────┤
│  0x31  │  requestOutOfRange                              │  422 Unprocessable Entity  │
│        │  DID/RID not supported, or value out of         │  (domain validation fail)  │
│        │  allowed range.                                 │                            │
├────────┼─────────────────────────────────────────────────┼────────────────────────────┤
│  0x33  │  securityAccessDenied                           │  401 Unauthorized          │
│        │  Security access (0x27) not unlocked, or the    │  (no valid token)          │
│        │  operation requires a higher security level.    │                            │
├────────┼─────────────────────────────────────────────────┼────────────────────────────┤
│  0x34  │  authenticationRequired                         │  401 (auth required)       │
│        │  ISO 14229-2 Authentication service needed.     │  Modern PKI-based ECUs.    │
├────────┼─────────────────────────────────────────────────┼────────────────────────────┤
│  0x35  │  invalidKey                                     │  401 (wrong password)      │
│        │  SecurityAccess key comparison failed.          │                            │
│        │  Attempt counter incremented.                   │                            │
├────────┼─────────────────────────────────────────────────┼────────────────────────────┤
│  0x36  │  exceededNumberOfAttempts                       │  429 (account locked)      │
│        │  SecurityAccess locked out after N wrong keys.  │                            │
├────────┼─────────────────────────────────────────────────┼────────────────────────────┤
│  0x37  │  requiredTimeDelayNotExpired                    │  429 (cooldown active)     │
│        │  Lockout timer still running. Wait before retry.│                            │
├────────┼─────────────────────────────────────────────────┼────────────────────────────┤
│  0x70  │  uploadDownloadNotAccepted                      │  406 Not Acceptable        │
│        │  RequestDownload (0x34) parameters rejected.    │  (flash preconditions)     │
├────────┼─────────────────────────────────────────────────┼────────────────────────────┤
│  0x71  │  transferDataSuspended                          │  500 (transfer aborted)    │
│        │  TransferData (0x36) interrupted.               │                            │
├────────┼─────────────────────────────────────────────────┼────────────────────────────┤
│  0x72  │  generalProgrammingFailure                      │  500 (write failed)        │
│        │  Flash write / erase failed at hardware level.  │                            │
├────────┼─────────────────────────────────────────────────┼────────────────────────────┤
│  0x73  │  wrongBlockSequenceCounter                      │  409 (out-of-order chunks) │
│        │  TransferData (0x36) block counter wrong.       │                            │
├────────┼─────────────────────────────────────────────────┼────────────────────────────┤
│  0x78  │  requestCorrectlyReceived–ResponsePending       │  102 Processing            │
│        │  (RCRRP) — "Got it, still working, wait."       │  NOT an error — extend     │
│        │  ECU will send the real response within P2*.    │  the timeout and wait.     │
├────────┼─────────────────────────────────────────────────┼────────────────────────────┤
│  0x7E  │  subFunctionNotSupportedInActiveSession         │  405 Method Not Allowed    │
│        │  Sub-function exists but not in current session.│  (try a different session) │
├────────┼─────────────────────────────────────────────────┼────────────────────────────┤
│  0x7F  │  serviceNotSupportedInActiveSession             │  405 Method Not Allowed    │
│        │  Service exists but can't be used here.         │  (upgrade your auth level) │
└────────┴─────────────────────────────────────────────────┴────────────────────────────┘
```

### The Three Critical Distinctions

#### 1. NRC 0x11 vs 0x7F

```
0x11 serviceNotSupported           → the SID simply doesn't exist in this ECU.
                                     "There's no such door."

0x7F serviceNotSupportedInActive   → the SID exists, but not in your session.
     Session                         "The door exists but your keycard level
                                      doesn't open it. Upgrade your session."
```

#### 2. NRC 0x12 vs 0x7E

```
0x12 subFunctionNotSupported       → the sub-function byte value doesn't exist
                                     at all for this service.
                                     "0x10 0x99 → session type 0x99 doesn't
                                      exist anywhere in the spec."

0x7E subFunctionNotSupportedIn     → the sub-function exists but isn't allowed
     ActiveSession                   in this session.
                                     "0x10 0x02 (programming) from default →
                                      programming session exists, just not from here."
```

#### 3. NRC 0x22 vs 0x24

```
0x22 conditionsNotCorrect          → the right STATE isn't met. Could be
                                     wrong session, vehicle speed > 0, engine
                                     running, wrong ECU mode. Anything contextual.

0x24 requestSequenceError          → the right ORDER wasn't followed.
                                     "You need to do A before B, and you tried
                                      B first." Classic: sendKey without requestSeed.
```

> 🌉 **From your world:** You know all these from HTTP. `0x11 = 404`, `0x7F = 405`. `0x12 = 404` on a sub-resource, `0x7E = 405` on a sub-resource. `0x22 = 412`, `0x24 = 409`. The vocabulary is different but the semantics are identical. You already speak this language.

---

## 🧠 Concept: NRC Testing Methodology — Systematic Negative Path

A common mistake junior testers make: test the happy path, pass, ship. A senior embedded test engineer tests **every NRC the spec says should fire** — and also tests that **no unexpected NRC fires**.

### The NRC Derivation Matrix

For any UDS service, derive test cases by asking:

```
┌───────────────────────────────────────────────────────────────────────┐
│  FOR EVERY SERVICE, ASK THESE QUESTIONS:                              │
├───────────────────────────────────────────────────────────────────────┤
│  SESSION GATING                                                       │
│  Q: Is this service available in all sessions?                        │
│  If not: test the disallowed sessions → expect NRC 0x22 or 0x7F      │
│                                                                       │
│  SUB-FUNCTION VALIDATION                                              │
│  Q: What sub-function values are valid?                               │
│  Test: send boundary values above the valid range → NRC 0x12          │
│  Test: send 0x00 for services where it's not valid → NRC 0x12         │
│                                                                       │
│  MESSAGE LENGTH                                                       │
│  Q: What is the exact required length?                                │
│  Test: send 1 byte less → NRC 0x13                                    │
│  Test: send 1 byte more (for fixed-length services) → NRC 0x13        │
│                                                                       │
│  DATA RANGE                                                           │
│  Q: Are there min/max constraints on data values?                     │
│  Test: min-1, min, max, max+1 → BVA on DID/RID values → NRC 0x31     │
│                                                                       │
│  SECURITY                                                             │
│  Q: Does this operation require SecurityAccess?                       │
│  Test: attempt without unlock → NRC 0x33                              │
│  Test: attempt with correct unlock → positive response                │
│                                                                       │
│  SEQUENCE                                                             │
│  Q: Does this operation have a prerequisite?                          │
│  Test: skip the prerequisite → NRC 0x24                               │
└───────────────────────────────────────────────────────────────────────┘
```

### The "NRC Tells You Where to Look" Rule

When a real ECU returns an unexpected NRC during testing, don't guess the cause — read the NRC:

```
Got NRC 0x22? → Check session. Check vehicle speed. Check ECU state.
Got NRC 0x24? → Check the order of your requests. Something came in wrong order.
Got NRC 0x31? → Check DID/RID table. Check data range. Check spec version.
Got NRC 0x33? → SecurityAccess not performed. Or performed for wrong level.
Got NRC 0x12? → Sub-function value doesn't exist. Check spec for valid values.
Got NRC 0x13? → Count your bytes. Wrong number sent. Check message format.
Got NRC 0x78? → NOT an error. Extend timeout, wait for real response.
Got NRC 0x11? → Feature not in this ECU variant. Check part number / config.
```

> **Senior tester mantra:** *"The NRC is the ECU's error message. Read it before debugging anything else."* A tester who ignores the NRC and starts probing the CAN bus has skipped the most informative diagnostic tool available.

---

## 🧠 Concept: The suppressPosRspMsgIndicationBit — Bit 7 of Sub-Functions

This is a **general UDS feature** that applies to *every* service with a sub-function byte — not just TesterPresent. But it's most visible with TesterPresent.

```
┌───────────────────────────────────────────────────────────────────┐
│  SUB-FUNCTION BYTE STRUCTURE                                       │
│                                                                   │
│  Bit 7:  suppressPosRspMsgIndicationBit (SPRMIB)                 │
│  Bits 6-0: actual sub-function value                              │
│                                                                   │
│  Examples:                                                        │
│    0x00 = zeroSubFunction, respond normally                       │
│    0x80 = zeroSubFunction, SUPPRESS the positive response         │
│    0x01 = sub-function 1, respond normally                        │
│    0x81 = sub-function 1, suppress the positive response          │
│    0x03 = extendedDiagnosticSession, respond normally             │
│    0x83 = extendedDiagnosticSession, suppress response            │
└───────────────────────────────────────────────────────────────────┘
```

**The rule:** When SPRMIB (bit 7) is set:
- ECU **processes the request normally** (resets S3, changes session, etc.)
- ECU sends **no positive response**
- If there's an **error**, ECU **still sends the NRC** (negative responses are never suppressed)

**Why does this exist?** To reduce bus traffic. A TesterPresent sent every 500ms would flood the bus with matching responses. With SPRMIB, the ECU silently resets the timer with no response traffic. This matters on a heavily loaded bus during a long calibration or flash operation.

> 🌉 **From your world:** This is like an HTTP request with `Prefer: respond-async` or a UDP packet that expects no ACK. "Process this, but don't bother confirming unless there's a problem." You've written fire-and-forget API calls. Same concept.

---

## 🧠 Concept: TesterPresent (0x3E) — The Heartbeat That Keeps Sessions Alive

### The Pacemaker Analogy 🏥

A pacemaker sends a tiny electrical pulse every second. If it stops, the heart knows something is wrong and goes into a safe fallback rhythm. The S3 timer is the ECU's pacemaker check: if it doesn't hear from the tester for 5 seconds, it assumes the connection is dead and drops back to defaultSession.

**TesterPresent (0x3E)** is the heartbeat that keeps the pacemaker happy:

```
┌──────────────────────────────────────────────────────────────────────┐
│  TESTER PRESENT — WIRE FORMAT                                       │
│                                                                      │
│  Request:                                                            │
│  [0x3E, 0x00]                                                       │
│   ^^^^  ^^^^                                                         │
│   SID   sub=zeroSubFunction, respond normally                        │
│                                                                      │
│  [0x3E, 0x80]                                                       │
│   ^^^^  ^^^^                                                         │
│   SID   sub=0x00 | 0x80 (suppress positive response)                │
│                                                                      │
│  Positive Response (sub=0x00 only):                                 │
│  [0x7E, 0x00]                                                       │
│   ^^^^  (0x3E + 0x40)                                               │
│                                                                      │
│  No response (sub=0x80): ECU processes, S3 timer reset, bus silent  │
└──────────────────────────────────────────────────────────────────────┘
```

### When TesterPresent Is Required

```
Scenario: Automated calibration sequence taking 30 seconds.

  t=0s    switch to extended session (0x10 0x03)
  t=1s    SecurityAccess unlock (0x27)
  t=2s    write calibration DID #1 (0x2E)
  t=3s    write calibration DID #2 (0x2E)
  ...
  t=6s    ← S3 timer would fire here and drop to default!
  ...
  t=30s   calibration routine complete

Without TesterPresent:
  t=6s: ECU drops to default. Next write returns NRC 0x22 (conditionsNotCorrect).
  Entire calibration sequence fails. Tester re-enters extended. Start over.

With TesterPresent every 2 seconds:
  t=2s: TP → S3 timer reset
  t=4s: TP → S3 timer reset
  t=6s: TP → S3 timer reset
  ...session stays alive for the entire 30 seconds
```

### The Correct Pattern: Background Thread

In automated testing, TesterPresent is typically sent from a **background thread** or timer, not interleaved manually:

```python
class SessionKeepAlive(threading.Thread):
    """Background thread that sends TesterPresent every interval_s seconds."""

    def __init__(self, tester_bus, interval_s: float = 2.0):
        super().__init__(daemon=True)
        self._bus      = tester_bus
        self._interval = interval_s
        self._active   = threading.Event()
        self._active.set()

    def pause(self) -> None:
        self._active.clear()   # pause during ECU reset (ECU is offline)

    def resume(self) -> None:
        self._active.set()

    def run(self) -> None:
        while True:
            if self._active.is_set():
                tp = can.Message(
                    arbitration_id=0x7E0,
                    # 0x3E 0x80 = TesterPresent, suppress response (no bus noise)
                    data=bytes([0x02, 0x3E, 0x80, 0x00, 0x00, 0x00, 0x00, 0x00]),
                    is_extended_id=False
                )
                self._bus.send(tp)
            time.sleep(self._interval)
```

> 🌉 **From your world:** This is your **session keepalive timer** — exactly like a WebSocket ping frame, or `SELECT 1` sent periodically to keep a database connection from timing out, or the `PING` in MQTT. You've implemented all of these. TesterPresent is the UDS equivalent.

> ⚠️ **The most common automation bug:** A test script that works perfectly for 3-second tests but silently fails for 10-second calibration sequences because nobody added a TesterPresent thread. The failure mode is always the same: `NRC 0x22 conditionsNotCorrect` on the first operation after the S3 timeout, with nothing obviously wrong in the test logic.

---

## 🧠 Concept: The DTC Lifecycle — From First Fault to Cleared

A **Diagnostic Trouble Code (DTC)** is an error code stored inside an ECU whenever it detects a fault. Think of a DTC as the ECU's error log. Even if the fault disappears later, the ECU may keep the DTC in memory until it is cleared.

Before reading DTC services, you need to understand **how a fault becomes a DTC and how it evolves**.

### The Four-Stage Journey

```
┌──────────────────────────────────────────────────────────────────────┐
│  DTC LIFECYCLE                                                       │
│                                                                      │
│  STAGE 1: FAULT DETECTED (testFailed)                               │
│  A monitor detects that sensor/component is out of range.            │
│  status bit 0 (TF) set. DTC exists but is only "pending."           │
│  No MIL (no warning light). Just internal knowledge.                 │
│                                                                      │
│           ↓  (fault still present at next drive cycle)              │
│                                                                      │
│  STAGE 2: PENDING DTC (pendingDTC)                                  │
│  Fault persisted through a monitoring cycle.                         │
│  status bit 2 (PDTC) set. Still no MIL. "Watching this..."          │
│                                                                      │
│           ↓  (fault confirmed: failed in 2 drive cycles)            │
│                                                                      │
│  STAGE 3: CONFIRMED DTC (confirmedDTC)                              │
│  Fault confirmed across multiple drive cycles (OEM-defined count).  │
│  status bit 3 (CDTC) set. MIL (check engine light) may illuminate.  │
│  DTC is now "stored" — survives power cycles.                        │
│                                                                      │
│           ↓  (fault stops occurring / vehicle repaired)             │
│                                                                      │
│  STAGE 4: PASSIVE / AGED-OUT                                        │
│  TF bit cleared (fault not currently active). CDTC may remain set   │
│  for a defined number of drive cycles then auto-ages out.            │
│  Or: ClearDiagnosticInformation (0x14) clears all status bits.      │
└──────────────────────────────────────────────────────────────────────┘
```

> 🌉 **From your world:** This lifecycle is identical to **bug severity escalation** in your test management tool:
> - Detected = a new test failure logged (bug created)
> - Pending = reproduced on a second run (confirmed reproducible)
> - Confirmed = regression test added and it fails on every run (confirmed defect)
> - Aged out = bug auto-closed after N sprints with no repro (stale/wont-fix)
>
> Same concept: the system waits for persistence before escalating severity. One flaky test failure ≠ a defect.

---

## 🧠 Concept: The DTC Status Byte — Eight Bits, Eight Questions

The DTC status byte is the richest single byte in automotive diagnostics. Each bit answers a specific question about the fault:

```
┌──────────────────────────────────────────────────────────────────────┐
│  DTC STATUS BYTE — BIT-BY-BIT                                       │
│                                                                      │
│  Bit 7 (0x80)  warningIndicatorRequested (WIR)                      │
│  Q: "Is the MIL (Check Engine light) on because of THIS DTC?"       │
│  A: 1 = MIL on, 0 = MIL not triggered by this DTC                  │
│                                                                      │
│  Bit 6 (0x40)  testNotCompletedThisMonitoringCycle (TNCTMC)         │
│  Q: "Has the monitor run this drive cycle?"                          │
│  A: 1 = NOT run yet, 0 = run at least once this cycle               │
│                                                                      │
│  Bit 5 (0x20)  testFailedSinceLastClear (TFSLC)                     │
│  Q: "Has this DTC ever failed since the last ClearDTC?"             │
│  A: 1 = yes (at any point since last clear), 0 = no                 │
│                                                                      │
│  Bit 4 (0x10)  testNotCompletedSinceLastClear (TNCLSC)              │
│  Q: "Has the monitor even run since the last ClearDTC?"             │
│  A: 1 = monitor has NOT run since clear, 0 = ran at least once      │
│                                                                      │
│  Bit 3 (0x08)  confirmedDTC (CDTC)                                  │
│  Q: "Has this fault been confirmed across multiple cycles?"         │
│  A: 1 = confirmed (stored permanently), 0 = not yet confirmed       │
│                                                                      │
│  Bit 2 (0x04)  pendingDTC (PDTC)                                    │
│  Q: "Did this fault occur in the current OR previous drive cycle?"  │
│  A: 1 = yes (recent, possibly transient), 0 = no                   │
│                                                                      │
│  Bit 1 (0x02)  testFailedThisMonitoringCycle (TFTMC)                │
│  Q: "Did this fault fire in the current monitoring cycle?"          │
│  A: 1 = yes (this cycle), 0 = not this cycle                       │
│                                                                      │
│  Bit 0 (0x01)  testFailed (TF)                                      │
│  Q: "Is this fault HAPPENING RIGHT NOW?"                            │
│  A: 1 = currently failing, 0 = not currently active                │
└──────────────────────────────────────────────────────────────────────┘
```

### Reading a Status Byte Like a Detective

```
Example: P0300 status = 0xAF = 10101111

Bit 7 (WIR):    1 → MIL is on  ← customer's check-engine light is illuminated
Bit 6 (TNCTMC): 0 → monitor DID run this cycle
Bit 5 (TFSLC):  1 → failed since last clear  ← this hasn't been fixed
Bit 4 (TNCLSC): 0 → monitor ran since last clear
Bit 3 (CDTC):   1 → confirmed DTC  ← confirmed real fault, not transient
Bit 2 (PDTC):   1 → pending (recent)
Bit 1 (TFTMC):  1 → failed this monitoring cycle
Bit 0 (TF):     1 → currently failing  ← engine is misfiring RIGHT NOW

Verdict: Active confirmed misfire with MIL on. Critical. Fix before clearing.

Example: P0420 status = 0x28 = 00101000

Bit 7 (WIR):    0 → MIL is off (perhaps cleared by a previous clear)
Bit 5 (TFSLC):  1 → was a problem since last clear
Bit 3 (CDTC):   1 → confirmed stored DTC
All others:     0

Verdict: Confirmed historical fault, not currently active. Catalyst may
have degraded but the monitor isn't currently failing. Could be fixed.
```

### Common Status Masks Used in Testing

```
┌──────────┬──────────────────────────────────────────────────────────┐
│  Mask    │  What You're Asking                                      │
├──────────┼──────────────────────────────────────────────────────────┤
│  0xFF    │  Give me ALL DTCs regardless of state                    │
│          │  (full inventory; what does the ECU know about?)        │
├──────────┼──────────────────────────────────────────────────────────┤
│  0x08    │  Confirmed DTCs only                                     │
│          │  (what has been confirmed as a real fault?)              │
├──────────┼──────────────────────────────────────────────────────────┤
│  0x09    │  Confirmed AND currently failing (0x08 | 0x01)          │
│          │  (what is confirmed AND active right now?)               │
├──────────┼──────────────────────────────────────────────────────────┤
│  0x04    │  Pending DTCs                                            │
│          │  (what's being monitored but not yet confirmed?)         │
├──────────┼──────────────────────────────────────────────────────────┤
│  0x80    │  MIL-on DTCs                                             │
│          │  (what's causing the check-engine light?)               │
├──────────┼──────────────────────────────────────────────────────────┤
│  0x01    │  Currently failing DTCs                                  │
│          │  (what is ACTIVELY misfiring/failing RIGHT NOW?)         │
└──────────┴──────────────────────────────────────────────────────────┘
```

---

## 🧠 Concept: Service 0x19 — ReadDTCInformation
**Purpose:** Read stored diagnostic trouble codes and related information.

Service 0x19 is the most **sub-function-rich** service in UDS — it has more than 20 sub-functions. The six you need to know for testing:

```
┌─────────────────────────────────────────────────────────────────────────┐
│  0x19 ReadDTCInformation — KEY SUB-FUNCTIONS                            │
├────────┬─────────────────────────────────────────┬──────────────────────┤
│  Sub   │  Name                                   │  Use                 │
├────────┼─────────────────────────────────────────┼──────────────────────┤
│  0x01  │  reportNumberOfDTCByStatusMask          │  Count matching DTCs │
│        │  Request: [0x19, 0x01, statusMask]      │  (quick check first) │
│        │  Response: [0x59, 0x01, availMask,      │                      │
│        │             formatId, count_H, count_L] │                      │
├────────┼─────────────────────────────────────────┼──────────────────────┤
│  0x02  │  reportDTCByStatusMask                  │  Get matching DTCs   │
│        │  Request: [0x19, 0x02, statusMask]      │  (main diagnostic    │
│        │  Response: [0x59, 0x02, availMask,      │   query)             │
│        │             DTC_H, DTC_L, status, ...]  │                      │
├────────┼─────────────────────────────────────────┼──────────────────────┤
│  0x04  │  reportDTCSnapshotRecordByDTCNumber     │  Freeze-frame data   │
│        │  (freeze frame)                         │  when DTC was set    │
│        │  Request: [0x19, 0x04, DTC_H, DTC_L,   │  (RPM, speed, temp   │
│        │             recordNumber]               │   at moment of fault)│
├────────┼─────────────────────────────────────────┼──────────────────────┤
│  0x06  │  reportDTCExtDataRecordByDTCNumber      │  Trip counter, aging │
│        │  (extended data)                        │  counter, occurrence │
│        │  Request: [0x19, 0x06, DTC_H, DTC_L,   │  count               │
│        │             extDataRecordNumber]        │                      │
├────────┼─────────────────────────────────────────┼──────────────────────┤
│  0x09  │  reportDTCWithPermanentStatus           │  Permanent DTCs that │
│        │  (permanent / MIL-on DTCs)              │  survive clear (OBD  │
│        │                                         │  mandated storage)   │
├────────┼─────────────────────────────────────────┼──────────────────────┤
│  0x0A  │  reportSupportedDTC                     │  All DTCs the ECU    │
│        │  Request: [0x19, 0x0A]                  │  knows about,        │
│        │  Response: same format as 0x02          │  regardless of status│
└────────┴─────────────────────────────────────────┴──────────────────────┘
```

### Response Format Deep-Dive

```
0x19 0x02 (reportDTCByStatusMask) response anatomy:

  [0x59, 0x02, 0xFF, 0x03, 0x00, 0xAF, 0x01, 0x28, 0x28, ...]
   ^^^^  ^^^^  ^^^^  ^^^^^^^^^^^^^^^^^  ^^^^^^^^^^^^^^^^^
   SID+  sub   avail P0300  P0300       P0420  P0420
   0x40        mask  high   status=0xAF high   status=0x28
                     low=00             low=20

   • 0x59 = 0x19 + 0x40 (positive response SID)
   • 0xFF = DTCStatusAvailabilityMask: which status bits this ECU supports
   • Per DTC: 3 bytes [DTC_high, DTC_low, status_byte]
   • Total = 3 + (3 × N) bytes where N = number of matching DTCs
   • Always a multi-frame response for N > 2 (exceeds 7-byte SF limit)
```

### Freeze Frame (Snapshot) Data — The "What Was Happening" Picture

The most valuable information for diagnosing the root cause of a DTC is the **freeze frame** (sub-function 0x04): a snapshot of sensor values captured the instant the DTC was confirmed.

```
Typical freeze frame for P0300 (Random Misfire):
  Engine RPM        : 2500 rpm     ← was the engine under load?
  Engine Load       : 85%          ← yes, heavily loaded
  Coolant Temp      : 92°C         ← engine was warm (not a cold-start misfire)
  Vehicle Speed     : 45 km/h      ← moving (not at idle)
  Short-term Fuel   : +15%         ← running lean (fuel trim clue)

Root cause from freeze frame: high-load lean misfire at 45 km/h.
Not a sensor cold-start issue. Look at fuel system and injectors.
```

> 🌉 **From your world:** Freeze frame data is exactly a **crash dump** or **application log at the moment of failure** — the "what was the state when the exception was thrown?" record that makes the difference between "we know what happened" and "we have no idea." You've been reading stack traces and log timestamps for years. Reading a freeze frame uses the same analytical skills.

---

## 🧠 Concept: Service 0x14 — ClearDiagnosticInformation

**Purpose:** Erase DTCs stored inside the ECU.

### Request Format

```
┌──────────┬──────────────────────────────────────────────────────────┐
│  0x14    │  groupOfDTC_H  groupOfDTC_M  groupOfDTC_L               │
│  SID     │  3 bytes — identifies which group of DTCs to clear       │
└──────────┴──────────────────────────────────────────────────────────┘

Clear ALL DTCs:   [0x14, 0xFF, 0xFF, 0xFF]   ← 0xFFFFFF = all groups
Clear specific:   [0x14, 0x00, 0x03, 0x00]   ← clear DTC 0x0300 only
```

### Positive Response Format

```
┌──────────┐
│  0x54    │   ← just one byte: 0x14 + 0x40
└──────────┘

Note: NO echo of the DTC group. No sub-function. Just 0x54.
This is the shortest possible positive response in UDS.
```

### Session and Condition Requirements

| Session | Result |
|---|---|
| Default (0x01) | OEM-defined — often allowed (OBD tools can clear in default) |
| Extended (0x03) | ✅ Always allowed |
| Programming (0x02) | ✅ Allowed |

**In our simulation:** extended session required. This is one common OEM interpretation.

### The Three Things That Can Go Wrong

```
1. Wrong session          → NRC 0x22 (conditionsNotCorrect)
2. Wrong payload length   → NRC 0x13 (incorrectMessageLength)
                           (must be exactly SID + 3 bytes = 4 bytes total)
3. DTC group not supported → NRC 0x31 (requestOutOfRange)
   (e.g., trying to clear a specific DTC code that doesn't exist in the ECU)
```

### ⚠️ Critical Tester Knowledge: Clear Then Verify

Clearing DTCs **never guarantees the DTC is gone**. If the fault is still active, the DTC will be re-set on the next monitoring cycle:

```
WRONG test:
  Step 1: Clear DTCs → 0x54  ✅
  Step 2: PASS  ← wrong! the fault may come back immediately

CORRECT test:
  Step 1: Verify DTC is present before clear
  Step 2: Clear DTCs → 0x54
  Step 3: Read DTCs immediately → 0 DTCs (or only TFSLC still set)
  Step 4: Wait one monitoring cycle
  Step 5: Read DTCs again → 0 DTCs confirms fault is truly fixed
                          → DTC reappears confirms fault is still present

The two-phase read (immediately after clear, then after monitoring) is
the only reliable confirmation that a fault is fixed vs temporarily cleared.
```

> 🌉 **From your world:** You've been doing this in web testing forever: `DELETE /resource` → `GET /resource` → assert 404. But you also verify that a reload or restart doesn't bring the resource back (persistence check). Same pattern: clear + immediate verify + post-cycle verify.

---

## 🧠 Concept: CommunicationControl (0x28) — Muting the Bus

A service you'll encounter but may not need to implement yourself:

```
Purpose: Disable normal CAN communication (periodic signals) during
         calibration or flash to reduce bus load and avoid interference.

Request: [0x28, controlType, communicationType]
  controlType:
    0x00 = enableRxAndTx          (re-enable everything)
    0x01 = enableRxAndDisableTx   (ECU listens but stops transmitting)
    0x02 = disableRxAndEnableTx   (ECU transmits but stops receiving)
    0x03 = disableRxAndTx         (ECU goes quiet in both directions)
  communicationType:
    0x01 = normalCommunicationMessages (regular app traffic)
    0x02 = nmCommunicationMessages (network management)
    0x03 = both

Positive Response: [0x68, controlType]

Why it matters for testing:
  During flash: Mute all non-diagnostic ECU transmissions (0x28 0x03 0x01)
                to reduce bus load. Re-enable after flash (0x28 0x00 0x01).
  During calibration: Stop periodic sensor broadcasts to avoid interfering
                with the DBC-level data you're monitoring.
  Test: Verify ECU stops transmitting after 0x28 0x03.
        Verify ECU resumes transmitting after 0x28 0x00.
        Verify session drop (S3 timeout) re-enables communication automatically.
```

> ⚠️ **Tester trap:** If you forget to call `0x28 0x00` (re-enable) after a test, the ECU stays silent and the *next* test team thinks the ECU is broken. Always re-enable as part of test teardown, even on failure (use a finally block).

---

## 🧠 Concept: InputOutputControl (0x2F) — Forcing ECU Outputs

Another service for advanced HIL and bench testing:

```
Purpose: Force an ECU output (actuator, PWM, relay) to a specific value
         for testing, bypassing the normal control logic.

Request: [0x2F, dataIdentifier_H, dataIdentifier_L, controlOptionByte, controlEnableDataRecord]
  controlOptionByte:
    0x00 = returnControlToECU    (give control back to the ECU)
    0x01 = resetToDefault        (reset output to default value)
    0x02 = freezeCurrentState    (hold current value)
    0x03 = shortTermAdjustment   (set to the value in controlEnableDataRecord)

Classic HIL test use case:
  Force fuel injector 3 on: 0x2F [injector_3_DID] 0x03 [ON value]
  Verify current draw on injector 3 increases
  Release: 0x2F [injector_3_DID] 0x00
  
Test: Verify ECU returns control automatically when session drops to default.
      Forcing an output and dropping session should not leave the actuator
      stuck in the forced state indefinitely (safety requirement).
```

> **Safety note:** InputOutputControl is the most dangerous UDS service from a vehicle-safety perspective. Forcing a throttle to "open" or a brake actuator on/off can cause harm. In HIL environments, these tests are always done on a bench with no mechanical connections. Document the safety preconditions in your test plan.

---

## 🧩 The Big Picture: Complete UDS Service Map

After Day 15, you have the full UDS landscape. Here's where every service you've studied fits:

```
┌──────────────────────────────────────────────────────────────────────────┐
│  COMPLETE UDS SERVICE MAP — TEST ENGINEER EDITION                       │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │  SESSION & TRANSPORT LAYER                                        │   │
│  │  0x10 DiagnosticSessionControl ✅ Day 12                          │   │
│  │  0x11 ECUReset               ✅ Day 12                            │   │
│  │  0x3E TesterPresent          ✅ Day 15  ← keepalive               │   │
│  │  0x28 CommunicationControl   📖 Day 15 concept                   │   │
│  ├──────────────────────────────────────────────────────────────────┤   │
│  │  DATA ACCESS                                                      │   │
│  │  0x22 ReadDataByIdentifier   ✅ Day 13                            │   │
│  │  0x2E WriteDataByIdentifier  ✅ Day 13                            │   │
│  │  0x2F InputOutputControl     📖 Day 15 concept                   │   │
│  │  0x23 ReadMemoryByAddress    (advanced, similar to 0x22)          │   │
│  ├──────────────────────────────────────────────────────────────────┤   │
│  │  SECURITY & AUTHENTICATION                                        │   │
│  │  0x27 SecurityAccess         ✅ Day 14                            │   │
│  │  0x29 Authentication         (ISO 14229-2, PKI-based, future)    │   │
│  ├──────────────────────────────────────────────────────────────────┤   │
│  │  FAULT CODE MANAGEMENT                                            │   │
│  │  0x14 ClearDiagnosticInfo    ✅ Day 15                            │   │
│  │  0x19 ReadDTCInformation     ✅ Day 15                            │   │
│  ├──────────────────────────────────────────────────────────────────┤   │
│  │  ROUTINE / ACTION CONTROL                                         │   │
│  │  0x31 RoutineControl         ✅ Day 14                            │   │
│  ├──────────────────────────────────────────────────────────────────┤   │
│  │  FIRMWARE UPDATE (FLASH)     ← Day 16 option                     │   │
│  │  0x34 RequestDownload                                             │   │
│  │  0x36 TransferData                                                │   │
│  │  0x37 RequestTransferExit                                         │   │
│  │  0x38 RequestFileTransfer                                         │   │
│  └──────────────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 🌍 Where It's Used in the Real World

| Context | TesterPresent (0x3E) | ReadDTC (0x19) | ClearDTC (0x14) |
|---|---|---|---|
| **Workshop scan** | Used by scan tool to stay in extended session while mechanic reads fault codes | Primary service — reads all P/B/C/U codes | After repair, mechanic clears codes and verifies they don't return |
| **EOL production** | Keeps session alive during 60-second EOL test sequence | Reads DTC after power-on functional test — must be zero | Clears any manufacturing DTCs before shipping |
| **OBD compliance** | OBD standard requires 0x3E support | OBD Mode 3 (read DTCs) maps to 0x19 sub-function | OBD Mode 4 (clear codes) maps to 0x14 |
| **HIL regression** | Background keepalive thread in every long-running test | Pre-test assertion: DTC count=0; post-fault injection: DTC count>0 | Teardown fixture: clear all DTCs after each test |
| **Field debugging** | N/A | Read DTCs remotely via telematics to diagnose in-field failures | Fleet management: remote clear after OTA fix to verify resolved |

---

## 🔬 How a Tester Thinks About It

```
┌────────────────────────────────────────────────────────────────────┐
│  TESTER'S CHECKLIST — 0x3E, 0x19, 0x14                            │
├────────────────────────────────────────────────────────────────────┤
│  TESTER PRESENT (0x3E)                                             │
│  ✓ Response [0x7E, 0x00] to sub=0x00?                             │
│  ✓ NO response to sub=0x80 (suppress bit)?                        │
│  ✓ Unknown sub-function returns NRC 0x12?                         │
│  ✓ Allowed in default session (not just extended)?                │
│  ✓ Does it actually reset the S3 timer? (test with near-S3 wait)  │
│  ✓ Background TP thread in all long test sequences?               │
│                                                                    │
│  READ DTC (0x19)                                                   │
│  ✓ Pre-test: assert DTC count = 0 (clean ECU)?                    │
│  ✓ mask=0xFF returns expected DTC count?                          │
│  ✓ mask=0x08 (confirmed) filters correctly?                       │
│  ✓ mask=0x01 (active) filters correctly?                          │
│  ✓ mask=0x80 (MIL) filters correctly?                             │
│  ✓ Status byte values match spec for each DTC?                    │
│  ✓ reportSupportedDTC (0x0A) returns complete DTC catalogue?      │
│  ✓ Unknown sub-function returns NRC 0x12?                         │
│  ✓ Freeze frame available for confirmed DTCs?                     │
│  ✓ Extended data (trip counter, occurrence count) accessible?     │
│                                                                    │
│  CLEAR DTC (0x14)                                                  │
│  ✓ In default session: NRC 0x22 (if OEM requires extended)?      │
│  ✓ In extended session: positive 0x54?                            │
│  ✓ Read-back immediately after clear: count = 0?                  │
│  ✓ After one monitoring cycle: still = 0 (fault not reproduced)?  │
│  ✓ Wrong payload length: NRC 0x13?                                │
│  ✓ Unknown DTC group: NRC 0x31?                                   │
│  ✓ Teardown: always clear DTCs as test cleanup (finally block)?   │
└────────────────────────────────────────────────────────────────────┘
```

### The DTC Testing Pattern in Automated Tests

```python
# Recommended DTC test fixture pattern

def setup():
    """Run before each test case."""
    switch_to_extended_session()
    clear_all_dtcs()
    assert dtc_count(mask=0xFF) == 0, "Pre-condition: ECU must start clean"

def teardown():
    """Always run, even on failure."""
    try:
        switch_to_extended_session()
        clear_all_dtcs()     # leave ECU clean for next test
        re_enable_comms()    # in case 0x28 was used
    except Exception:
        pass   # best-effort cleanup

def test_p0300_misfire_detection():
    """Inject a misfire condition, verify DTC is set."""
    inject_misfire_fault(cylinder=3)         # via hardware or HiL signal
    wait_for_monitor_cycle(seconds=2)
    dtcs = read_dtcs(mask=DTC_PDTC)          # pending first
    assert 0x0300 in [d.code for d in dtcs], "P0300 should be pending"
    wait_for_confirm_cycle(seconds=5)
    dtcs = read_dtcs(mask=DTC_CDTC)          # now confirmed
    assert 0x0300 in [d.code for d in dtcs], "P0300 should be confirmed"
    assert dtcs[0].status & DTC_WIR,         "MIL should be on"
```

---

## 🛠️ Hands-On Exercise: DTC & TesterPresent Simulator

### What You'll Build

```
Day-15_UDS_NRC_DTC_TesterPresent/
├── uds_dtc_tester_present.py   ← full simulation + 20 test cases
└── Day15_UDS_NRC_DTC_TesterPresent.md
```

**ECU's DTC store (4 representative fault codes):**

```
┌────────┬────────────────────────────────┬─────────┬──────────────────────────┐
│  Code  │  Name                          │ Status  │  Meaning                 │
├────────┼────────────────────────────────┼─────────┼──────────────────────────┤
│ 0x0300 │ P0300 Random Misfire           │  0xAF   │ Active, confirmed, MIL on│
│ 0x0128 │ P0128 Thermostat Stuck Open    │  0x24   │ Pending, not confirmed   │
│ 0x0420 │ P0420 Catalyst Efficiency Low  │  0x28   │ Confirmed, stored, passive│
│ 0xC100 │ U0100 Lost Comm with ECM       │  0xAF   │ Active, confirmed, MIL on│
└────────┴────────────────────────────────┴─────────┴──────────────────────────┘

Status byte 0xAF = WIR | TFSLC | CDTC | PDTC | TFTMC | TF
Status byte 0x24 = TFSLC | PDTC
Status byte 0x28 = TFSLC | CDTC
```

**Services implemented:**
- `0x10` DiagnosticSessionControl
- `0x3E` TesterPresent (with suppress bit)
- `0x19` ReadDTCInformation (sub-functions: 0x01, 0x02, 0x0A)
- `0x14` ClearDiagnosticInformation

---

### Expected Output

```
🩺🔔  🩺🔔  🩺🔔  🩺🔔  🩺🔔  🩺🔔  🩺🔔  🩺🔔  🩺🔔  🩺🔔
  Day 15 — UDS NRC Reference + TesterPresent (0x3E) +
           ReadDTCInformation (0x19) + ClearDTCs (0x14)
🩺🔔  🩺🔔  🩺🔔  🩺🔔  🩺🔔  🩺🔔  🩺🔔  🩺🔔  🩺🔔  🩺🔔

────────────────────────────────────────────────────────────────
  GROUP 1: TesterPresent (0x3E)
────────────────────────────────────────────────────────────────
  ✅ PASS  TC01 TesterPresent extended session  [SID=0x7E]
  ✅ PASS  TC01 sub-function echoed as 0x00  [✓]
  ✅ PASS  TC02 TesterPresent in default session (allowed everywhere)  [SID=0x7E]
  ✅ PASS  TC03 TesterPresent suppress → no response (correct)  [✓]
  ✅ PASS  TC04 TesterPresent unknown sub → NRC 0x12  [NRC=0x12]

────────────────────────────────────────────────────────────────
  GROUP 2: ReadDTCInformation (0x19) — Count by Mask
────────────────────────────────────────────────────────────────
  ✅ PASS  TC05 0x19 0x01 mask=0xFF  [SID=0x59]
  ✅ PASS  TC05 DTC count = 4  [✓]
  ✅ PASS  TC06 0x19 0x01 mask=0x08 (confirmed)  [SID=0x59]
  ✅ PASS  TC06 Confirmed DTC count = 3  [✓]
  ✅ PASS  TC07 0x19 0x01 mask=0x01 (active)  [SID=0x59]
  ✅ PASS  TC07 Active DTC count = 2 (P0300, U0100)  [✓]

────────────────────────────────────────────────────────────────
  GROUP 3: ReadDTCInformation (0x19) — DTCs by Mask
────────────────────────────────────────────────────────────────
  ✅ PASS  TC08 0x19 0x02 mask=0xFF  [SID=0x59]
  ✅ PASS  TC08 All 4 DTCs returned  [✓]
  ✅ PASS  TC08 P0300 status byte correct  [0xAF ✓]
  ✅ PASS  TC09 0x19 0x02 mask=0x01 (active)  [SID=0x59]
  ✅ PASS  TC09 Active DTCs = {P0300, U0100}  [✓]
  ✅ PASS  TC10 0x19 0x02 mask=0x80 (MIL on)  [SID=0x59]
  ✅ PASS  TC10 MIL-on DTCs = {P0300, U0100}  [✓]
  ✅ PASS  TC11 0x19 0x02 mask=0x04 (pending)  [SID=0x59]
  ✅ PASS  TC11 Pending DTC count = 3 (P0300, P0128, U0100)  [✓]
  ✅ PASS  TC12 0x19 0x0A reportSupportedDTC  [SID=0x59]
  ✅ PASS  TC12 Supported DTC count = 4  [✓]
  ✅ PASS  TC13 0x19 unknown sub → NRC 0x12  [NRC=0x12]

────────────────────────────────────────────────────────────────
  GROUP 4: ClearDiagnosticInformation (0x14)
────────────────────────────────────────────────────────────────
  ✅ PASS  TC14 Clear DTCs in default → NRC 0x22  [NRC=0x22]
  ✅ PASS  TC15 Clear all DTCs → 0x54  [✓]
  ✅ PASS  TC16 Read DTC count after clear  [SID=0x59]
  ✅ PASS  TC16 DTC count = 0 after clear  [✓]
  ✅ PASS  TC17 ClearDTC wrong length → NRC 0x13  [NRC=0x13]

────────────────────────────────────────────────────────────────
  GROUP 5: Comprehensive NRC Validation
────────────────────────────────────────────────────────────────
  ✅ PASS  TC18 0x19 missing sub-function → NRC 0x13  [NRC=0x13]
  ✅ PASS  TC19 Unknown SID → NRC 0x11  [NRC=0x11 ✓]
  ✅ PASS  TC20 NRC format: [0x7F, SID_echoed, NRC] ✓  [[0x7F, 0x19, 0x12]]

================================================================
  TEST SUMMARY: 26/26 passed, 0 failed
================================================================
```

### Run It

```bash
cd "Day-15_UDS_NRC_DTC_TesterPresent"
pip install python-can
python uds_dtc_tester_present.py
```

---

## 🔥 Challenge

### Challenge 1 — 🔔 Background TesterPresent Thread

Implement a keepalive thread and prove it prevents S3 expiry:

```python
class SessionKeepAlive(threading.Thread):
    def __init__(self, bus, tx_id: int, interval_s: float = 2.0):
        super().__init__(daemon=True)
        self._bus      = bus
        self._tx_id    = tx_id
        self._interval = interval_s
        self._active   = threading.Event()
        self._active.set()
        self._stop     = threading.Event()

    def pause(self):  self._active.clear()
    def resume(self): self._active.set()
    def stop(self):   self._stop.set()

    def run(self):
        while not self._stop.is_set():
            if self._active.is_set():
                # TODO: send TesterPresent with suppress bit (0x3E 0x80)
                pass
            time.sleep(self._interval)

def tc_s3_keepalive_test(tester, ecu):
    """
    1. Enter extended session
    2. Start keepalive thread (interval=2s, S3=5s)
    3. Wait 6 seconds (would expire without TP)
    4. Attempt action that requires extended session
    5. Assert success (TP kept session alive)
    6. Stop keepalive, wait S3+1s
    7. Same action → NRC 0x22 (session expired)
    """
```

### Challenge 2 — 📊 DTC Lifecycle Simulation

Add a `drive_cycle()` method to the ECU that transitions DTC status:

```python
def drive_cycle(self, ecu: SimulatedECU) -> None:
    """
    Simulate one drive cycle:
    - P0128 (pending only, 0x24): promote to confirmed (add CDTC bit)
    - All TNCTMC bits (0x40): clear them (monitors ran this cycle)
    Then verify the new status bytes via 0x19 0x02 0xFF.
    """
```

### Challenge 3 — 🧹 Selective Clear

Test clearing a single DTC by code (not 0xFFFFFF):

```python
def tc_selective_clear():
    """
    1. Verify 4 DTCs present
    2. Clear only P0128 (0x14 0x00 0x01 0x28)
    3. Verify P0128 status = 0x00
    4. Verify P0300, P0420, U0100 still present with original status
    """
```

### Challenge 4 — 📋 NRC Coverage Report

Write a `NRCCoverageReporter` that tracks which NRCs were tested and which are still missing:

```python
NRC_REGISTRY = {
    0x11: "serviceNotSupported",
    0x12: "subFunctionNotSupported",
    0x13: "incorrectMessageLength",
    0x22: "conditionsNotCorrect",
    0x24: "requestSequenceError",
    0x31: "requestOutOfRange",
    # ... add all others
}

class NRCCoverageReporter:
    def __init__(self):
        self._tested = set()

    def record(self, nrc: int):
        self._tested.add(nrc)

    def report(self):
        covered   = {n: NRC_REGISTRY[n] for n in self._tested if n in NRC_REGISTRY}
        uncovered = {n: v for n, v in NRC_REGISTRY.items() if n not in self._tested}
        pct = 100 * len(covered) / len(NRC_REGISTRY)
        print(f"NRC Coverage: {pct:.0f}% ({len(covered)}/{len(NRC_REGISTRY)})")
        # ... print covered + uncovered
```

---

## ❓ Quiz + Answers

**Q1.** A test script enters extended session, performs a 10-second calibration write sequence, and the 8th write returns NRC 0x22. The writes are structurally identical. What happened, and what's the fix?

<details>
<summary>Answer</summary>

The **S3 session-inactivity timer expired**. After 5 seconds (typical S3) with no UDS activity from the tester, the ECU silently dropped back to defaultSession. The 8th write arrived in the wrong session → NRC 0x22 conditionsNotCorrect.

**Fix:** Add a `TesterPresent` keepalive (sub=0x80 to avoid response noise) sent every 2 seconds in a background thread. Alternatively, ensure the write sequence keeps its round trips under the S3 threshold — but the background TP thread is the robust production solution.

</details>

---

**Q2.** You read DTC P0420 with status byte `0x28`. The customer says the Check Engine light is off. Is this consistent with the status byte, and would you recommend clearing the DTC?

<details>
<summary>Answer</summary>

`0x28 = 00101000`:
- Bit 7 (WIR) = 0 → MIL is **not** requested by this DTC ✓ (consistent with light off)
- Bit 5 (TFSLC) = 1 → failed since last clear (was a real fault at some point)
- Bit 3 (CDTC) = 1 → confirmed DTC (was a genuine confirmed fault)
- All other bits = 0 → not currently failing, monitor ran this cycle

This is a **stored but passive fault** — confirmed historical fault, not currently active. The catalyst was below efficiency at some point but is not currently failing the monitor.

**Recommendation:** Do NOT clear yet. Run the OBD monitor drive cycle to completion, then read again. If CDTC clears (ages out) and TFSLC resets after a successful cycle, the catalyst may have self-recovered. If CDTC reappears, the catalyst needs replacement. Clearing before the drive cycle destroys the evidence and forces the customer to return.

</details>

---

**Q3.** What is the difference between `0x19 0x01 mask=0xFF` and `0x19 0x0A`?

<details>
<summary>Answer</summary>

**`0x19 0x01 mask=0xFF`** — `reportNumberOfDTCByStatusMask` with mask 0xFF:
- Returns **count only** (a number), not the DTC codes themselves
- Returns DTCs whose status byte ANDed with 0xFF is non-zero
- Since 0xFF AND anything non-zero = non-zero, this counts all DTCs with any non-zero status

**`0x19 0x0A`** — `reportSupportedDTC`:
- Returns the **full list** of all DTCs the ECU knows about, with their current status bytes
- Includes DTCs with status=0x00 (cleared, with no bits set) if the ECU still "knows" them
- The ECU's DTC catalogue — independent of current status

In practice for a freshly cleared ECU, `0x19 0x0A` may return more DTCs than `0x19 0x02 0xFF` because it includes DTCs with zero status (the ECU knows they exist but no faults have ever been detected for them). `0x01 0xFF` would count zero for those.

</details>

---

**Q4.** A test sends `[0x14, 0xFF, 0xFF, 0xFF]` and gets `[0x7F, 0x14, 0x13]`. What does this mean, and how do you fix the request?

<details>
<summary>Answer</summary>

NRC 0x13 = **incorrectMessageLengthOrInvalidFormat**. The ECU is saying the request has the wrong number of bytes.

Looking at `0x14` format: `[SID, groupOfDTC_H, groupOfDTC_M, groupOfDTC_L]` = **4 bytes total** (including SID).

The request `[0x14, 0xFF, 0xFF, 0xFF]` is 4 bytes in the UDS payload — which is correct. But in an ISO-TP single frame, this needs to be sent as:
`[0x04, 0x14, 0xFF, 0xFF, 0xFF, 0x00, 0x00, 0x00]`
where `0x04` is the PCI byte indicating 4 UDS bytes follow.

If the test is sending fewer bytes (e.g., only `[0x14, 0xFF, 0xFF]` = 3 bytes), fix by adding the missing third group byte. `0x14` requires **exactly 3 bytes after the SID** (no more, no less).

</details>

---

**Q5.** An ECU returns `0x7F 0x3E 0x12` when the tester sends `[0x3E, 0x01]`. What went wrong, and what are the only valid sub-function values for TesterPresent?

<details>
<summary>Answer</summary>

NRC 0x12 = **subFunctionNotSupported**. Sub-function `0x01` is not valid for TesterPresent.

The only valid sub-function for TesterPresent is **`0x00` (zeroSubFunction)**:
- `0x00` = process normally and send positive response `[0x7E, 0x00]`
- `0x80` = process normally but **suppress** the positive response (no response sent)

The `0x80` variant is just `0x00` with the suppressPosRspMsgIndicationBit (bit 7) set. So the only actual sub-function value is `0x00` — bit 7 is the modifier.

Any other value (0x01, 0x02, 0x03...) → NRC 0x12 subFunctionNotSupported.

</details>

---

## 📌 Key Takeaways

```
┌──────────────────────────────────────────────────────────────────┐
│  DAY 15 KEY TAKEAWAYS                                            │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  1. Read the NRC before debugging anything else.                │
│     NRC 0x22 = wrong state. 0x24 = wrong order. 0x31 = wrong    │
│     range/DID. 0x33 = no security. 0x13 = wrong byte count.     │
│     0x78 = wait, NOT an error. NRC = exact diagnosis code.       │
│                                                                  │
│  2. suppressPosRspMsgIndicationBit (bit 7 of sub-function)       │
│     applies to ALL services. When set: ECU processes silently,   │
│     no positive response. NRCs are still sent on errors.         │
│                                                                  │
│  3. TesterPresent (0x3E) sub=0x80 is the production keepalive   │
│     pattern. Run in a background thread, every 2 seconds.        │
│     Without it, any sequence > S3 seconds silently fails.        │
│                                                                  │
│  4. The DTC status byte is 8 individual questions about a        │
│     fault. TF=active now. CDTC=confirmed. WIR=MIL on.           │
│     Status mask lets you filter by exactly which question.       │
│                                                                  │
│  5. 0x19 0x01 (count) before 0x19 0x02 (list) — always get     │
│     the count first. If count=0, skip the expensive list query.  │
│                                                                  │
│  6. Clear DTCs (0x14) positive response = just [0x54].          │
│     Always read-back verify: count immediately after clear,      │
│     then again after one monitoring cycle.                       │
│                                                                  │
│  7. CommunicationControl (0x28) mutes ECU transmissions.         │
│     Always re-enable in teardown even if the test fails.         │
│                                                                  │
│  8. After Day 15, you know every UDS service a test engineer     │
│     touches in daily work. What remains is the flash sequence    │
│     (0x34/0x36/0x37) for firmware update testing.               │
└──────────────────────────────────────────────────────────────────┘
```

---

## ⏭️ Run code 
```
cd "Day-15_UDS_NRC_DTC_TesterPresent"
pip install python-can
python uds_dtc_tester_present.py
```
