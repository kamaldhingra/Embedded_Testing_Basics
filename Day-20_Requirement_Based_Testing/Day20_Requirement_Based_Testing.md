# Day 20: Requirement-Based Testing

> **Professor Embed says:** "Here's the question I get from every QA engineer
> crossing into embedded: 'How do I know I've written enough test cases?' In web
> testing you often stop when the sprint ends. In automotive, that answer gets you
> fired — or worse. Today I'm going to give you a systematic answer. We're going to
> learn five test design techniques that turn a requirement into a *provably complete*
> test set. And we're going to pay special attention to MC/DC — the technique that
> ISO 26262 mandates for ASIL-D. Because 'we tested it and it seemed fine' is not
> a sentence that passes a TÜV audit."
>
> **Prerequisites:** Days 1–19 (full CAN/UDS/ASPICE/gate reviews stack)

---

## Quick Recap

| Day  | Topic |
|------|-------|
| 1–11  | CAN bus, DBC, tools |
| 12–16 | UDS services, ISO-TP |
| 17    | ECU flashing |
| 18    | SIL vs HIL |
| 19    | Automotive lifecycle — ASPICE, ISO 26262, gate reviews |
| **20** | **Requirement-based testing — EP, BVA, State, Decision, MC/DC** |

---

## Why Requirement-Based Testing?

In exploratory testing (or "vibe testing" as Professor Embed calls it), you run the
system and see what breaks. This works for early prototypes and bug hunts. But it has
a fatal flaw: **you cannot prove coverage**.

An OEM auditor at ASPICE CL2 asks: "How do you know all the requirements are tested?"
If your answer is "we tested for a week," the audit fails.

**Requirement-based testing** solves this by:
1. Starting from a requirement specification
2. Applying a *systematic technique* to derive test cases
3. Producing traceable evidence that every requirement class, boundary, and condition has been exercised

> 🌉 **From your world:** You already do this — you just don't formalise it.
> When you write Playwright tests for a login form, you test valid credentials,
> invalid credentials, empty fields, too-long strings, and SQL injection.
> That's equivalence partitioning + boundary value analysis, informally applied.
> Today we formalise it.

---

## The Five Techniques

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  TECHNIQUE              QUESTION IT ANSWERS                                │
├─────────────────────────────────────────────────────────────────────────────┤
│  Equivalence            "Are all input types covered?"                     │
│  Partitioning (EP)      One test per class — valid and invalid.            │
├─────────────────────────────────────────────────────────────────────────────┤
│  Boundary Value         "Are the edges tested?"                            │
│  Analysis (BVA)         Below, at, and just above every threshold.         │
├─────────────────────────────────────────────────────────────────────────────┤
│  State Transition       "Are all state machine paths tested?"              │
│  Testing                Every valid transition + at least one blocked one. │
├─────────────────────────────────────────────────────────────────────────────┤
│  Decision Table         "Are all input combinations covered?"              │
│  Testing                One row per unique combination of conditions.      │
├─────────────────────────────────────────────────────────────────────────────┤
│  MC/DC Coverage         "Does every condition independently matter?"       │
│  (ISO 26262 ASIL-D)     Each boolean sub-condition flips the outcome alone.│
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Technique 1: Equivalence Partitioning (EP)

**Core idea:** If all values in a set behave identically, testing one is enough.
Dividing the input space into *equivalence classes* reduces the test count without
losing coverage.

### EP for REQ-RBT-001
> *The ECU's DiagnosticSessionControl (0x10) sub-function byte shall accept values
> 0x01, 0x02, and 0x03 and reject all other values with NRC 0x12.*

```
Input space: 1 byte (0x00–0xFF = 256 values)

Equivalence Classes:
  ┌────────────────────────────────────────────────────────────┐
  │ EP Class     │ Values         │ Expected     │ Test value  │
  ├────────────────────────────────────────────────────────────┤
  │ valid        │ {0x01, 0x02,   │ 0x50 + sub   │   0x02      │
  │              │  0x03}         │ (positive)   │             │
  ├────────────────────────────────────────────────────────────┤
  │ invalid_zero │ {0x00}         │ NRC 0x12     │   0x00      │
  ├────────────────────────────────────────────────────────────┤
  │ invalid_high │ {0x04–0xFF}    │ NRC 0x12     │   0x80      │
  └────────────────────────────────────────────────────────────┘

Without EP: 256 tests
With EP:    3 tests  (one per class)
```

