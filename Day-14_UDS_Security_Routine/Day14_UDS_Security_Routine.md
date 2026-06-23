# 🔐🔑 Day 14: UDS Services — SecurityAccess (0x27) & RoutineControl (0x31)

> **Course:** Embedded & IoT Testing Masterclass
> **Instructor:** Professor Embed
> **Prerequisites:** Day 1–13 (CAN fundamentals + python-can + UDS Sessions, Reset, ReadData, WriteData)

---

## 📚 Table of Contents

1. [Recap: Where We Left Off](#recap-where-we-left-off)
2. [Concept: What Is SecurityAccess (0x27)?](#concept-securityaccess)
3. [Concept: The Seed/Key Challenge-Response Mechanism](#concept-seed-key)
4. [Concept: Security Levels — Not All Locks Are Equal](#concept-security-levels)
5. [Concept: Brute-Force Protection — NRCs 0x35, 0x36, 0x37](#concept-brute-force)
6. [Concept: Key Algorithm Design — Why You Never Hardcode Keys](#concept-key-algo)
7. [Concept: What Is RoutineControl (0x31)?](#concept-routinecontrol)
8. [Concept: The Three Sub-Functions — Start / Stop / Results](#concept-subfunctions)
9. [Concept: Routine Identifiers (RIDs)](#concept-rids)
10. [Concept: NRCs Specific to 0x27 and 0x31](#concept-nrcs)
11. [The Big Picture: Security + Routines in the UDS Stack](#the-big-picture)
12. [Where It's Used in the Real World](#where-its-used)
13. [How a Tester Thinks About It](#how-a-tester-thinks)
14. [Hands-On Exercise: Security & Routine Simulator](#hands-on-exercise)
15. [Quiz + Answers](#quiz--answers)
16. [Key Takeaways](#key-takeaways)

---

## 🔁 Recap: Where We Left Off

Day 13 gave you the ability to **read and write named data** from inside an ECU:

- `0x22 ReadDataByIdentifier` = GET a DID value
- `0x2E WriteDataByIdentifier` = PUT a new DID value
- Write protection has four layers: session, security, writability, data validity
- Security layer rejection = **NRC 0x33 securityAccessDenied**

And there it was — the NRC we mentioned but didn't fully explain. TC11 in Day 13 showed you that writing `MaxRPMLimitCalibration` (DID 0x3001) returns NRC 0x33 unless a "security unlock" has been performed. We shortcut it with an `ecu.unlock_security()` cheat call.

Today we close that loop. **SecurityAccess (0x27)** is *how you earn that unlock*. And once you're unlocked, **RoutineControl (0x31)** is *how you trigger the ECU to do things* — self-tests, calibration sequences, drive-cycle completion checks.

> *"Yesterday you had the key to the filing cabinet. Today you learn how to get the key to the vault — and how to push the button that runs the self-diagnostic."*

---

## 🧠 Concept: What Is SecurityAccess (0x27)?

### The Night-Club Bouncer Analogy 🔒

Imagine a night club with two doors:
- **Door 1 (Extended Session):** You show your ID to get in. The bouncer checks your face against a list. If you're on it, you're in.
- **Door 2 (Security Access):** Inside, there's a VIP vault. The vault has no keyhole — instead, a screen shows you a random number. Your job is to compute the correct "response" to that number using a secret algorithm that only authorised people know. Get it right, the vault opens. Get it wrong three times, the vault locks for 5 minutes.

**UDS SecurityAccess is Door 2.** It's a cryptographic challenge-response protocol built into the diagnostic layer. Even if an attacker has a CAN interface and knows all the UDS service codes, they cannot write calibration data or flash firmware without knowing the key derivation algorithm.

> 🌉 **From your world:** SecurityAccess is **TOTP (Time-based One-Time Password)** or a **challenge-response HMAC**. The server (ECU) sends a random challenge (the seed). The client (tester) runs the secret algorithm on the challenge and sends the response (the key). The server verifies it. This is exactly how `ssh -o ChallengeResponseAuthentication` works, or FIDO2 hardware keys. Same concept — different transport.

---

## 🧠 Concept: The Seed/Key Challenge-Response Mechanism

### The Four-Step Dance

```
┌──────────────────────────────────────────────────────────────────────┐
│  SECURITYACCESS (0x27) — CHALLENGE-RESPONSE FLOW                    │
│                                                                      │
│  Tester                             ECU                              │
│    │                                  │                              │
│    │  [0x27, 0x01]                    │  ← requestSeed (sub=0x01)   │
│    │  "Give me a challenge"           │                              │
│    │─────────────────────────────────▶│                              │
│    │                                  │                              │
│    │                                  │  ECU generates random seed   │
│    │                                  │  Stores seed internally      │
│    │                                  │                              │
│    │  [0x67, 0x01, S0, S1, S2, S3]   │  ← positive (sub=0x01)     │
│    │  "Here is your challenge"        │  0x67 = 0x27 + 0x40         │
│    │◀─────────────────────────────────│                              │
│    │                                  │                              │
│    │  KEY = DERIVE(S0..S3)           │  ← tester computes key       │
│    │  (algorithm known to both sides) │                              │
│    │                                  │                              │
│    │  [0x27, 0x02, K0, K1, K2, K3]  │  ← sendKey (sub=0x02)       │
│    │  "Here is my response"          │                              │
│    │─────────────────────────────────▶│                              │
│    │                                  │                              │
│    │                                  │  ECU computes expected key   │
│    │                                  │  Compares with received key  │
│    │                                  │                              │
│    │  [0x67, 0x02]                   │  ← positive (sub=0x02)      │
│    │  "Access granted — unlocked"    │  No extra data in response   │
│    │◀─────────────────────────────────│                              │
│    │                                  │                              │
│    │  (Tester is now unlocked.        │                              │
│    │   Can write protected DIDs,      │                              │
│    │   start security-gated routines) │                              │
└──────────────────────────────────────────────────────────────────────┘
```

### Sub-Function Numbering Convention

ISO 14229 uses a consistent pattern for SecurityAccess sub-functions:

```
┌──────────────────────────────────────────────────────────────┐
│  Odd sub-function  = requestSeed  for security level N/2     │
│  Even sub-function = sendKey      for security level N/2     │
│                                                              │
│  0x01 = requestSeed, level 1  │  0x02 = sendKey, level 1    │
│  0x03 = requestSeed, level 2  │  0x04 = sendKey, level 2    │
│  0x05 = requestSeed, level 3  │  0x06 = sendKey, level 3    │
│  ...                          │  ...                        │
└──────────────────────────────────────────────────────────────┘
```

> **Pattern:** Request seed with sub N (odd), respond with sub N+1 (even). If the tester sends key sub N+1 without first requesting seed sub N, the ECU returns **NRC 0x24 requestSequenceError**. This is the UDS equivalent of submitting an answer to a question you were never asked.

### The "Already Unlocked" Signal

When the ECU is already unlocked and the tester requests a seed again, the ECU returns a seed of all zeros: `[0x67, 0x01, 0x00, 0x00, 0x00, 0x00]`. This signals: *"You don't need to go through the key exchange — you're already in."* The tester should detect this and skip the sendKey step.

---

## 🧠 Concept: Security Levels — Not All Locks Are Equal

A single ECU can have multiple security levels, each protecting different capabilities:

```
┌──────────────────────────────────────────────────────────────────────┐
│  SECURITY LEVELS — TYPICAL ASSIGNMENT                                │
├──────────────────────────────────────────────────────────────────────┤
│  Level 1  (0x01/0x02)  — Calibration & Configuration               │
│  Protects: Write calibration DIDs (offsets, thresholds, PID gains)  │
│  Required for: 0x2E writes to manufacturer-specific DIDs            │
│  Session: extendedDiagnosticSession                                  │
├──────────────────────────────────────────────────────────────────────┤
│  Level 3  (0x03/0x04)  — Firmware & Identity Programming            │
│  Protects: Firmware flash, VIN write, ECU serialisation             │
│  Required for: 0x34/0x36/0x37 flash sequence                        │
│  Session: programmingSession                                         │
├──────────────────────────────────────────────────────────────────────┤
│  Level 5  (0x05/0x06)  — Development / Engineering                  │
│  Protects: Memory read/write, debug access, test modes              │
│  Not available in production ECUs (disabled at build time)          │
└──────────────────────────────────────────────────────────────────────┘

In today's simulation we implement Level 1 only.
Level 3 (for flashing) is Day 15's topic.
```

> **Key tester insight:** The level required depends on the service or DID, not your own choice. The ECU's diagnostic spec defines which level protects which resource. Your test plan must use the right level for each operation.

### Security State Lifecycle

```
┌──────────────────────────────────────────────────────────────────┐
│  SECURITY ACCESS STATE MACHINE                                   │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │               LOCKED  (default after power-on)           │   │
│  │                                                          │   │
│  │  Transitions to UNLOCKED:                                │   │
│  │  1. Request seed (odd sub-function)                      │   │
│  │  2. Compute correct key                                  │   │
│  │  3. Send key (even sub-function) → ECU verifies → OK     │   │
│  └─────────────────┬────────────────────────────────────────┘   │
│                    │                                             │
│                    ▼ (correct key received)                      │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                      UNLOCKED                            │   │
│  │                                                          │   │
│  │  Returns to LOCKED when:                                 │   │
│  │  • Session drops to defaultSession (S3 timeout or 0x10)  │   │
│  │  • ECU reset (0x11)                                      │   │
│  │  • Power cycle                                           │   │
│  │                                                          │   │
│  │  Does NOT lock on:                                       │   │
│  │  • Staying in extended session (it stays unlocked)       │   │
│  │  • Session switch extended ↔ programming (OEM-specific)  │   │
│  └──────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
```

---

## 🧠 Concept: Brute-Force Protection — NRCs 0x35, 0x36, 0x37

The three NRCs that every SecurityAccess test must exercise:

```
┌─────────┬──────────────────────────────────────────────────────────────┐
│  NRC    │  Meaning & When It Fires                                     │
├─────────┼──────────────────────────────────────────────────────────────┤
│  0x35   │  invalidKey                                                  │
│         │  Wrong key sent. ECU increments failed-attempt counter.      │
│         │  If counter < MAX_ATTEMPTS, you can try again (new seed).    │
│         │  Analogy: wrong password — "try again, N attempts left"      │
├─────────┼──────────────────────────────────────────────────────────────┤
│  0x36   │  exceededNumberOfAttempts                                    │
│         │  MAX_ATTEMPTS wrong keys in sequence → lockout triggered.   │
│         │  Analogy: account locked after N failed logins               │
├─────────┼──────────────────────────────────────────────────────────────┤
│  0x37   │  requiredTimeDelayNotExpired                                 │
│         │  Lockout timer is active — any 0x27 request returns this.   │
│         │  Timer duration is ECU-defined (seconds to minutes).        │
│         │  Analogy: "Too many attempts. Wait 30 seconds before retry." │
└─────────┴──────────────────────────────────────────────────────────────┘
```

### The Brute-Force Protection Sequence

```
Attempt 1: requestSeed → seed_A → sendKey(wrong) → NRC 0x35  [count=1]
Attempt 2: requestSeed → seed_B → sendKey(wrong) → NRC 0x35  [count=2]
Attempt 3: requestSeed → seed_C → sendKey(wrong) → NRC 0x36  [LOCKOUT]

During lockout:
  Any 0x27 request → NRC 0x37

After lockout timer expires:
  requestSeed → seed_D → sendKey(correct) → NRC 0x67 0x02  [unlocked]
```

> ⚠️ **Critical tester trap:** Each wrong attempt consumes a seed. After each `0x35`, you must request a **new seed** (not reuse the old one) before sending the next key. The ECU clears `_seed_issued` after every key attempt (right or wrong). Reusing the old seed → `0x24 requestSequenceError` (key sent without a valid pending seed).

> 🌉 **From your world:** This is the **OAuth lockout / account brute-force protection** pattern. You've tested it for web apps: N failed logins → account locked → cooldown period. Same test strategy applies here: verify the exact N that triggers lockout, verify the lockout period is correct, verify that the counter resets after a successful unlock.

---

## 🧠 Concept: Key Algorithm Design — Why You Never Hardcode Keys in Test Scripts

### The Simulation vs Reality Gap

In today's simulation we use:
```python
key = seed ^ 0xDEADBEEF   # XOR — SIMULATION ONLY
```

This is pedagogically clear but **cryptographically worthless**. Real ECU implementations use:

```
┌──────────────────────────────────────────────────────────────────┐
│  REAL SECURITY ACCESS ALGORITHMS (examples)                      │
├──────────────────────────────────────────────────────────────────┤
│  AES-CMAC (Cipher-based MAC)                                    │
│  key = AES-CMAC(seed, secret_key_material)                      │
│  Used by: most modern AUTOSAR-compliant ECUs                    │
├──────────────────────────────────────────────────────────────────┤
│  HMAC-SHA256                                                    │
│  key = HMAC-SHA256(seed, shared_secret)                         │
│  Used by: ECUs targeting EVITA/ISO 21434                        │
├──────────────────────────────────────────────────────────────────┤
│  OEM-proprietary                                                │
│  key = ManufacturerSpecificAlgorithm(seed)                      │
│  Delivered as a compiled DLL / shared library to authorised     │
│  test tool vendors — never as source code                       │
└──────────────────────────────────────────────────────────────────┘
```

### Why You Must Never Hardcode the Secret

```python
# ❌ WRONG — Never do this in a test script
SECURITY_KEY = 0xDEADBEEF   # leaked in git, leaked in CI logs

# ✅ CORRECT — Load from secure secret store
import os
SECURITY_SECRET = int(os.environ["SA_SECRET"], 16)

# ✅ ALSO CORRECT — Use the OEM-supplied algorithm library
from oem_sec_lib import derive_key   # compiled, key material inside the .so
key = derive_key(seed, level=1)
```

> **The security tester's golden rule:** The key derivation function lives in the **test infrastructure** (environment variable, secrets vault, HSM, compiled library). It **never** lives in the test script source code that gets committed to version control. This is why even if your CI pipeline leaks the test source code, the secrets are not exposed.

> 🌉 **From your world:** This is exactly how you handle API keys in web testing — you inject them via environment variables (`process.env.API_KEY`) or secrets management (GitHub Secrets, AWS Secrets Manager). You've been doing this for years. The UDS equivalent is `os.environ["SA_SECRET"]` instead of `const API_KEY = "abc123"` hardcoded in the test file.

### Timing Side-Channel Warning

A subtle security testing concern: the ECU's key comparison should take the **same amount of time regardless of how many bytes are wrong** (constant-time comparison). If comparison short-circuits on the first wrong byte, an attacker can measure response times and deduce the key byte by byte.

```python
# ❌ Vulnerable comparison (short-circuits on first wrong byte)
if received_key == expected_key:

# ✅ Constant-time comparison (same time regardless of where it differs)
import hmac
if hmac.compare_digest(
    expected_key.to_bytes(4, 'big'),
    received_key.to_bytes(4, 'big')
):
```

This is a real security finding in ECU penetration tests. If an ECU's SecurityAccess response time varies with how many bytes are correct, you can enumerate the key byte-by-byte in O(256 × 4) attempts instead of O(256^4).

---

## 🧠 Concept: What Is RoutineControl (0x31)?

### The "Make the ECU Do Something" Service

All the UDS services so far have been about **reading state** (0x22) or **writing state** (0x2E). RoutineControl is different — it's about **triggering actions** inside the ECU.

Think of it as calling a function rather than reading or writing a variable:

```
0x22  →  GET  /ecu/vin              (read a value)
0x2E  →  PUT  /ecu/calibration/rpm  (write a value)
0x31  →  POST /ecu/routines/self-test/start   (trigger an action)
```

What kind of actions?
- **Self-test:** "Run all your internal diagnostics and tell me if everything is OK"
- **Calibration:** "Do the sensor zero-point calibration procedure now"
- **EOL routine:** "Perform the End-of-Line acceptance test"
- **Drive cycle check:** "Has the OBD monitor drive cycle been completed?"
- **Actuator test:** "Activate the fuel injector on cylinder 3 once"
- **Memory check:** "Verify checksum of your own flash memory"

> 🌉 **From your world:** RoutineControl is a **REST API action resource** (verb resource pattern). In REST you sometimes have resources like `POST /jobs/analysis/start` instead of just CRUD on a data resource. A RoutineControl RID is exactly that — a job you can start, stop, and poll for results. It maps perfectly to a background task API: start it, poll it, get results.

---

## 🧠 Concept: The Three Sub-Functions — Start / Stop / Results

Every RoutineControl operation uses one of three sub-functions:

```
┌──────────────────────────────────────────────────────────────────────┐
│  ROUTINECONTROL (0x31) — SUB-FUNCTIONS                               │
├────────────┬───────────────────────────────────────────────────────┤
│  0x01      │  startRoutine                                          │
│  START     │  Trigger the routine to begin execution.               │
│            │  Response includes a status byte:                      │
│            │    0x01 = routine is now running                       │
│            │  If already running: response 0x01 (still running)    │
├────────────┼───────────────────────────────────────────────────────┤
│  0x02      │  stopRoutine                                           │
│  STOP      │  Abort a running routine before it completes.         │
│            │  Response: 0x71 0x02 [RID_H] [RID_L]                  │
│            │  Error: if routine isn't running → NRC 0x22           │
├────────────┼───────────────────────────────────────────────────────┤
│  0x03      │  requestRoutineResults                                 │
│  RESULTS   │  Poll for the routine's completion status and output. │
│            │  Response status byte:                                 │
│            │    0x01 = still running                                │
│            │    0x02 = completed successfully                       │
│            │    0x03 = stopped (by stopRoutine)                     │
│            │    0x04 = failed                                       │
│            │  Followed by result bytes (routine-specific).          │
│            │  Error: if routine never started → NRC 0x22           │
└────────────┴───────────────────────────────────────────────────────┘
```

### Request/Response Wire Format

```
Request: start self-test routine (RID 0xFF00)
  [0x04, 0x31, 0x01, 0xFF, 0x00]
   ^^^   ^^^^  ^^^^  ^^^^^^^^^
   PCI   SID   sub   RID bytes (big-endian)

Positive Response:
  [0x05, 0x71, 0x01, 0xFF, 0x00, 0x01]
         ^^^^  ^^^^  ^^^^^^^^^  ^^^^
        0x31+  sub   RID echo   status=running
        0x40

requestRoutineResults after completion:
  [0x05, 0x71, 0x03, 0xFF, 0x00, 0x02, 0x00]
         ^^^^  ^^^^  ^^^^^^^^^  ^^^^  ^^^^
        0x71   0x03  RID echo   RS_COMPLETED  result byte
```

> **The polling pattern:** RoutineControl is inherently asynchronous. You start it (`RC_START`), the ECU starts the routine in the background, and you periodically call `RC_RESULTS` until you get `RS_COMPLETED` or `RS_FAILED`. This is the **polling pattern** — identical to polling a job API for completion. In web testing you've built retry-with-backoff for async API calls. Same here.

---

## 🧠 Concept: Routine Identifiers (RIDs)

Like DIDs, RIDs are **two-byte addresses**. ISO 14229 reserves certain ranges:

```
┌─────────────────────────────────────────────────────────────────────┐
│  RID RANGE ASSIGNMENTS                                               │
├────────────────┬────────────────────────────────────────────────────┤
│  0x0000–0x00FF │  ISO SAE Reserved                                  │
│  0x0100–0xDEFF │  Manufacturer-defined (OEM custom routines)        │
│  0xDF00–0xDFFF │  Diagnostic routines (OEM diagnostic check)        │
│  0xE000–0xEFFF │  System supplier-defined                           │
│  0xF000–0xFEFF │  ISO SAE Standardised                              │
│  0xFF00        │  EraseMemory — but commonly used as "self-test"    │
│  0xFF01        │  CheckProgrammingDependencies                      │
│  0xFF02        │  EraseMirrorMemoryDTCs                             │
│  0xFFFF        │  ISO SAE Reserved                                  │
└────────────────┴────────────────────────────────────────────────────┘

Our simulation uses:
  0xFF00 — SelfTest            (200ms, no security needed)
  0x0203 — SensorCalibration   (300ms, requires SecurityAccess level 1)
  0xDF00 — DTCMemoryCheck      (150ms, no security needed)
```

> **Interview insight:** "What's the difference between a DID and a RID?" DID = address for a data *value* (noun). RID = address for a *procedure* (verb). You read/write DIDs. You start/stop/query RIDs. They're different address spaces (0x22/0x2E vs 0x31) and serve different purposes. Testers often conflate them — they're architecturally distinct.

---

## 🧠 Concept: NRCs Specific to 0x27 and 0x31

```
┌────────┬──────────────────────────────┬────────────────────────────────────┐
│  NRC   │  Name                        │  0x27 / 0x31 Context               │
├────────┼──────────────────────────────┼────────────────────────────────────┤
│  0x22  │  conditionsNotCorrect        │  0x27: wrong session (need ≥extended)│
│        │                              │  0x31: wrong session or routine in  │
│        │                              │  wrong state (e.g., stop non-running)│
├────────┼──────────────────────────────┼────────────────────────────────────┤
│  0x24  │  requestSequenceError        │  0x27: sendKey without requestSeed  │
│        │                              │  (wrong order — no pending seed)    │
├────────┼──────────────────────────────┼────────────────────────────────────┤
│  0x31  │  requestOutOfRange           │  0x31: unknown RID (routine not     │
│        │                              │  supported by this ECU variant)     │
├────────┼──────────────────────────────┼────────────────────────────────────┤
│  0x33  │  securityAccessDenied        │  0x31: routine requires SecurityAccess│
│        │                              │  and it hasn't been unlocked        │
├────────┼──────────────────────────────┼────────────────────────────────────┤
│  0x35  │  invalidKey                  │  0x27: wrong key sent; attempt      │
│        │                              │  counter incremented                │
├────────┼──────────────────────────────┼────────────────────────────────────┤
│  0x36  │  exceededNumberOfAttempts    │  0x27: MAX_ATTEMPTS wrong keys;     │
│        │                              │  lockout period starts              │
├────────┼──────────────────────────────┼────────────────────────────────────┤
│  0x37  │  requiredTimeDelayNotExpired │  0x27: locked out, wait for timer   │
└────────┴──────────────────────────────┴────────────────────────────────────┘
```

---

## 🧩 The Big Picture: Security + Routines in the UDS Stack

```
┌──────────────────────────────────────────────────────────────────────┐
│  UDS SERVICES — BUILDING A FULL DIAGNOSTIC SEQUENCE                  │
│                                                                      │
│  Power on → defaultSession (always)                                  │
│                                                                      │
│  STEP 1: DiagnosticSessionControl (0x10)                            │
│  → Switch to extendedDiagnosticSession                               │
│                                                                      │
│  STEP 2: SecurityAccess (0x27)  ←── TODAY                           │
│  → requestSeed (0x27 0x01) → compute key → sendKey (0x27 0x02)      │
│  → Now UNLOCKED for protected operations                             │
│                                                                      │
│  STEP 3a: WriteDataByIdentifier (0x2E)  ← Day 13                   │
│  → Write calibration DIDs (now that security is unlocked)           │
│                                                                      │
│  STEP 3b: RoutineControl (0x31)  ←── TODAY                         │
│  → startRoutine (0x31 0x01) → poll requestRoutineResults (0x31 0x03)│
│  → Wait for RS_COMPLETED → verify result                            │
│                                                                      │
│  STEP 4: ECUReset (0x11)  ← Day 12                                  │
│  → Hard/soft reset to apply written calibration                     │
│                                                                      │
│  STEP 5: ReadDataByIdentifier (0x22)  ← Day 13                     │
│  → Read-back to verify calibration persisted (NVM check)            │
│                                                                      │
│  This five-step sequence is a complete calibration test cycle.      │
│  Every UDS test at senior level involves chaining these services.   │
└──────────────────────────────────────────────────────────────────────┘
```

> 🌉 **From your world:** This is an **end-to-end workflow test** — exactly like testing "register → verify email → login → change password → re-login" in a web app. Each step has a precondition (the previous step's output) and a post-condition (verified by the next step). The services chain. A tester who only tests individual services but never tests the full chain misses the integration failures.

---

## 🌍 Where It's Used in the Real World

| Context | SecurityAccess (0x27) | RoutineControl (0x31) |
|---|---|---|
| **EOL production** | Unlock level 1 to write VIN/serial onto blank ECU | Run self-test routine to verify ECU is functional before shipping |
| **Workshop calibration** | Unlock to write steering angle sensor offset after replacement | Start calibration routine to find sensor zero-point |
| **OTA firmware update** | Unlock level 3 (programming session) before flash sequence | Run CheckProgrammingDependencies (0xFF01) post-flash |
| **HIL regression** | Inject security access unlock as test setup fixture | Trigger DTC memory check routine, verify 0 faults |
| **Penetration testing** | Attempt brute-force, verify lockout fires at N attempts | Attempt to start security-gated routine without unlock |
| **Supplier acceptance test** | Verify wrong key returns 0x35, not silence or crash | Verify routine results are available within timing SLA |

---

## 🔬 How a Tester Thinks About It

```
┌────────────────────────────────────────────────────────────────────┐
│  TESTER'S CHECKLIST — 0x27 and 0x31                                │
├────────────────────────────────────────────────────────────────────┤
│                                                                    │
│  SECURITYACCESS (0x27)                                             │
│  ✓ requestSeed in default session → NRC 0x22?                     │
│  ✓ requestSeed in extended session → non-zero seed received?      │
│  ✓ Correct key derived and sent → positive response?              │
│  ✓ After unlock: write-protected DID now writable?                │
│  ✓ sendKey without prior requestSeed → NRC 0x24?                  │
│  ✓ Wrong key → NRC 0x35? (first attempt)                          │
│  ✓ N wrong keys → NRC 0x36 (lockout)?                             │
│  ✓ During lockout → NRC 0x37?                                     │
│  ✓ Lockout timer: how long? Does it match spec?                   │
│  ✓ After lockout expiry: can unlock again?                        │
│  ✓ Session drop (S3 / reset) → security back to locked?           │
│  ✓ requestSeed when already unlocked → zero seed?                 │
│  ✓ Key algorithm: constant-time comparison (no timing attack)?    │
│                                                                    │
│  ROUTINECONTROL (0x31)                                             │
│  ✓ startRoutine in default session → NRC 0x22?                    │
│  ✓ startRoutine in extended session → RS_RUNNING?                 │
│  ✓ requestRoutineResults while running → RS_RUNNING?              │
│  ✓ Wait for completion → RS_COMPLETED?                            │
│  ✓ Result data correct? (routine-specific verification)           │
│  ✓ stopRoutine while running → success?                           │
│  ✓ stopRoutine on idle/completed routine → NRC 0x22?              │
│  ✓ requestRoutineResults before starting → NRC 0x22?             │
│  ✓ Unknown RID → NRC 0x31?                                        │
│  ✓ Security-gated routine without unlock → NRC 0x33?              │
│  ✓ Routine completion timing: within SLA?                         │
│  ✓ Failed routine: RS_FAILED returned (not silence)?              │
└────────────────────────────────────────────────────────────────────┘
```

### The Routine Polling Pattern

```python
# ✅ Correct — poll with timeout
def wait_for_routine(tester, rid_h, rid_l, timeout_s=5.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        tester._send([SID_ROUTINE, RC_RESULTS, rid_h, rid_l])
        resp = tester._recv()
        status = resp[4] if resp and len(resp) >= 5 else None
        if status == RS_COMPLETED:
            return resp   # done
        if status == RS_FAILED:
            raise RuntimeError("Routine failed")
        if status == RS_RUNNING:
            time.sleep(0.05)   # back-off between polls
            continue
    raise TimeoutError("Routine did not complete within timeout")
```

> **Do not busy-poll.** Always add a small sleep between result requests. A test that hammers the ECU with 1000 result requests per second is a denial-of-service test, not a functional test. Use exponential back-off or a fixed 50–100ms interval between polls.

---

## 🛠️ Hands-On Exercise: Security & Routine Simulator

### What You'll Build

```
Day-14_UDS_Security_Routine/
├── uds_security_routine.py   ← full simulation + 16 test cases
└── Day14_UDS_Security_Routine.md
```

**ECU internals:**
- SecurityAccess level 1: seed = random 4 bytes; key = seed XOR 0xDEADBEEF (simulation only)
- Lockout: 3 wrong keys → 3-second lockout
- Routines:
  - `0xFF00` SelfTest (200ms, no security needed, result = `0x00` pass)
  - `0x0203` SensorCalibration (300ms, requires security unlock, result = 2-byte offset)
  - `0xDF00` DTCMemoryCheck (150ms, no security needed, result = 2-byte DTC count)

---

## 🛠️ `uds_security_routine.py` — Full Listing

```python
"""
Day 14: UDS SecurityAccess (0x27) & RoutineControl (0x31)
==========================================================
Simulates an ECU with a seed/key security challenge-response and
a set of routine-control procedures (self-test, sensor calibration,
DTC memory check) on a python-can virtual bus.

No hardware needed.

Install:
    pip install python-can

Run:
    python uds_security_routine.py
"""

import can
import threading
import time
import random
import struct

# ─── UDS CONSTANTS ───────────────────────────────────────────────────────────

CHANNEL      = "vcan0"
TESTER_TX_ID = 0x7E0
TESTER_RX_ID = 0x7E8

SID_SESSION = 0x10
SID_SEC     = 0x27    # SecurityAccess
SID_ROUTINE = 0x31    # RoutineControl
SID_NEG     = 0x7F

SESSION_DEFAULT     = 0x01
SESSION_PROGRAMMING = 0x02
SESSION_EXTENDED    = 0x03

SEC_REQ_SEED = 0x01   # odd  = requestSeed
SEC_SEND_KEY = 0x02   # even = sendKey

RC_START   = 0x01
RC_STOP    = 0x02
RC_RESULTS = 0x03

RID_SELF_TEST  = 0xFF00
RID_SENSOR_CAL = 0x0203
RID_DTC_CHECK  = 0xDF00

NRC_SUBFUNC_NOT_SUPPORTED  = 0x12
NRC_INCORRECT_MSG_LENGTH   = 0x13
NRC_CONDITIONS_NOT_CORRECT = 0x22
NRC_REQUEST_SEQUENCE_ERROR = 0x24
NRC_REQUEST_OUT_OF_RANGE   = 0x31
NRC_SECURITY_ACCESS_DENIED = 0x33
NRC_INVALID_KEY            = 0x35
NRC_EXCEEDED_ATTEMPTS      = 0x36
NRC_REQUIRED_TIME_DELAY    = 0x37

SEC_SECRET       = 0xDEADBEEF   # XOR mask — SIMULATION ONLY
SEC_MAX_ATTEMPTS = 3
SEC_LOCKOUT_SECS = 3.0

RS_RUNNING   = 0x01
RS_COMPLETED = 0x02
RS_STOPPED   = 0x03
RS_FAILED    = 0x04


# ─── ISO-TP HELPERS ───────────────────────────────────────────────────────────

def build_single_frame(uds_bytes: list) -> bytes:
    assert 1 <= len(uds_bytes) <= 7
    pci    = len(uds_bytes)
    padded = [pci] + uds_bytes + [0x00] * (7 - len(uds_bytes))
    return bytes(padded)


def build_multi_frame_response(uds_bytes: list) -> list:
    total  = len(uds_bytes)
    frames = []
    ff     = [0x10 | ((total >> 8) & 0x0F), total & 0xFF] + uds_bytes[:6]
    frames.append(bytes(ff))
    sn, offset = 1, 6
    while offset < total:
        chunk = uds_bytes[offset: offset + 7]
        cf    = [0x20 | (sn & 0x0F)] + chunk + [0x00] * (7 - len(chunk))
        frames.append(bytes(cf))
        sn     = (sn + 1) & 0x0F
        offset += 7
    return frames


def parse_uds_from_frame(data: bytes):
    pci = data[0]
    if (pci & 0xF0) != 0x00:
        return None
    length = pci & 0x0F
    return list(data[1: 1 + length])


# ─── SECURITY ACCESS STATE ────────────────────────────────────────────────────

class SecurityState:
    def __init__(self):
        self.unlocked       = False
        self._seed          = 0
        self._seed_issued   = False
        self._failed_count  = 0
        self._lockout_until = 0.0

    def is_locked_out(self) -> bool:
        return time.monotonic() < self._lockout_until

    def issue_seed(self) -> int:
        if self.unlocked:
            return 0x00000000
        seed              = random.randint(0x00000001, 0xFFFFFFFF)
        self._seed        = seed
        self._seed_issued = True
        return seed

    def verify_key(self, key: int) -> str:
        if not self._seed_issued:
            return "no_seed"
        expected          = self._derive_key(self._seed)
        self._seed_issued = False
        if key == expected:
            self.unlocked      = True
            self._failed_count = 0
            return "ok"
        self._failed_count += 1
        if self._failed_count >= SEC_MAX_ATTEMPTS:
            self._lockout_until = time.monotonic() + SEC_LOCKOUT_SECS
            self._failed_count  = 0
            return "lockout"
        return "wrong"

    def lock(self) -> None:
        self.unlocked      = False
        self._seed_issued  = False
        self._failed_count = 0

    @staticmethod
    def _derive_key(seed: int) -> int:
        """⚠️ SIMULATION ONLY — XOR. Never use in production."""
        return seed ^ SEC_SECRET


# ─── ROUTINE STATE ────────────────────────────────────────────────────────────

class RoutineState:
    def __init__(self, rid: int, name: str, duration_ms: float,
                 requires_security: bool = False):
        self.rid               = rid
        self.name              = name
        self.duration_ms       = duration_ms
        self.requires_security = requires_security
        self._status           = "idle"
        self._start_t          = None
        self._result: bytes    = b""

    @property
    def status(self) -> str:
        if self._status == "running":
            if (time.monotonic() - self._start_t) * 1000 >= self.duration_ms:
                self._status = "completed"
                self._result = self._generate_result()
        return self._status

    def start(self) -> None:
        self._status  = "running"
        self._start_t = time.monotonic()
        self._result  = b""

    def stop(self) -> None:
        self._status = "stopped"

    def _generate_result(self) -> bytes:
        if self.rid == RID_SELF_TEST:
            return bytes([0x00])
        elif self.rid == RID_SENSOR_CAL:
            return struct.pack(">H", 125)
        elif self.rid == RID_DTC_CHECK:
            return struct.pack(">H", 0)
        return b""


# ─── SIMULATED ECU ────────────────────────────────────────────────────────────

class SimulatedECU(threading.Thread):

    S3_TIMEOUT_S = 5.0

    def __init__(self):
        super().__init__(daemon=True, name="SimECU")
        self.bus          = can.Bus(interface="virtual", channel=CHANNEL)
        self.session      = SESSION_DEFAULT
        self.security     = SecurityState()
        self._stop        = threading.Event()
        self._last_diag_t = time.monotonic()
        self._routines    = {
            RID_SELF_TEST:  RoutineState(RID_SELF_TEST,  "SelfTest",    200.0),
            RID_SENSOR_CAL: RoutineState(RID_SENSOR_CAL, "SensorCal",   300.0,
                                         requires_security=True),
            RID_DTC_CHECK:  RoutineState(RID_DTC_CHECK,  "DTCMemCheck", 150.0),
        }

    def stop(self) -> None:
        self._stop.set()
        self.bus.shutdown()

    def _send_raw(self, payload: list) -> None:
        if len(payload) <= 7:
            self.bus.send(can.Message(arbitration_id=TESTER_RX_ID,
                                      data=build_single_frame(payload),
                                      is_extended_id=False))
        else:
            for fd in build_multi_frame_response(payload):
                self.bus.send(can.Message(arbitration_id=TESTER_RX_ID,
                                          data=fd, is_extended_id=False))
                time.sleep(0.001)

    def _neg(self, sid: int, nrc: int) -> None:
        self._send_raw([SID_NEG, sid, nrc])

    def _handle_session(self, sub: int) -> None:
        valid = {SESSION_DEFAULT, SESSION_PROGRAMMING, SESSION_EXTENDED}
        if sub not in valid:
            self._neg(SID_SESSION, NRC_SUBFUNC_NOT_SUPPORTED); return
        if self.session == SESSION_DEFAULT and sub == SESSION_PROGRAMMING:
            self._neg(SID_SESSION, 0x24); return
        self.session = sub
        if sub == SESSION_DEFAULT:
            self.security.lock()
        self._last_diag_t = time.monotonic()
        self._send_raw([SID_SESSION + 0x40, sub, 0x00, 0x19, 0x01, 0xF4])

    def _handle_security(self, uds: list) -> None:
        if len(uds) < 2:
            self._neg(SID_SEC, NRC_INCORRECT_MSG_LENGTH); return
        sub = uds[1]
        if self.session == SESSION_DEFAULT:
            self._neg(SID_SEC, NRC_CONDITIONS_NOT_CORRECT); return
        if self.security.is_locked_out():
            self._neg(SID_SEC, NRC_REQUIRED_TIME_DELAY); return
        if sub == SEC_REQ_SEED:
            seed = self.security.issue_seed()
            self._send_raw([SID_SEC + 0x40, sub,
                             (seed >> 24) & 0xFF, (seed >> 16) & 0xFF,
                             (seed >> 8)  & 0xFF,  seed        & 0xFF])
        elif sub == SEC_SEND_KEY:
            if len(uds) < 6:
                self._neg(SID_SEC, NRC_INCORRECT_MSG_LENGTH); return
            key    = (uds[2] << 24) | (uds[3] << 16) | (uds[4] << 8) | uds[5]
            result = self.security.verify_key(key)
            if result == "ok":       self._send_raw([SID_SEC + 0x40, sub])
            elif result == "wrong":  self._neg(SID_SEC, NRC_INVALID_KEY)
            elif result == "lockout":self._neg(SID_SEC, NRC_EXCEEDED_ATTEMPTS)
            elif result == "no_seed":self._neg(SID_SEC, NRC_REQUEST_SEQUENCE_ERROR)
        else:
            self._neg(SID_SEC, NRC_SUBFUNC_NOT_SUPPORTED)

    def _handle_routine(self, uds: list) -> None:
        if len(uds) < 4:
            self._neg(SID_ROUTINE, NRC_INCORRECT_MSG_LENGTH); return
        sub     = uds[1]
        rid     = (uds[2] << 8) | uds[3]
        if self.session == SESSION_DEFAULT:
            self._neg(SID_ROUTINE, NRC_CONDITIONS_NOT_CORRECT); return
        if rid not in self._routines:
            self._neg(SID_ROUTINE, NRC_REQUEST_OUT_OF_RANGE); return
        routine = self._routines[rid]
        if sub == RC_START:
            if routine.requires_security and not self.security.unlocked:
                self._neg(SID_ROUTINE, NRC_SECURITY_ACCESS_DENIED); return
            if routine.status != "running":
                routine.start()
            self._send_raw([SID_ROUTINE + 0x40, sub,
                             (rid >> 8) & 0xFF, rid & 0xFF, RS_RUNNING])
        elif sub == RC_STOP:
            if routine.status != "running":
                self._neg(SID_ROUTINE, NRC_CONDITIONS_NOT_CORRECT); return
            routine.stop()
            self._send_raw([SID_ROUTINE + 0x40, sub,
                             (rid >> 8) & 0xFF, rid & 0xFF])
        elif sub == RC_RESULTS:
            st = routine.status
            if st == "idle":
                self._neg(SID_ROUTINE, NRC_CONDITIONS_NOT_CORRECT); return
            sb  = {
                "running": RS_RUNNING, "completed": RS_COMPLETED,
                "stopped": RS_STOPPED,
            }.get(st, RS_FAILED)
            res = routine._result if st == "completed" else b""
            self._send_raw([SID_ROUTINE + 0x40, sub,
                             (rid >> 8) & 0xFF, rid & 0xFF, sb] + list(res))
        else:
            self._neg(SID_ROUTINE, NRC_SUBFUNC_NOT_SUPPORTED)

    def run(self) -> None:
        while not self._stop.is_set():
            if (self.session != SESSION_DEFAULT
                    and time.monotonic() - self._last_diag_t > self.S3_TIMEOUT_S):
                self.session = SESSION_DEFAULT
                self.security.lock()
            frame = self.bus.recv(timeout=0.05)
            if frame is None or frame.arbitration_id != TESTER_TX_ID:
                continue
            self._last_diag_t = time.monotonic()
            uds = parse_uds_from_frame(bytes(frame.data))
            if uds is None or len(uds) < 1:
                continue
            sid = uds[0]
            if   sid == SID_SESSION and len(uds) >= 2: self._handle_session(uds[1])
            elif sid == SID_SEC:                        self._handle_security(uds)
            elif sid == SID_ROUTINE:                    self._handle_routine(uds)
            else:                                       self._neg(sid, 0x11)


# ─── TESTER CLIENT ────────────────────────────────────────────────────────────

class UDSTester:

    RESPONSE_TIMEOUT_S = 2.0

    def __init__(self):
        self.bus    = can.Bus(interface="virtual", channel=CHANNEL)
        self.passed = []
        self.failed = []

    def shutdown(self) -> None:
        self.bus.shutdown()

    def _send(self, uds_bytes: list) -> None:
        self.bus.send(can.Message(arbitration_id=TESTER_TX_ID,
                                  data=build_single_frame(uds_bytes),
                                  is_extended_id=False))

    def _recv(self, timeout: float = None):
        deadline = time.monotonic() + (timeout or self.RESPONSE_TIMEOUT_S)
        collected_payload, total_expected = [], 0
        while time.monotonic() < deadline:
            frame = self.bus.recv(timeout=max(0.01, deadline - time.monotonic()))
            if frame is None or frame.arbitration_id != TESTER_RX_ID:
                continue
            first_byte = frame.data[0]
            pci_type   = (first_byte & 0xF0) >> 4
            if pci_type == 0x0:
                uds = list(frame.data[1: 1 + (first_byte & 0x0F)])
                if len(uds) == 3 and uds[0] == SID_NEG and uds[2] == 0x78:
                    deadline += 5.0; continue
                return uds
            elif pci_type == 0x1:
                total_expected    = ((first_byte & 0x0F) << 8) | frame.data[1]
                collected_payload = list(frame.data[2:])
                self.bus.send(can.Message(
                    arbitration_id=TESTER_TX_ID,
                    data=bytes([0x30, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]),
                    is_extended_id=False))
            elif pci_type == 0x2:
                collected_payload += list(frame.data[1:])
                if len(collected_payload) >= total_expected - 2:
                    return collected_payload[:total_expected]
        return collected_payload if collected_payload else None

    def _pass(self, name: str, detail: str = "") -> None:
        tag = f"  ✅ PASS  {name}" + (f"  [{detail}]" if detail else "")
        print(tag); self.passed.append(tag)

    def _fail(self, name: str, detail: str = "") -> None:
        tag = f"  ❌ FAIL  {name}" + (f"  [{detail}]" if detail else "")
        print(tag); self.failed.append(tag)

    def _assert_positive(self, name, resp, expected_sid, expected_sub):
        if resp is None:
            self._fail(name, "no response"); return False
        if resp[0] == SID_NEG:
            self._fail(name, f"NRC=0x{resp[2]:02X}"); return False
        if resp[0] != expected_sid + 0x40:
            self._fail(name, f"bad SID 0x{resp[0]:02X}"); return False
        if len(resp) < 2 or resp[1] != expected_sub:
            self._fail(name, f"bad sub 0x{resp[1]:02X}"); return False
        self._pass(name, f"0x{resp[0]:02X} 0x{resp[1]:02X}"); return True

    def _assert_negative(self, name, resp, expected_nrc):
        if resp is None:
            self._fail(name, "no response"); return False
        if resp[0] != SID_NEG:
            self._fail(name, f"expected NegResp, got 0x{resp[0]:02X}"); return False
        actual = resp[2] if len(resp) >= 3 else 0
        if actual != expected_nrc:
            self._fail(name, f"expected 0x{expected_nrc:02X} got 0x{actual:02X}"); return False
        self._pass(name, f"NRC=0x{actual:02X}"); return True

    def _switch_session(self, session_type: int) -> None:
        self._send([SID_SESSION, session_type]); self._recv()

    def _do_security_unlock(self) -> bool:
        self._send([SID_SEC, SEC_REQ_SEED])
        resp = self._recv()
        if resp is None or resp[0] != SID_SEC + 0x40 or len(resp) < 6:
            return False
        seed = (resp[2] << 24) | (resp[3] << 16) | (resp[4] << 8) | resp[5]
        if seed == 0:
            return True
        key = seed ^ SEC_SECRET
        self._send([SID_SEC, SEC_SEND_KEY,
                    (key >> 24) & 0xFF, (key >> 16) & 0xFF,
                    (key >> 8)  & 0xFF,  key        & 0xFF])
        resp2 = self._recv()
        return (resp2 is not None and resp2[0] == SID_SEC + 0x40
                and resp2[1] == SEC_SEND_KEY)

    # ── Test Cases ────────────────────────────────────────────────

    def tc01_seed_in_default_session(self):
        self._switch_session(SESSION_DEFAULT)
        self._send([SID_SEC, SEC_REQ_SEED])
        self._assert_negative("TC01 requestSeed in default → NRC 0x22",
                              self._recv(), NRC_CONDITIONS_NOT_CORRECT)

    def tc02_seed_in_extended_session(self):
        self._switch_session(SESSION_EXTENDED)
        self._send([SID_SEC, SEC_REQ_SEED])
        resp = self._recv()
        if not self._assert_positive("TC02 requestSeed in extended",
                                      resp, SID_SEC, SEC_REQ_SEED): return
        if len(resp) >= 6:
            seed = (resp[2] << 24) | (resp[3] << 16) | (resp[4] << 8) | resp[5]
            if seed != 0:
                self._pass("TC02 Non-zero seed received", f"0x{seed:08X}")
            else:
                self._fail("TC02 Seed is non-zero", "got 0x00000000")

    def tc03_correct_key_unlocks(self):
        self._switch_session(SESSION_EXTENDED)
        self._send([SID_SEC, SEC_REQ_SEED])
        resp = self._recv()
        if resp is None or resp[0] == SID_NEG:
            self._fail("TC03 setup: requestSeed failed"); return
        seed = (resp[2] << 24) | (resp[3] << 16) | (resp[4] << 8) | resp[5]
        key  = seed ^ SEC_SECRET
        self._send([SID_SEC, SEC_SEND_KEY,
                    (key >> 24) & 0xFF, (key >> 16) & 0xFF,
                    (key >> 8)  & 0xFF,  key        & 0xFF])
        self._assert_positive("TC03 sendKey correct → unlocked",
                               self._recv(), SID_SEC, SEC_SEND_KEY)

    def tc04_sendkey_without_seed(self):
        self._switch_session(SESSION_EXTENDED)
        self._send([SID_SEC, SEC_SEND_KEY, 0xDE, 0xAD, 0xBE, 0xEF])
        self._assert_negative("TC04 sendKey without seed → NRC 0x24",
                              self._recv(), NRC_REQUEST_SEQUENCE_ERROR)

    def tc05_wrong_key_nrc35(self):
        self._switch_session(SESSION_DEFAULT)
        self._switch_session(SESSION_EXTENDED)
        self._send([SID_SEC, SEC_REQ_SEED]); self._recv()
        self._send([SID_SEC, SEC_SEND_KEY, 0x00, 0x00, 0x00, 0x01])
        self._assert_negative("TC05 Wrong key → NRC 0x35",
                              self._recv(), NRC_INVALID_KEY)

    def tc06_lockout_after_three_fails(self):
        self._switch_session(SESSION_DEFAULT)
        self._switch_session(SESSION_EXTENDED)
        for attempt in range(1, SEC_MAX_ATTEMPTS + 1):
            self._send([SID_SEC, SEC_REQ_SEED])
            sr = self._recv()
            if sr is None or sr[0] == SID_NEG:
                self._fail(f"TC06 attempt {attempt}: seed failed"); break
            self._send([SID_SEC, SEC_SEND_KEY, 0x00, 0x00, 0x00, 0x00])
            resp = self._recv()
            if attempt < SEC_MAX_ATTEMPTS:
                ok = resp and resp[0] == SID_NEG and resp[2] == NRC_INVALID_KEY
                (self._pass if ok else self._fail)(
                    f"TC06 attempt {attempt}/{SEC_MAX_ATTEMPTS} → NRC 0x35",
                    "✓" if ok else f"got 0x{resp[2]:02X}" if resp else "timeout")
            else:
                self._assert_negative(
                    f"TC06 attempt {attempt}/{SEC_MAX_ATTEMPTS} → NRC 0x36",
                    resp, NRC_EXCEEDED_ATTEMPTS)

    def tc07_lockout_period(self):
        self._send([SID_SEC, SEC_REQ_SEED])
        self._assert_negative("TC07 During lockout → NRC 0x37",
                              self._recv(), NRC_REQUIRED_TIME_DELAY)
        wait_s = SEC_LOCKOUT_SECS + 0.5
        print(f"    ⏰ Waiting {wait_s:.1f}s for lockout to expire...")
        time.sleep(wait_s)
        self._send([SID_SEC, SEC_REQ_SEED])
        resp2 = self._recv()
        if resp2 and resp2[0] == SID_SEC + 0x40:
            self._pass("TC07 Lockout expired — seed available again", "✓")
        else:
            nrc = f"0x{resp2[2]:02X}" if resp2 and len(resp2) >= 3 else "?"
            self._fail("TC07 Lockout expired", f"still blocked NRC={nrc}")

    def tc08_already_unlocked_zero_seed(self):
        self._switch_session(SESSION_DEFAULT)
        self._switch_session(SESSION_EXTENDED)
        if not self._do_security_unlock():
            self._fail("TC08 setup: unlock failed"); return
        self._send([SID_SEC, SEC_REQ_SEED])
        resp = self._recv()
        if resp is None or resp[0] == SID_NEG:
            self._fail("TC08 requestSeed when unlocked", "NegResp"); return
        if len(resp) >= 6:
            seed = (resp[2] << 24) | (resp[3] << 16) | (resp[4] << 8) | resp[5]
            if seed == 0:
                self._pass("TC08 Already unlocked → zero seed (0x00000000)", "✓")
            else:
                self._fail("TC08 Already unlocked → zero seed", f"got 0x{seed:08X}")

    def tc09_routine_in_default_session(self):
        self._switch_session(SESSION_DEFAULT)
        self._send([SID_ROUTINE, RC_START, 0xFF, 0x00])
        self._assert_negative("TC09 startRoutine in default → NRC 0x22",
                              self._recv(), NRC_CONDITIONS_NOT_CORRECT)

    def tc10_start_self_test(self):
        self._switch_session(SESSION_EXTENDED)
        self._send([SID_ROUTINE, RC_START, 0xFF, 0x00])
        resp = self._recv()
        if not self._assert_positive("TC10 startRoutine SelfTest",
                                      resp, SID_ROUTINE, RC_START): return
        rid_echo = (resp[2] << 8) | resp[3] if len(resp) >= 4 else 0
        status   = resp[4] if len(resp) >= 5 else 0
        (self._pass if rid_echo == RID_SELF_TEST else self._fail)(
            "TC10 RID echoed correctly", f"0x{rid_echo:04X}")
        (self._pass if status == RS_RUNNING else self._fail)(
            "TC10 Status = RS_RUNNING (0x01)",
            "✓" if status == RS_RUNNING else f"got 0x{status:02X}")

    def tc11_results_while_running(self):
        self._send([SID_ROUTINE, RC_RESULTS, 0xFF, 0x00])
        resp = self._recv()
        if resp and resp[0] == SID_ROUTINE + 0x40 and resp[1] == RC_RESULTS:
            s = resp[4] if len(resp) >= 5 else 0
            if s in (RS_RUNNING, RS_COMPLETED):
                self._pass("TC11 requestResults → running or completed", f"0x{s:02X} ✓")
            else:
                self._fail("TC11 unexpected status", f"0x{s:02X}")
        else:
            self._fail("TC11 requestResults while running",
                       f"NRC=0x{resp[2]:02X}" if resp else "timeout")

    def tc12_results_after_completion(self):
        self._send([SID_ROUTINE, RC_START, 0xFF, 0x00]); self._recv()
        print("    ⏱  Waiting 300ms for SelfTest (200ms) to complete...")
        time.sleep(0.3)
        self._send([SID_ROUTINE, RC_RESULTS, 0xFF, 0x00])
        resp = self._recv()
        if resp and resp[0] == SID_ROUTINE + 0x40 and resp[1] == RC_RESULTS:
            s      = resp[4] if len(resp) >= 5 else 0
            result = bytes(resp[5:]) if len(resp) > 5 else b""
            if s == RS_COMPLETED:
                self._pass("TC12 requestResults → RS_COMPLETED", f"result={result.hex()}")
                if result == bytes([0x00]):
                    self._pass("TC12 SelfTest result = 0x00 (pass)", "✓")
                else:
                    self._fail("TC12 SelfTest result", f"unexpected {result.hex()}")
            else:
                self._fail("TC12 expected RS_COMPLETED", f"got 0x{s:02X}")
        else:
            self._fail("TC12 requestResults after completion",
                       f"NRC=0x{resp[2]:02X}" if resp else "timeout")

    def tc13_stop_routine(self):
        self._switch_session(SESSION_EXTENDED)
        self._send([SID_ROUTINE, RC_START, 0xDF, 0x00]); self._recv()
        self._send([SID_ROUTINE, RC_STOP, 0xDF, 0x00])
        self._assert_positive("TC13 stopRoutine DTCCheck",
                               self._recv(), SID_ROUTINE, RC_STOP)

    def tc14_unknown_rid(self):
        self._switch_session(SESSION_EXTENDED)
        self._send([SID_ROUTINE, RC_START, 0x99, 0x99])
        self._assert_negative("TC14 Unknown RID → NRC 0x31",
                              self._recv(), NRC_REQUEST_OUT_OF_RANGE)

    def tc15_security_gated_routine_blocked(self):
        self._switch_session(SESSION_DEFAULT)
        self._switch_session(SESSION_EXTENDED)
        self._send([SID_ROUTINE, RC_START, 0x02, 0x03])
        self._assert_negative("TC15 Security-gated routine → NRC 0x33",
                              self._recv(), NRC_SECURITY_ACCESS_DENIED)

    def tc16_full_security_gated_routine(self):
        self._switch_session(SESSION_DEFAULT)
        self._switch_session(SESSION_EXTENDED)
        if not self._do_security_unlock():
            self._fail("TC16 SecurityAccess unlock failed"); return
        self._pass("TC16 SecurityAccess unlocked", "✓")
        self._send([SID_ROUTINE, RC_START, 0x02, 0x03])
        resp = self._recv()
        if not self._assert_positive("TC16 startRoutine SensorCal (after unlock)",
                                      resp, SID_ROUTINE, RC_START): return
        print("    ⏱  Waiting 400ms for SensorCal (300ms) to complete...")
        time.sleep(0.4)
        self._send([SID_ROUTINE, RC_RESULTS, 0x02, 0x03])
        resp2 = self._recv()
        if resp2 and resp2[0] == SID_ROUTINE + 0x40 and resp2[1] == RC_RESULTS:
            s      = resp2[4] if len(resp2) >= 5 else 0
            result = bytes(resp2[5:]) if len(resp2) > 5 else b""
            if s == RS_COMPLETED:
                cal = struct.unpack(">H", result[:2])[0] if len(result) >= 2 else "?"
                self._pass("TC16 SensorCal completed", f"calibration offset={cal}")
            else:
                self._fail("TC16 SensorCal results", f"status=0x{s:02X}")
        else:
            self._fail("TC16 SensorCal results",
                       f"NRC=0x{resp2[2]:02X}" if resp2 else "timeout")

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


def banner(title: str) -> None:
    print(f"\n{'─'*64}\n  {title}\n{'─'*64}")


def main() -> None:
    print("\n" + "🔐🔑  " * 10)
    print("  Day 14 — UDS SecurityAccess (0x27) &")
    print("           RoutineControl (0x31) Simulator")
    print("🔐🔑  " * 10)

    ecu    = SimulatedECU()
    tester = UDSTester()
    ecu.start()
    time.sleep(0.1)

    banner("GROUP 1: SecurityAccess (0x27) — Session Gating")
    tester.tc01_seed_in_default_session()
    tester.tc02_seed_in_extended_session()

    banner("GROUP 2: SecurityAccess (0x27) — Key Exchange")
    tester.tc03_correct_key_unlocks()
    tester.tc04_sendkey_without_seed()

    banner("GROUP 3: SecurityAccess (0x27) — Brute-Force Protection")
    tester.tc05_wrong_key_nrc35()
    tester.tc06_lockout_after_three_fails()
    tester.tc07_lockout_period()
    tester.tc08_already_unlocked_zero_seed()

    banner("GROUP 4: RoutineControl (0x31) — Basic Usage")
    tester.tc09_routine_in_default_session()
    tester.tc10_start_self_test()
    tester.tc11_results_while_running()
    tester.tc12_results_after_completion()

    banner("GROUP 5: RoutineControl (0x31) — Error Paths & Advanced")
    tester.tc13_stop_routine()
    tester.tc14_unknown_rid()
    tester.tc15_security_gated_routine_blocked()
    tester.tc16_full_security_gated_routine()

    tester.print_summary()
    ecu.stop()
    tester.shutdown()


if __name__ == "__main__":
    main()
```

### Expected Output

```
🔐🔑  🔐🔑  🔐🔑  🔐🔑  🔐🔑  🔐🔑  🔐🔑  🔐🔑  🔐🔑  🔐🔑
  Day 14 — UDS SecurityAccess (0x27) &
           RoutineControl (0x31) Simulator
🔐🔑  🔐🔑  🔐🔑  🔐🔑  🔐🔑  🔐🔑  🔐🔑  🔐🔑  🔐🔑  🔐🔑

────────────────────────────────────────────────────────────────
  GROUP 1: SecurityAccess (0x27) — Session Gating
────────────────────────────────────────────────────────────────
  ✅ PASS  TC01 requestSeed in default → NRC 0x22  [NRC=0x22]
  ✅ PASS  TC02 requestSeed in extended  [0x67 0x01]
  ✅ PASS  TC02 Non-zero seed received  [0x3F8A21CD]

────────────────────────────────────────────────────────────────
  GROUP 2: SecurityAccess (0x27) — Key Exchange
────────────────────────────────────────────────────────────────
  ✅ PASS  TC03 sendKey correct → unlocked  [0x67 0x02]
  ✅ PASS  TC04 sendKey without seed → NRC 0x24  [NRC=0x24]

────────────────────────────────────────────────────────────────
  GROUP 3: SecurityAccess (0x27) — Brute-Force Protection
────────────────────────────────────────────────────────────────
  ✅ PASS  TC05 Wrong key → NRC 0x35  [NRC=0x35]
  ✅ PASS  TC06 attempt 1/3 → NRC 0x35  [✓]
  ✅ PASS  TC06 attempt 2/3 → NRC 0x35  [✓]
  ✅ PASS  TC06 attempt 3/3 → NRC 0x36 (lockout)  [NRC=0x36]
  ✅ PASS  TC07 During lockout → NRC 0x37  [NRC=0x37]
    ⏰ Waiting 3.5s for lockout to expire...
  ✅ PASS  TC07 Lockout expired — seed available again  [✓]
  ✅ PASS  TC08 Already unlocked → zero seed (0x00000000)  [✓]

────────────────────────────────────────────────────────────────
  GROUP 4: RoutineControl (0x31) — Basic Usage
────────────────────────────────────────────────────────────────
  ✅ PASS  TC09 startRoutine in default → NRC 0x22  [NRC=0x22]
  ✅ PASS  TC10 startRoutine SelfTest  [0x71 0x01]
  ✅ PASS  TC10 RID echoed correctly  [0xFF00]
  ✅ PASS  TC10 Status = RS_RUNNING (0x01)  [✓]
  ✅ PASS  TC11 requestResults → running or completed  [0x01 ✓]
    ⏱  Waiting 300ms for SelfTest (200ms) to complete...
  ✅ PASS  TC12 requestResults → RS_COMPLETED  [result=00]
  ✅ PASS  TC12 SelfTest result = 0x00 (pass)  [✓]

────────────────────────────────────────────────────────────────
  GROUP 5: RoutineControl (0x31) — Error Paths & Advanced
────────────────────────────────────────────────────────────────
  ✅ PASS  TC13 stopRoutine DTCCheck  [0x71 0x02]
  ✅ PASS  TC14 Unknown RID → NRC 0x31  [NRC=0x31]
  ✅ PASS  TC15 Security-gated routine → NRC 0x33  [NRC=0x33]
  ✅ PASS  TC16 SecurityAccess unlocked  [✓]
  ✅ PASS  TC16 startRoutine SensorCal (after unlock)  [0x71 0x01]
    ⏱  Waiting 400ms for SensorCal (300ms) to complete...
  ✅ PASS  TC16 SensorCal completed  [calibration offset=125]

================================================================
  TEST SUMMARY: 20/20 passed, 0 failed
================================================================
```

> ⏱ TC07 takes ~3.5s for the lockout to expire. This is intentional — you're testing a real timing behaviour, not just mocking it away.

### Run It

```bash
cd "Day-14_UDS_Security_Routine"
pip install python-can
python uds_security_routine.py
```

---

## 🔥 Challenge

### Challenge 1 — ⏱️ Measure Lockout Duration Precisely

The spec says lockout must be exactly N seconds. Test it:

```python
def tc_lockout_timing_precision(self) -> None:
    """
    Verify the lockout duration matches the spec within ±100ms tolerance.
    Steps:
      1. Trigger lockout (3 wrong keys)
      2. Record t_lockout_start
      3. Poll requestSeed every 100ms
      4. Record t_first_seed_granted
      5. Assert abs((t_first_seed_granted - t_lockout_start) - SEC_LOCKOUT_SECS) < 0.1
    """
```

### Challenge 2 — 🔄 Routine Completion Timing SLA

The spec says each routine must complete within a defined time:
- SelfTest: ≤ 500ms
- SensorCal: ≤ 1000ms

Verify:

```python
def tc_routine_completion_sla(tester, rid_h, rid_l, max_ms: float) -> None:
    """Start routine, poll results, assert completes within max_ms."""
    t_start = time.monotonic()
    tester._send([SID_ROUTINE, RC_START, rid_h, rid_l])
    tester._recv()
    while True:
        tester._send([SID_ROUTINE, RC_RESULTS, rid_h, rid_l])
        resp = tester._recv()
        # TODO: check elapsed time + RS_COMPLETED
```

### Challenge 3 — 🔁 Session Drop Resets Security

Prove that S3 timeout resets the security lock:

```python
def tc_s3_resets_security(tester, ecu) -> None:
    """
    1. Unlock security in extended session
    2. Do nothing for S3_TIMEOUT_S + 1 second
    3. Try to start security-gated routine
    4. Expect NRC 0x33 (ECU dropped to default → security locked)
    """
```

### Challenge 4 — 🛡️ Add Security Level 2

Extend the simulation with a second security level for programming session access (seed `0x03`, key `0x04`). Protect a new "EraseMemory" routine (RID `0xFF00` in programming session only) behind level 2. Add test cases for the full level 2 exchange and the cross-level protection (level 1 unlock does NOT grant level 2 access).

---

## ❓ Quiz + Answers

**Q1.** A tester sends `[0x27, 0x02, 0xDE, 0xAD, 0xBE, 0xEF]` immediately after connecting. The ECU returns `0x7F 0x27 0x24`. What went wrong and what's the correct sequence?

<details>
<summary>Answer</summary>

NRC `0x24` = **requestSequenceError**. The tester sent `sendKey` (sub-function 0x02, even) without first requesting a seed (sub-function 0x01, odd). There is no pending seed for the ECU to compare against.

Correct sequence:
1. Send `[0x27, 0x01]` (requestSeed)
2. Receive `[0x67, 0x01, S0, S1, S2, S3]` — extract the 4-byte seed
3. Compute `KEY = DERIVE(S0..S3)` using the correct algorithm
4. Send `[0x27, 0x02, K0, K1, K2, K3]` (sendKey)
5. Receive `[0x67, 0x02]` — now unlocked

</details>

---

**Q2.** An ECU returns `0x7F 0x27 0x36`. What does this mean, what caused it, and what should the tester do next?

<details>
<summary>Answer</summary>

NRC `0x36` = **exceededNumberOfAttempts**. The tester has sent the wrong key too many times in sequence (typically 3). The ECU is now in a **timed lockout state**.

What the tester should do:
1. Wait for the lockout timer to expire (duration defined in the ECU spec — commonly 10–60 seconds in production, 3 seconds in our simulation)
2. Any attempt before the timer expires will return NRC `0x37` (requiredTimeDelayNotExpired)
3. After expiry, restart the seed/key sequence from the beginning

Do NOT retry immediately — that wastes time and may extend the lockout in some implementations.

</details>

---

**Q3.** You start a RoutineControl (startRoutine 0x31 0x01) and get `[0x71, 0x01, 0xFF, 0x00, 0x01]`. Then you immediately send requestRoutineResults and get `[0x71, 0x03, 0xFF, 0x00, 0x01]`. What does `0x01` in the last response mean, and what should you do next?

<details>
<summary>Answer</summary>

The final `0x01` is the **routine status byte** = `RS_RUNNING`. The routine is still executing — it hasn't completed yet.

Next steps:
1. Wait a short delay (50–100ms)
2. Send requestRoutineResults again
3. Repeat until you receive `RS_COMPLETED (0x02)` or a timeout occurs
4. On RS_COMPLETED, the bytes after position 4 are the result data
5. Verify the result data matches the expected value per the diagnostic spec

This is the standard **polling pattern** — start, wait, poll, collect results.

</details>

---

**Q4.** A security tester observes that when the wrong key starts with the correct first byte, the ECU takes 0.8ms to respond with NRC 0x35. When all four bytes are wrong, it takes 0.3ms. Is this a bug? Why or why not?

<details>
<summary>Answer</summary>

Yes, this is a **timing side-channel vulnerability**. The ECU's key comparison is short-circuiting — it takes longer to process a partially-correct key because it continues comparing after the first match before failing. This means an attacker can enumerate the correct key byte-by-byte:
- Try all 256 values for byte 0; the one that takes longer is the correct first byte
- Repeat for each subsequent byte

Total attack: 256 × 4 = 1024 attempts vs. brute-force 256^4 = 4 billion attempts.

The fix: the ECU should use **constant-time comparison** (e.g., `hmac.compare_digest()` in Python, `CRYPTO_memcmp()` in C). The comparison must always examine all bytes regardless of where the mismatch occurs.

This is a **real finding** in automotive penetration tests.

</details>

---

**Q5.** Why do odd sub-functions indicate requestSeed and even sub-functions indicate sendKey in SecurityAccess?

<details>
<summary>Answer</summary>

This is an **ISO 14229 convention** that makes parsing straightforward:
- `subFunction & 0x01 == 1` → requestSeed (odd)
- `subFunction & 0x01 == 0` → sendKey (even)

Each pair `(2N-1, 2N)` corresponds to security level N. This means the ECU can determine both the operation type AND the security level from a single sub-function byte by simple bit manipulation, without needing a lookup table.

A tester who receives a requestSeed for level 3 (`0x05`) knows they must respond with sendKey for level 3 (`0x06`). The ECU knows that sub 0x05 awaits sub 0x06. If sub 0x04 arrives instead (sendKey for level 2), it's a sequence error — wrong level's key.

</details>

---

## 📌 Key Takeaways

```
┌──────────────────────────────────────────────────────────────────┐
│  DAY 14 KEY TAKEAWAYS                                            │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  1. SecurityAccess (0x27) is a challenge-response protocol.     │
│     requestSeed (odd sub) → compute key → sendKey (even sub).   │
│     The sub-function pair (2N-1, 2N) identifies security level. │
│                                                                  │
│  2. The key derivation algorithm is secret.                     │
│     Testers use: env vars, compiled .so libs, HSMs — never      │
│     hardcoded constants in source code.                          │
│                                                                  │
│  3. Brute-force protection: NRC 0x35 (wrong key) →             │
│     NRC 0x36 (locked out) → NRC 0x37 (wait). Test all three.   │
│     Verify the lockout count AND the lockout timer duration.    │
│                                                                  │
│  4. requestSeed when already unlocked returns 0x00000000.       │
│     Zero seed = "already in" signal. Tester must detect it.     │
│                                                                  │
│  5. RoutineControl (0x31) is for triggering ECU actions:        │
│     start (0x01) → stop (0x02) → requestResults (0x03).        │
│     Results are async — always poll until RS_COMPLETED.          │
│                                                                  │
│  6. RIDs address procedures; DIDs address data.                 │
│     0x22/0x2E operate on DIDs. 0x31 operates on RIDs.           │
│     Different address spaces, different purposes.                │
│                                                                  │
│  7. Security-gated routines need BOTH the right session AND     │
│     SecurityAccess unlock. Missing either → NRC 0x33.           │
│                                                                  │
│  8. Constant-time key comparison is a testable security         │
│     requirement, not an implementation detail. Timing attacks    │
│     are real and are found in automotive ECU pen-tests.          │
└──────────────────────────────────────────────────────────────────┘
```

---

## ⏭️ Run code 
```
cd "Day-14_UDS_Security_Routine"
pip install python-can
python uds_security_routine.py
```