**Why `invalid_zero` is its own class:** Zero is often handled as a special case in
firmware (`if (sub == 0)` is a common guard). Testing 0x00 explicitly catches this.

> 🌉 **From your world:** You do this with HTTP status codes.
> Valid: 200. Error: 400, 404, 500. You don't write 600 separate tests.
> EP is why you don't.

---

## Technique 2: Boundary Value Analysis (BVA)

**Core idea:** Bugs cluster at boundaries. Off-by-one errors (`>` vs `>=`, `<` vs `<=`)
are the most common implementation mistakes in embedded firmware.

### BVA for REQ-RBT-002
> *DTC P0217 (engine coolant temperature too high) shall be set to CONFIRMED status
> when the coolant temperature is **strictly greater than** 105.0 °C.*

```
Threshold: 105.0 °C
Operator:  STRICTLY GREATER THAN (> not >=)

BVA Test Points:
  ┌──────────────────────────────────────────────────────────────────┐
  │ Point         │ Value   │ Expected    │ Why it matters           │
  ├──────────────────────────────────────────────────────────────────┤
  │ just below    │ 104.9°C │ no DTC      │ below threshold          │
  ├──────────────────────────────────────────────────────────────────┤
  │ at threshold  │ 105.0°C │ no DTC      │ strict > means 105.0     │
  │               │         │             │ does NOT trigger DTC!    │
  │               │         │             │ ← catches = vs > bug     │
  ├──────────────────────────────────────────────────────────────────┤
  │ just above    │ 105.1°C │ DTC P0217   │ first value that fires   │
  └──────────────────────────────────────────────────────────────────┘

The critical test: 105.0°C must NOT set the DTC.
A developer who wrote `if (temp >= 105.0)` instead of `if (temp > 105.0)`
passes the 104.9 and 105.1 tests — but FAILS the 105.0 test.
BVA catches the bug. A mid-range test (e.g., 110°C only) does not.
```

**The "averages hide danger" principle — again:**
Testing at 90°C and 120°C only tells you the function works at comfortable values.
The bug lives at 105.0°C on the boundary. Test there.

### BVA for REQ-RBT-006 (SecurityAccess lockout)

```
Threshold: SEC_MAX_ATTEMPTS = 3
Operator:  fail_count >= 3 → lock out

BVA test points:
  2nd wrong key  →  NRC 0x35 (InvalidKey)   ← below lockout boundary
  3rd wrong key  →  NRC 0x36 (ExceededAttempts) ← AT lockout boundary
```

A developer who wrote `if (fail_count > 3)` instead of `>= 3` would allow a 4th wrong
key without locking — the BVA test at attempt 3 catches this.

---

## Technique 3: State Transition Testing

**Core idea:** An ECU is a finite state machine. Model every state and every transition,
then test each transition at least once — plus key invalid transitions.

### The ECU Session State Machine

```
        ┌────────────────────────────────────────────────────────────────┐
        │                                                                │
        │   ╔══════════════════════════╗                                 │
        │   ║    defaultSession        ║ ◄── Initial state             │
        │   ║       (0x01)             ║ ◄── ECUReset (0x11 0x01)      │
        │   ╚══════╤═══════════╤═══════╝ ◄── S3 timeout               │
        │          │           │                                        │
        │   0x10   │           │ 0x10 0x02                             │
        │   0x03   │           │                                        │
        │          ▼           ▼                                        │
        │   ╔══════════╗  ╔═════════════╗                              │
        │   ║extended  ║  ║programming  ║                              │
        │   ║ (0x03)   ║  ║  Session    ║                              │
        │   ╚══════════╝  ║   (0x02)    ║                              │
        │                 ╚═════════════╝                              │
        │                                                                │
        │   Invalid transition (tested in TC12):                        │
        │   0x27 0x01 in defaultSession → NRC 0x22 (BLOCKED)           │
        └────────────────────────────────────────────────────────────────┘
```

**Transitions tested:**

| TC | From | To | Trigger | Expected |
|----|------|----|---------|----------|
| TC09 | DEFAULT | EXTENDED | 0x10 0x03 | DID 0xF186 = 0x03 |
| TC10 | EXTENDED | PROGRAMMING | 0x10 0x02 | DID 0xF186 = 0x02 |
| TC11 | PROGRAMMING | DEFAULT | 0x11 0x01 reset | DID 0xF186 = 0x01 |
| TC12 | DEFAULT | BLOCKED | 0x27 0x01 (invalid) | NRC 0x22 |

**DID 0xF186 — `activeSession`:** This is a standardised UDS DID (ISO 14229-1 §C.1)
that returns the current session mode. Using it to verify state transitions is the
automotive equivalent of reading `document.title` to verify a page navigation.

> 🌉 **From your world:** You already test state machines when you write:
> - "Click Login → verify page = Dashboard"
> - "Click Logout → verify page = Login"
> - "Try to access /admin without auth → verify redirect to /login"
> State transition testing is just the formalised version of this.

---

## Technique 4: Decision Table Testing

**Core idea:** For functions with multiple boolean conditions, enumerate all
*distinct combinations* of conditions that lead to a different output.

### Decision Table for REQ-RBT-004 (Fan Hysteresis)
> *The cooling fan shall activate when coolant temperature exceeds 90°C and
> shall deactivate when temperature falls below 85°C (hysteresis controller).*

```
Conditions:
  C1: temp > FAN_ON_C  (> 90°C)
  C2: temp < FAN_OFF_C (< 85°C)
  C3: fan_was_ON

  ┌────────────────┬───────┬────────┬────────┬────────┐
  │                │  Row1 │  Row2  │  Row3  │  Row4  │
  │ C1: temp>90    │   F   │   T    │   F    │   F    │
  │ C2: temp<85    │   T   │   F    │   F    │   T    │
  │ C3: fan_was_ON │   F   │   F    │   T    │   T    │
  ├────────────────┼───────┼────────┼────────┼────────┤
  │ Fan output     │  OFF  │   ON   │  ON    │  OFF   │
  │ Rule           │normal │ turn   │hyster- │ turn   │
  │                │ off   │  on    │  esis  │  off   │
  ├────────────────┼───────┼────────┼────────┼────────┤
  │ Test case      │ TC13  │ TC14   │ TC15   │ TC16   │
  │ Temp used      │  70°C │  95°C  │  87°C  │  80°C  │
  └────────────────┴───────┴────────┴────────┴────────┘
```

**TC15 is the critical row:** Row 3 represents the hysteresis dead-band (85°C ≤ temp ≤ 90°C,
fan was already on). The fan MUST stay on. A developer who forgot the hysteresis and used
a single threshold would have the fan toggle off at this row — TC15 catches the bug.

> Without hysteresis: at 87°C, the fan would toggle ON/OFF thousands of times per second.
> Decision table testing ensures the fourth row (hysteresis maintenance) is not forgotten.

---

## Technique 5: MC/DC Coverage

**MC/DC (Modified Condition/Decision Coverage)** is the coverage criterion mandated by:
- ISO 26262 — ASIL-D (automotive)
- DO-178C — Level A (aviation)
- IEC 61508 — SIL 4 (industrial)

**The rule:** For every boolean *decision* (compound condition), each individual
*condition* within it must independently affect the decision outcome in at least one test.

### MC/DC for REQ-RBT-005

The DTC set expression in the ECU:
```python
should_set_dtc = (temp > OVER_TEMP_C) AND dtc_enabled
                  ─────────────────     ────────────
                  Condition C1          Condition C2
```

**Minimal MC/DC test set (3 tests for 2 conditions):**

```
  Test  C1 (temp>105)  C2 (dtc_enabled)  Decision  Proof
  ────  ────────────  ────────────────  ────────  ──────────────────────────
  TC17  True          True              True      Reference case (both True)
  TC18  False         True              False     C1 changes T→F; decision T→F
  TC19  True          False             False     C2 changes T→F; decision T→F

  TC17 vs TC18: same C2, C1 differs → C1 independently affects outcome ✓
  TC17 vs TC19: same C1, C2 differs → C2 independently affects outcome ✓
```

**Why MC/DC is harder than branch coverage:**

Branch coverage (80% for typical software) only requires that each branch is taken once.
For the expression `A AND B`, branch coverage is satisfied by:
- `(T, T) → T`  — the True branch
- `(F, F) → F`  — the False branch

But this does NOT prove that `B` independently matters. What if the developer wrote
`A AND True` by accident? Both tests still pass. MC/DC would catch it because there's
no pair showing B causes a change.

```
Branch coverage: 2 tests
MC/DC coverage:  3 tests (for 2 conditions)
                 n+1 tests (for n conditions) — still very manageable
```

> 🌉 **From your world:** MC/DC is what you do when you have an `&&` in a critical
> `if` statement and you want to prove BOTH sides matter. If your Playwright test
> only has a case where both conditions are true and one where both are false,
> you haven't proven that each condition independently contributes.
> MC/DC forces you to isolate each condition.

### A Common MC/DC Misconception

**Question:** Does MC/DC require testing all 4 combinations of 2 conditions?

**Answer:** No. MC/DC requires n+1 tests for n conditions.
For `A AND B`, MC/DC needs 3 tests, not 4 (full combinatorial).

```
Full combinatorial (2 conditions): 4 tests  (overkill for ASIL-D)
MC/DC (2 conditions):              3 tests  (minimum proof of independence)
Branch coverage (2 conditions):    2 tests  (not sufficient for ASIL-D)
```

For 10 conditions: full combinatorial = 1024 tests; MC/DC = 11 tests.
MC/DC is the pragmatic middle ground between "too few" and "combinatorial explosion."

---

## Which Technique for Which Requirement?

```
┌──────────────────────────────────────────────────────────────────────────┐
│  Requirement type          → Best technique                             │
├──────────────────────────────────────────────────────────────────────────┤
│  Function with valid/      → EQUIVALENCE PARTITIONING                  │
│  invalid input sets           "what values are accepted?"               │
├──────────────────────────────────────────────────────────────────────────┤
│  Numeric threshold         → BOUNDARY VALUE ANALYSIS                   │
│  (> < >= <=)                  "where exactly does behaviour change?"    │
├──────────────────────────────────────────────────────────────────────────┤
│  Mode/session/state        → STATE TRANSITION                           │
│  machine                      "which paths are reachable?"              │
├──────────────────────────────────────────────────────────────────────────┤
│  Multiple boolean          → DECISION TABLE                             │
│  conditions → one output      "are all combinations handled?"           │
├──────────────────────────────────────────────────────────────────────────┤
│  Safety-critical (ASIL-D)  → MC/DC COVERAGE                            │
│  compound condition           "does each condition independently count?"│
└──────────────────────────────────────────────────────────────────────────┘
```

**They combine.** A single requirement may need multiple techniques:
- Fan control: Decision Table (4 rows) + BVA (boundary at FAN_ON and FAN_OFF)
- DTC set: BVA (boundary at 105.0°C) + MC/DC (compound condition)
- Session control: EP (valid/invalid sub-function) + State Transition

---

## Test Cases Overview

| TC | Group | Technique | Requirement | What It Proves |
|----|-------|-----------|-------------|----------------|
| TC01 | EP | Equivalence Partitioning | REQ-RBT-001 | Valid session class representative |
| TC02 | EP | Equivalence Partitioning | REQ-RBT-001 | Invalid class (zero) → NRC 0x12 |
| TC03 | EP | Equivalence Partitioning | REQ-RBT-001 | Valid DID class representative |
| TC04 | EP | Equivalence Partitioning | REQ-RBT-001 | Invalid DID class → NRC 0x31 |
| TC05 | BVA | Boundary Value Analysis | REQ-RBT-002 | 104.9°C → no DTC (just below) |
| TC06 | BVA | Boundary Value Analysis | REQ-RBT-002 | 105.0°C → no DTC (strict >) |
| TC07 | BVA | Boundary Value Analysis | REQ-RBT-002 | 105.1°C → DTC confirmed |
| TC08 | BVA | Boundary Value Analysis | REQ-RBT-006 | 3rd wrong key = lockout boundary |
| TC09 | State | State Transition | REQ-RBT-003 | DEFAULT → EXTENDED |
| TC10 | State | State Transition | REQ-RBT-003 | EXTENDED → PROGRAMMING |
| TC11 | State | State Transition | REQ-RBT-003 | Any → DEFAULT via ECUReset |
| TC12 | State | State Transition | REQ-RBT-003 | DEFAULT → SecurityAccess = BLOCKED |
| TC13 | DT | Decision Table | REQ-RBT-004 | (temp<90, fan=OFF) → fan stays OFF |
| TC14 | DT | Decision Table | REQ-RBT-004 | (temp>90, fan=OFF) → fan turns ON |
| TC15 | DT | Decision Table | REQ-RBT-004 | (87°C, fan=ON) → fan STAYS ON (hysteresis) |
| TC16 | DT | Decision Table | REQ-RBT-004 | (temp<85, fan=ON) → fan turns OFF |
| TC17 | MC/DC | MC/DC Coverage | REQ-RBT-005 | (T, T) → True: reference case |
| TC18 | MC/DC | MC/DC Coverage | REQ-RBT-005 | (F, T) → False: temp is deciding |
| TC19 | MC/DC | MC/DC Coverage | REQ-RBT-005 | (T, F) → False: dtc_enabled deciding |
| TC20 | Report | All | All | Coverage report — all techniques 100% |

---

## The `dtc_enabled` Flag: What It Represents

In the MC/DC tests (TC17–TC19), the ECU exposes a `dtc_enabled` boolean that acts as
the second condition in the compound expression:

```
should_set_dtc = (temp > 105.0) AND dtc_enabled
```

In production firmware this would typically be:
- A configuration DID: `0x2E 0xF4 0x01` (WriteDataByIdentifier)
- A variant code bit: "DTC logging enabled for this vehicle variant"
- An NVM-stored flag: set by end-of-line programming

In SIL, we set it directly: `ecu.dtc_enabled = False`. This is the SIL advantage —
instant controllability of any firmware flag without having to send a full UDS sequence.

The key insight: **MC/DC requires that each condition be independently observable.**
If `dtc_enabled` is buried inside an interrupt handler with no external visibility,
you cannot write MC/DC tests for it. ASPICE SWE.4 requires that ASIL-D components
be designed with testability in mind — meaning every condition in a safety expression
must be controllable and observable from the test interface.

---

## DID 0xF186 — The Standard Session Query DID

In TC09, TC10, TC11 (state transition tests), we use DID 0xF186 to verify the
ECU's current session after each transition:

```
ISO 14229-1, Annex C — Standardised DIDs:
  0xF186:  activeSessionDataIdentifier
  Length:  1 byte
  Value:   0x01 = defaultSession
           0x02 = programmingSession
           0x03 = extendedDiagSession
```

This DID is required to return the current session in any session without security
access. It's the automotive equivalent of `/api/whoami` — always available, always
tells you what state you're in. If an ECU doesn't implement 0xF186, it's non-compliant
with ISO 14229-1.

---

## Expected Output (All 20/20 Pass)

```
📋🔬  📋🔬  📋🔬  📋🔬  📋🔬  ...

──────────────────────────────────────────────────────────────────
  GROUP 1: Equivalence Partitioning  [ASIL-B]
──────────────────────────────────────────────────────────────────
  ✅ PASS  TC01  EP valid class: 0x10 0x02 → 0x50 0x02 ✓
  ✅ PASS  TC02  EP invalid class: 0x10 0x00 → NRC 0x12 ✓
  ✅ PASS  TC03  EP valid DID: 0x22 0xF189 → 0x62 ✓
  ✅ PASS  TC04  EP invalid DID: 0x22 0xABCD → NRC 0x31 ✓

──────────────────────────────────────────────────────────────────
  GROUP 2: Boundary Value Analysis  [ASIL-C]
──────────────────────────────────────────────────────────────────
  ✅ PASS  TC05  BVA 104.9°C: no DTC ✓  (below strict > 105.0)
  ✅ PASS  TC06  BVA 105.0°C: no DTC ✓  (105.0 is NOT strictly > 105.0)
  ✅ PASS  TC07  BVA 105.1°C: P0217 confirmed ✓
  ✅ PASS  TC08  BVA SA lockout: 2nd→NRC 0x35 (below) / 3rd→NRC 0x36 (at boundary) ✓

──────────────────────────────────────────────────────────────────
  GROUP 3: State Transition Testing  [ASIL-B]
──────────────────────────────────────────────────────────────────
  ✅ PASS  TC09  State DEFAULT(0x01) → EXTENDED(0x03) ✓  (DID 0xF186 confirmed)
  ✅ PASS  TC10  State EXTENDED(0x03) → PROGRAMMING(0x02) ✓
  ✅ PASS  TC11  State PROGRAMMING(2) → DEFAULT(0x01) via ECUReset ✓
  ✅ PASS  TC12  Invalid transition: SecurityAccess in DEFAULT → NRC 0x22 ✓

──────────────────────────────────────────────────────────────────
  GROUP 4: Decision Table Testing  [ASIL-B]
──────────────────────────────────────────────────────────────────
  ✅ PASS  TC13  DT Row 1: (temp=70, fan_was=OFF) → fan=OFF ✓
  ✅ PASS  TC14  DT Row 2: (temp=95, fan_was=OFF) → fan=ON ✓
  ✅ PASS  TC15  DT Row 3: (temp=87, fan_was=ON) → fan STAYS ON ✓ (hysteresis)
  ✅ PASS  TC16  DT Row 4: (temp=80, fan_was=ON) → fan=OFF ✓

──────────────────────────────────────────────────────────────────
  GROUP 5: MC/DC Coverage  [ASIL-D — ISO 26262 mandate]
──────────────────────────────────────────────────────────────────
  ✅ PASS  TC17  MC/DC (T,T)→True:  temp=110>105 AND dtc_en=True → DTC ✓
  ✅ PASS  TC18  MC/DC (F,T)→False: temp condition is deciding factor ✓
  ✅ PASS  TC19  MC/DC (T,F)→False: dtc_enabled is deciding factor ✓

[Coverage report printed — all 5 techniques at 100%]

  ✅ PASS  TC20  All test design techniques applied; coverage report generated ✓

================================================================
  TEST SUMMARY: 20/20 TCs pass  |  0 fail
================================================================
```

---

## Software QA Bridge

| Automotive Concept | Your World Equivalent |
|-------------------|-----------------------|
| **Equivalence Partitioning** | Testing a form with valid input, empty input, invalid format, too-long string — one representative per class |
| **Boundary Value Analysis** | Testing password length at 7 chars (fail), 8 chars (pass), 9 chars (pass) — boundary between rejection and acceptance |
| **State Transition Testing** | Testing a multi-step checkout: cart → shipping → payment → confirmation — every page transition + trying to skip to payment directly |
| **Decision Table** | Testing `if (isLoggedIn && isAdmin)` — all 4 combinations: (F,F), (T,F), (F,T), (T,T) |
| **MC/DC Coverage** | Ensuring `isLoggedIn` and `isAdmin` EACH independently cause test failure when false — not just testing both-false vs both-true |
| **DID 0xF186 (activeSession)** | `/api/session` endpoint — returns current auth state for assertion |
| **ASIL-D requires MC/DC** | SOC 2 / PCI-DSS requires certain test depths for critical paths (payment, auth, data export) |
| **Strict `>` vs `>=`** | Off-by-one in pagination: `if (page >= totalPages)` vs `> totalPages` — one is a logic bug |
| **Hysteresis dead-band** | Debounce interval — don't re-fire an event within N ms of the last one |
| **`dtc_enabled` flag** | Feature flag / kill switch — test that the system is actually OFF when the flag is OFF |

---

## Quiz

**Q1.** You have this UDS requirement:
> "The ECU shall respond positively to ReadDataByIdentifier (0x22) for any DID in the
> range 0xF000–0xF0FF. It shall respond NRC 0x31 for any DID outside this range."

How many equivalence classes exist, and what is the minimal EP test set?

<details><summary>Answer</summary>

**3 equivalence classes:**
1. **Valid range:** 0xF000–0xF0FF (256 values) — representative: any one, e.g., 0xF050
2. **Below range:** 0x0000–0xEFFF — representative: e.g., 0x1234
3. **Above range:** 0xF100–0xFFFF — representative: e.g., 0xFF00

**Minimal EP test set:** 3 tests (one per class).

Note: if the requirement specifies that SOME sub-ranges within the valid range have
different behaviour (e.g., 0xF000–0xF07F are read-only, 0xF080–0xF0FF are read/write),
then you would need additional partitions within the valid range.

Also note: 0xF000 and 0xF0FF are boundary values and should be additionally tested
with BVA alongside the EP partitioning.

</details>

---

**Q2.** A developer implements the DTC threshold check as:
```c
if (temp_celsius >= 105) {
    set_dtc(P0217);
}
```
The requirement says STRICTLY GREATER THAN 105°C. Which BVA test point catches this bug?

<details><summary>Answer</summary>

**TC06: temp = 105.0°C → expected: no DTC.**

The developer's code uses `>=` (greater than or equal to), so at exactly 105.0°C,
`set_dtc()` IS called — the DTC is set. But the requirement says STRICTLY GREATER
THAN, meaning 105.0°C should NOT set the DTC.

TC06 sends TesterPresent with `ecu.test_temperature = 105.0`, then reads DTCs.
The DTC is found → TC06 FAILS → defect opened for "off-by-one comparison operator."

TC05 (104.9°C) would also fail with `>=` code, but TC05 might not uniquely identify the
operator bug because 104.9 is genuinely not a fault value for any reasonable threshold.
TC06 at exactly 105.0°C is the diagnostic test that pinpoints the `>` vs `>=` error.

</details>

---

**Q3.** For the expression `A AND B AND C` (3 conditions), what is the minimum
number of tests required for MC/DC coverage? Write the test matrix.

<details><summary>Answer</summary>

**Minimum: 4 tests** (n+1 for n=3 conditions).

```
Test   C1(A)  C2(B)  C3(C)  Decision  MC/DC Pair
T1     T      T      T      T         Reference
T2     F      T      T      F         T1 vs T2: A changes T→F → A independently decides
T3     T      F      T      F         T1 vs T3: B changes T→F → B independently decides
T4     T      T      F      F         T1 vs T4: C changes T→F → C independently decides
```

**Proof of independence:**
- A: T1 vs T2: B=T, C=T same; A changes T→F; decision changes T→F ✓
- B: T1 vs T3: A=T, C=T same; B changes T→F; decision changes T→F ✓
- C: T1 vs T4: A=T, B=T same; C changes T→F; decision changes T→F ✓

Full combinatorial for 3 conditions = 8 tests.
MC/DC = 4 tests.
Branch coverage = 2 tests (insufficient for ASIL-D).

Note: this specific structure (one True reference + flip each condition) works for
AND expressions. OR expressions require a different reference (the False case).

</details>

---

**Q4.** You run TC15 (hysteresis test: temp=87°C, fan was ON → fan should stay ON).
The test FAILS — the fan turns OFF. Name two possible root causes and how you would
confirm each.

<details><summary>Answer</summary>

1. **Missing hysteresis: single-threshold implementation.**
   Developer wrote `if (temp < 90) fan_off()` instead of `if (temp < 85) fan_off()`.
   At 87°C (< 90), the single-threshold code turns the fan off.
   Confirm: check firmware source — `FAN_OFF_C` constant is set to 90 instead of 85.

2. **Fan state not preserved between control loop calls.**
   The `_fan_on` flag is not a persistent state — it's recomputed from temperature alone
   each loop iteration. Without storing the previous fan state, the logic always
   evaluates as "is temp > 90?" and returns False at 87°C.
   Confirm: set a breakpoint in `_update_control()` and print `_fan_on` before and after
   the function — it resets to False regardless of input state.

**Both are caught by TC15 (decision table Row 3) but NOT by TC13 or TC14** because
TC13 starts with fan=OFF at 70°C (correct for both bugs) and TC14 starts with fan=OFF
at 95°C (also correct for both bugs). Only TC15's initial state `fan_was=ON` at 87°C
exercises the state persistence and the lower threshold.

</details>

---

**Q5.** An ASPICE assessor auditing at SWE.4 asks: "Show me your MC/DC evidence for
the safety-critical DTC condition." What documentation do you need to provide?

<details><summary>Answer</summary>

Under ASPICE SWE.4 and ISO 26262 Part 6, clause 9.4.3 (unit testing), MC/DC evidence must include:

1. **The requirement being covered** (REQ-RBT-005 with ASIL-D label).
2. **The source-code expression** showing the compound boolean condition (with operator).
3. **The MC/DC test case matrix** — a table listing each test case, the truth value of
   each condition, the decision outcome, and the independent-effect pairing.
4. **The test execution records** — automated test log (timestamp, pass/fail, actual
   decision value observed).
5. **Tool qualification evidence** (if using a coverage measurement tool like BullseyeCoverage,
   Cantata, or VectorCAST) demonstrating the tool correctly identifies MC/DC coverage.
6. **Code instrumentation evidence** — if coverage is measured by instrument (added probes),
   proof that instrumentation does not change runtime behaviour.

The minimum acceptable artifact is items 1–4 in a Test Case Specification document
(ASPICE SWTS artefact) with the execution log attached as the Test Report.

Without items 3 and 4, the assessor has no proof that MC/DC was actually achieved —
"we think we covered it" is not evidence.

</details>

---

## Key Takeaways

1. **EP divides infinite input into finite classes.** You can never test all 256 values
   of a sub-function byte. EP lets you test 3 (one per class) and prove the requirement
   is covered. One test per class — valid and invalid.

2. **BVA is where implementation bugs actually hide.** Off-by-one errors at thresholds
   (`>` vs `>=`) are caught by boundary tests, not by mid-range tests. Always add a test
   at the exact threshold value.

3. **State transition testing proves path completeness.** Model every state and every
   arrow. Test every valid transition. Test at least one invalid (blocked) transition.
   Use DID 0xF186 to read current session — that is your state verification oracle.

4. **Decision tables make complex control logic auditable.** For the fan hysteresis
   controller, the table has 4 rows. Every row is a test case. If you can show 4 ✅,
   you've proven the logic is complete. TC15 (the hysteresis row) is the hardest to
   think of — decision tables force you to think of it systematically.

5. **MC/DC is not as hard as it sounds.** For n boolean conditions: n+1 tests.
   For 2 conditions: 3 tests. You need a reference case and one case per condition
   where that condition alone flips the outcome. That's it.

6. **`>` vs `>=` kills people in automotive.** The BVA test at exactly 105.0°C is the
   audit-ready proof that the operator was implemented correctly. Without it, you have
   not tested the requirement — you've only tested some of the requirement.

7. **Testability is a design constraint, not an afterthought.** MC/DC requires that
   every condition in a safety expression be independently controllable and observable.
   If `dtc_enabled` is buried in a ROM constant, you can't test C2. ASPICE SWE.4 requires
   ASIL-D components to be designed with this in mind. Design for testability.

---

## What's Next? (Day 21 Options)

| Option | Topic |
|--------|-------|
| **21A** | **DoIP (Diagnostics over IP)** — UDS over Ethernet + TCP/IP, ISO 13400-2 |
| **21B** | **OBD-II / Emission Testing** — PIDs, Mode 01–09, IUMPR readiness monitors |
| **21C** | **Automotive Cybersecurity** — UDS attack surface, AUTOSAR SecOC, fuzz testing |
| **21D** | **CAN FD Deep Dive** — BRS bit, ESI bit, 64-byte frames, ISO 11898-1:2015 |
| **21E** | **Masterclass Interview Prep: Days 1–20** — 20 interview rounds, all topics |

---

## Running the Simulation

```bash
cd "Day-20_Requirement_Based_Testing"
pip install python-can
python req_based_testing.py
```

**What to watch:**

- **TC06:** `BVA 105.0°C: no DTC` — this is the boundary test that catches `>` vs `>=` bugs.
  The ECU correctly uses strict `>`, so no DTC is set at exactly 105.0°C.
- **TC08:** Watch 2nd attempt → NRC 0x35, then 3rd attempt → NRC 0x36.
  The output shows BOTH boundary points in one TC.
- **TC09–TC11:** DID 0xF186 confirms the session change. You can see `0x01 → 0x03 → 0x02 → 0x01`.
- **TC15:** `fan STAYS ON` at 87°C — the hysteresis row. This is the row most developers forget.
- **TC17–TC19:** Three lines that prove MC/DC. Two conditions. One reference. Two variations.
- **Coverage report (TC20):** All 5 techniques at 100%. Note that the BVA section shows
  5 boundary points (3 for DTC threshold + 2 for SA lockout) traced to separate requirement IDs.

> **Runtime:** approximately 3–5 seconds

---

*Generated from a live mentoring session with Professor Embed. 🚗⚡🔥*
