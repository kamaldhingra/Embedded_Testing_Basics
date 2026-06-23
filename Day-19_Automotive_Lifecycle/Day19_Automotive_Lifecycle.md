# Day 19: Automotive Testing Lifecycle — ASPICE, ISO 26262, Gate Reviews

> **Professor Embed says:** "You've tested CAN frames, decoded DBCs, sent UDS requests,
> flashed firmware, and learned the difference between SIL and HIL. But here's the
> question your interviewer will ask next: *where does all of that testing fit inside
> the bigger automotive development process?* Today we zoom out. We name the framework.
> We show you the gate that your test results must pass through before a car leaves the
> factory. And we give your test suite a formal identity — complete with ASIL tags,
> requirement IDs, and a defect lifecycle that would survive a TÜV audit."
>
> **Prerequisites:** Days 1–18 (full CAN + UDS + ISO-TP + SIL/HIL stack)

---

## Quick Recap: The Full Journey

| Day  | Topic |
|------|-------|
| 1–11  | CAN bus, DBC, tools, arbitration |
| 12–16 | UDS services, ISO-TP transport |
| 17    | ECU flashing pipeline |
| 18    | SIL vs HIL — naming what we built |
| **19** | **Automotive testing lifecycle — ASPICE, ISO 26262, gates, defects** |

---

## The Two Frameworks Every Automotive Tester Must Know

### ASPICE — Automotive SPICE
*(Software Process Improvement and Capability dEtermination)*

ASPICE is the **process maturity model** used by every OEM in Europe (Volkswagen Group,
BMW, Daimler, Stellantis) and increasingly in Asia. It defines:
- What activities must happen in software development (requirements, architecture, coding, testing)
- What evidence must exist (test plans, test specs, test reports, traceability)
- How to measure process maturity on a 0–5 capability scale (CL0 → CL5)

> 🌉 **From your world:** ASPICE is the embedded world's equivalent of ISO/IEC 29119
> (software testing standard) combined with CMMI. If you've worked at a regulated
> environment (financial services, medical devices) you've felt this — process gates,
> test evidence artifacts, and traceability matrices. ASPICE is the automotive flavour.

**The SWE (Software Engineering) process chain:**

```
SWE.1  Software Requirements Analysis       → REQ-SW-xxxx
SWE.2  Software Architectural Design        → System decomposition
SWE.3  Software Detailed Design & Unit Const→ Unit implementation
SWE.4  Software Unit Verification           → Unit tests (TC01–TC04)
SWE.5  Software Integration & Integration Test→ SIL Integration (TC05–TC08)
SWE.6  Software Qualification Test          → SIL System (TC09–TC12)
```

Each SWE level maps to a test phase in today's simulation.

### ISO 26262 — Automotive Functional Safety
*(Road vehicles — Functional safety)*

ISO 26262 mandates that **every safety-related software function** must be classified
by its potential harm:

```
┌──────────────────────────────────────────────────────────────────────┐
│  ASIL — Automotive Safety Integrity Level                           │
├────────────────────────────────────────────────────────────────────┤
│  ASIL QM  │ Quality Management — no specific safety requirements    │
│  ASIL A   │ Lowest safety — negligible harm if it fails             │
│  ASIL B   │ Minor injury possible                                   │
│  ASIL C   │ Serious injury possible                                 │
│  ASIL D   │ HIGHEST — life-threatening if the function fails        │
└──────────────────────────────────────────────────────────────────────┘
```

**Examples from today's ECU:**

| Function | ASIL | Why? |
|----------|------|------|
| ISO-TP frame encoding | QM | Protocol encoding — no direct harm |
| CRC-32 computation | ASIL-A | Data integrity — low risk |
| Session control P2 timing | ASIL-B | Missed timing could delay diagnostics |
| SecurityAccess lockout | ASIL-C | Security bypass could enable unsafe commands |
| ECU flash pipeline | ASIL-D | Corrupted firmware = loss of vehicle control |
| S3 session timeout | ASIL-D | Session not dropping = diagnostic exploit possible |

> ⚠️ **The implication:** ASIL-D tests are not optional. They are blocking gates.
> A project cannot ship to production with any ASIL-D test case failing or not run.
> This is a legal and contractual obligation, not just good engineering practice.

---

## The V-Model With Test Phases

```
                    Requirements (ASPICE SWE.1)
                   ╱                            ╲
          System Design                    Vehicle Testing
         ╱                                              ╲
    SW Architecture                               HIL Validation ← ASIL-D
   ╱                                                              ╲
 Detailed Design                                          SIL System ← ASIL-C
╱                                                                      ╲
Unit Design ──────────────────────────────────────────── SIL Integration ← ASIL-B
                                                                          │
                                                          Unit Tests ← QM/ASIL-A

      Development side                    Verification/Validation side
```

| Phase | ASPICE Activity | ASIL Coverage | What we simulate |
|-------|----------------|---------------|------------------|
| Unit Test | SWE.4 | QM, ASIL-A | TC01–TC04 (pure Python, no bus) |
| SIL Integration | SWE.5 | ASIL-B | TC05–TC08 (basic UDS services) |
| SIL System | SWE.6 | ASIL-C | TC09–TC12 (security, DTCs) |
| HIL Validation | SYS.5 | ASIL-D | TC13–TC16 (flash, reset, S3 timeout) |
| Production | — | Gate review | TC17–TC20 (process gates, report) |

---

## Gate Reviews

A **gate review** is a formal checkpoint that must pass before the project advances
to the next phase. It is not a test — it is an *evidence review*. The gate reviewer
(usually the system architect + safety manager + ASPICE assessor) checks:

```
┌─────────────────────────────────────────────────────────────────────┐
│  GATE CRITERIA SUMMARY                                             │
├──────────────┬──────────────────────────────────────────────────────┤
│  Gate 1      │ All Unit-phase TCs executed.                        │
│  Unit → SIL  │ 0 CRITICAL defects open.                           │
│  Integration │                                                     │
├──────────────┼──────────────────────────────────────────────────────┤
│  Gate 2      │ All ASIL-B tests PASS (no ASIL-B+ failures).       │
│  SIL Int →   │ 0 CRITICAL, 0 MAJOR defects open.                  │
│  SIL System  │ (MAJOR = must fix; can get waiver only for QM tests)│
├──────────────┼──────────────────────────────────────────────────────┤
│  Gate 3      │ All ASIL-C tests PASS.                              │
│  SIL System  │ 0 CRITICAL, 0 MAJOR defects open.                  │
│  → HIL       │                                                     │
├──────────────┼──────────────────────────────────────────────────────┤
│  Gate 4      │ All ASIL-D tests PASS.                              │
│  HIL →       │ 0 open defects at ANY severity.                     │
│  Production  │ (even MINOR defects must be closed for SOP)         │
└──────────────┴──────────────────────────────────────────────────────┘
```

**SOP** = Start of Production — the automotive equivalent of "the code ships."

> 🌉 **From your world:** Gate reviews are the automotive equivalent of
> your sprint **Definition of Done** — but with legal consequences if you skip them.
> In regulated industries (aviation DO-178C, medical IEC 62304) you have identical
> checkpoints. ASPICE gate reviews generate evidence that can be presented to an OEM
> auditor or a TÜV inspector.

---

## The Defect Lifecycle

```
DEVELOPER                    TESTER                       PROJECT MANAGER

                              Executes TC06
                              ECU returns truncated
                              session response
                                    │
                              ❌ TC06 FAIL
                                    │
                              🐛 Opens D001
                              MAJOR / ASIL-B
                                    │
                              Gate 2 attempted
                              ⛔ BLOCKED (D001 MAJOR open)
                                    │
              ◄─────────────── Defect assigned to dev
  Analyses root cause
  Fixes ECU firmware
  (P2 timing bytes now included)
  Marks D001 as FIXED ─────────────►
                                    │
                              TC06 RE-TEST
                              ✅ Response length = 6
                              ✔️  D001 VERIFIED
                                    │
                         PROJECT MANAGER ◄────
                         🔒 D001 CLOSED
                                    │
                              Gate 2 re-attempted
                              ✅ CLEARED
```

| Status | Meaning | Who sets it |
|--------|---------|-------------|
| **OPEN** | Defect found, root cause not yet identified | Tester (on test failure) |
| **FIXED** | Developer claims root cause addressed | Developer |
| **VERIFIED** | Tester re-ran the failing TC; now passes | Tester |
| **CLOSED** | Project manager confirms closure; gate unblocked | PM / Team Lead |

---

## Requirement Traceability

Every test case must trace to exactly one requirement. No orphan tests. No untested requirements.

```
┌─────────────────────────────────────────────────────────────────────────┐
│  TRACEABILITY CHAIN                                                    │
│                                                                        │
│  Vehicle-level hazard analysis (ISO 26262 HARA)                       │
│         │                                                              │
│         ▼ ASIL assigned to system function                            │
│  System safety goal  (e.g., "ECU shall detect over-temperature")       │
│         │                                                              │
│         ▼ derived to software level                                    │
│  Software requirement  REQ-SW-0032                                    │
│  "DTC P0217 shall confirm when coolant temp > 105 °C  [ASIL-C]"       │
│         │                                                              │
│         ▼ implemented and tested                                      │
│  Test Case  TC11                                                       │
│  "DTC P0217 confirmed at 110 °C"                                      │
│         │                                                              │
│         ▼ evidence                                                     │
│  Test result: ✅ PASS  status=0xAF  [2026-06-22]                      │
└─────────────────────────────────────────────────────────────────────────┘
```

This chain is what the OEM auditor checks. If any link is broken:
- Requirement has no test → **coverage gap** (must add a TC or formally accept the risk)
- Test has no requirement → **orphan test** (what does it prove? remove or link it)
- Test fails → **open defect** blocks gate

---

## The Defect D001 — What Happened in Today's Simulation

**What the tester observed:**
```
TC06: Send 0x10 0x03 (switch to extended session)
ECU response (defect_mode=True): [0x50, 0x03]
                                  ──── ────
                                  SID  sub   ← only 2 bytes!

Expected per REQ-SW-0021: [0x50, 0x03, 0x00, 0x19, 0x01, 0xF4]
                                         ─────────────────────
                                         P2 = 25 ms   P2* = 5000 ms
```

**Why P2 timing matters (ASIL-B):**

The tester uses P2/P2* to configure its own response timeout:
- P2 (byte 3–4): standard response time — tester waits this long before retrying
- P2* (byte 5–6): extended response time — used after NRC 0x78 (RCRRP)

Without P2 timing, a poorly coded tester may retry too early — sending a new request
while the ECU is still processing the previous one. In a flash sequence (ASIL-D), this
can corrupt firmware.

**The fix:** ECU firmware patch v1.1 — always include P2/P2* in session response.

---

## S3 Timeout: The ASIL-D Safety Test (TC16)

S3 is the "server side" session timeout — the ECU drops back to `defaultSession`
if no diagnostic message arrives within the S3 window (typically 5 seconds).

**Why it's ASIL-D:**
If the S3 watchdog fails to fire, an ECU in `programmingSession` or `extendedSession`
could remain unlocked indefinitely after the tester disconnects. This means:
- Programming session stays open → flash memory remains write-enabled
- Security unlocked → any CAN node on the bus could inject malicious UDS requests

**How TC16 tests it:**
```
1. Enter extended session  ← now in non-default session
2. Silence for 2.0 seconds ← do NOT send TesterPresent
3. Send 0x27 0x01 (requestSeed)
4. Expect NRC 0x22 (conditionsNotCorrect) ← session has dropped to default
   → SecurityAccess denied because we are back in default session
```

If NRC 0x22 is received → S3 watchdog fired → ASIL-D test PASSES.

If a positive response is received → S3 watchdog broken → ASIL-D FAIL → **Gate 4 blocked**.

> 🌉 **From your world:** S3 timeout is a server-side **idle session expiry** — exactly
> like your web app's JWT expiry or database connection timeout. If you don't
> keepalive, the server drops your session. The test is: deliberately go idle longer
> than the timeout, then try an authenticated action — expect HTTP 401, not 200.

---

## Test Cases Overview

| TC | ASIL | Phase | What It Tests | Pass Criteria |
|----|------|-------|---------------|---------------|
| TC01 | QM | Unit | ISO-TP SF PCI byte = payload length | `SF[0] == 0x02` |
| TC02 | QM | Unit | ISO-TP FF 12-bit length field | `total_length == 20` |
| TC03 | ASIL-A | Unit | DID °C×10 round-trip, 4 temperatures | All ±0.05 °C |
| TC04 | ASIL-A | Unit | CRC-32 deterministic | Two calls equal |
| TC05 | ASIL-B | SIL Int | Default session → 0x50 0x01 | Positive SID |
| TC06 | ASIL-B | SIL Int | Extended session P2 timing bytes | `len(resp) ≥ 6` |
| TC07 | ASIL-B | SIL Int | ReadDID 0xF189 returns ASCII | SW version string |
| TC08 | ASIL-B | SIL Int | ReadDID unknown → NRC 0x31 | NRC = 0x31 |
| TC09 | ASIL-C | SIL Sys | SecurityAccess seed/key | 0x67 0x02 |
| TC10 | ASIL-C | SIL Sys | SecurityAccess lockout after 3 fails | NRC 0x36 |
| TC11 | ASIL-C | SIL Sys | DTC P0217 at 110 °C | status = 0xAF |
| TC12 | ASIL-C | SIL Sys | ClearDTC removes P0217 | Empty DTC list |
| TC13 | ASIL-D | HIL Val | Programming session entry | 0x50 0x02 |
| TC14 | ASIL-D | HIL Val | CommCtrl disable/re-enable | 0x68 0x03 / 0x68 0x00 |
| TC15 | ASIL-D | HIL Val | ECUReset → ECU reboots | ReadDID works after reset |
| TC16 | ASIL-D | HIL Val | S3 timeout → NRC 0x22 | After 1.5 s silence |
| TC17 | QM | Gate | Gate 1: Unit complete | No blockers |
| TC18 | QM | Gate | Gate 2 + D001 lifecycle | D001 CLOSED, Gate 2 clear |
| TC19 | QM | Gate | Gates 3 + 4 | Both cleared |
| TC20 | QM | Gate | Final report + SOP readiness | 0 fails, 0 open defects |

---

## Expected Output (All 20/20 Pass)

```
🏭📋  🏭📋  🏭📋  🏭📋  🏭📋  ...

  Registered: 20 requirements, 20 test cases
  ECU defect_mode = True  (TC06 will fail until D001 is fixed)

────────────────────────────────────────────────────────────────
  GROUP 1: Unit Tests  [QM / ASIL-A]  — no CAN bus needed
────────────────────────────────────────────────────────────────
  ✅ PASS  TC01 [ASIL-QM] ISO-TP SF: PCI byte encodes payload length  (SF[0]=0x02 ✓)
  ✅ PASS  TC02 [ASIL-QM] ISO-TP FF: total length in 12-bit field
  ✅ PASS  TC03 [ASIL-A]  DID temp encoding: °C × 10 round-trip
  ✅ PASS  TC04 [ASIL-A]  CRC-32: deterministic output on same input

────────────────────────────────────────────────────────────────
  GROUP 2: SIL Integration Tests  [ASIL-B]
────────────────────────────────────────────────────────────────
  ✅ PASS  TC05 [ASIL-B] Default session: 0x10 0x01 → 0x50 0x01
  ❌ FAIL  TC06 [ASIL-B] Extended session: response includes P2 timing
                          (response length=2 — P2/P2* timing bytes missing)
  🐛 DEFECT OPENED  D001  MAJOR/ASIL-B:  Extended session response missing P2/P2* timing bytes
  ✅ PASS  TC07 [ASIL-B] ReadDID 0xF189: returns ASCII SW version
  ✅ PASS  TC08 [ASIL-B] ReadDID unknown DID → NRC 0x31

────────────────────────────────────────────────────────────────
  GROUP 3: SIL System Tests  [ASIL-C]
────────────────────────────────────────────────────────────────
  ✅ PASS  TC09 [ASIL-C] SecurityAccess: correct seed/key unlocks ECU
  ✅ PASS  TC10 [ASIL-C] SecurityAccess: 3 wrong keys → NRC 0x36 lockout
  ✅ PASS  TC11 [ASIL-C] DTC P0217 confirmed at 110 °C
  ✅ PASS  TC12 [ASIL-C] ClearDTC removes P0217

────────────────────────────────────────────────────────────────
  GROUP 4: HIL Validation Tests  [ASIL-D]
────────────────────────────────────────────────────────────────
  ✅ PASS  TC13 [ASIL-D] Programming session entry: 0x10 0x02 → 0x50 0x02
  ✅ PASS  TC14 [ASIL-D] CommunicationControl: disable then re-enable tx
  ✅ PASS  TC15 [ASIL-D] ECUReset hardReset → ECU returns to defaultSession
  ✅ PASS  TC16 [ASIL-D] S3 timeout: session drops after 1.5 s silence

────────────────────────────────────────────────────────────────
  GROUP 5: Gate Reviews & Campaign Closure  [QM — Process]
────────────────────────────────────────────────────────────────
  ✅ PASS  TC17 [ASIL-QM] Gate 1 (Unit → SIL Integration) CLEARED ✓
  ⛔  Gate 2 BLOCKED  —  blockers:
        • ASIL-B+ failure(s): ['TC06']
        • 1 MAJOR defect(s) open — must fix before advancing

  [FIX] Applying ECU patch: sw_session_response_v1.1.c
  🔧 DEFECT FIXED   D001:  ECU firmware corrected: session response includes P2/P2* bytes
  ↻ RE-TEST PASS  TC06 [ASIL-B] Extended session: response includes P2 timing
  ✔️  DEFECT VERIFIED D001
  🔒 DEFECT CLOSED  D001
  ✅ PASS  TC18 [ASIL-QM] Gate 2 (SIL Integration → SIL System) CLEARED ✓
  ✅ Gate 3 (SIL System → HIL Validation) CLEARED
  ✅ Gate 4 (HIL Validation → Production Readiness) CLEARED
  ✅ PASS  TC19 [ASIL-QM] Gates 3 + 4: SIL System and HIL complete ✓

  [CAMPAIGN REPORT printed]

  🏁 PRODUCTION READINESS DECLARED — all gates passed,
     0 open defects, 100% ASIL-D coverage.
  ✅ PASS  TC20 [ASIL-QM] Production readiness + campaign report ✓

================================================================
  TEST SUMMARY: 20/20 TCs pass  |  0 fail
================================================================
```

---

## Software QA Bridge

| Automotive Concept | Your World Equivalent |
|-------------------|-----------------------|
| **ASPICE** | ISO/IEC 29119 + CMMI — process maturity framework for software testing |
| **ISO 26262 ASIL** | Risk classification — P0/P1/P2/P3 priority, or FMEA severity×occurrence |
| **ASIL-D test** | Critical / blocking test — CI pipeline fails the build if this test fails |
| **Gate review** | Definition of Done + sprint review — formal evidence sign-off |
| **Traceability matrix** | Test case ↔ story/ticket/acceptance criteria mapping |
| **OPEN defect** | Open bug in Jira / VSTS — blocking further release |
| **FIXED defect** | Developer marks Jira ticket "Resolved" |
| **VERIFIED defect** | QA confirms fix in re-test → closes ticket |
| **CLOSED defect** | PM moves ticket to "Done" column |
| **SOP (Start of Production)** | Production release / CD deploy to production |
| **Orphan test** | Test without a linked requirement / user story |
| **Coverage gap** | Requirement without a linked test — untested acceptance criterion |
| **S3 timeout test** | Session expiry test — hit an auth-required endpoint after JWT expiry |
| **ASPICE CL3** | CMMI Maturity Level 3 — "defined" process |
| **Hazard analysis (HARA)** | Risk assessment / threat modelling |

---

## Quiz

**Q1.** A test case for the steering ECU's emergency steer assist is classified ASIL-D.
It fails. What exactly must happen before the project can advance to production?

<details><summary>Answer</summary>

1. A defect must be opened and assigned to the development team (OPEN status).
2. The developer must fix the root cause in the ECU firmware (FIXED status).
3. The **tester must re-run the failing TC and confirm it now passes** (VERIFIED status).
4. The project manager or safety manager must formally close the defect (CLOSED status).
5. Gate 4 (HIL Validation → Production) must be re-reviewed — with 0 open defects, it can now pass.

A waiver is **not acceptable** for ASIL-D failures. Unlike MAJOR defects at lower ASIL
levels (where a waiver + risk acceptance is sometimes possible), ASIL-D failures must
be fixed. They represent life-critical functionality.

</details>

---

**Q2.** An ASPICE assessor asks: "Show me your traceability matrix."
What should this document contain, and what would cause a finding?

<details><summary>Answer</summary>

The traceability matrix must show:
- Every software requirement mapped to ≥ 1 test case
- Every test case mapped to ≥ 1 software requirement
- The ASIL level of each requirement/test case
- The current pass/fail status of each test case

**Findings (ASPICE non-conformances):**
- A requirement with 0 test cases → coverage gap → finding
- A test case with 0 requirements → orphan test → finding (lower severity)
- A requirement classified ASIL-C but its test case is labelled QM → ASIL inconsistency → finding
- Test cases with status "NOT_RUN" at the gate review → process non-conformance → finding

At ASPICE CL2 (Managed), you must have the matrix. At CL3 (Defined), it must be maintained
in a qualified tool (e.g., DOORS, Polarion, Jira with specific plugins).

</details>

---

**Q3.** TC16 tests the S3 session timeout. The test sends nothing for 2.0 seconds, then
sends 0x27 0x01 and expects NRC 0x22. On the HIL rig, the test passes in SIL but fails
on real hardware. What are two likely root causes?

<details><summary>Answer</summary>

1. **Real ECU's S3 timer fires at a different threshold.**
   The SIL simulation uses `S3_TIMEOUT_S = 1.5 s`. The real ECU firmware may be configured
   for 5 seconds (the standard). After 2.0 seconds, the real ECU's S3 watchdog has NOT
   yet fired — the session is still active — so 0x27 0x01 returns a seed (positive),
   not NRC 0x22.
   Fix: read the actual S3 timeout from the ECU (it's in the session control response)
   and wait for `P2* + margin` instead of a hardcoded value.

2. **Background CAN traffic keeps the S3 timer alive.**
   On a real CAN bus, there may be periodic frames from other ECUs (speed, torque, ignition
   signals). If the ECU's UDS stack treats ANY received frame as a "heartbeat" that resets
   the S3 timer (a common implementation bug), the timer never expires regardless of the
   tester going silent.
   Fix: isolate the ECU from all other network traffic during the timeout test (e.g., using
   a network switch that blocks everything except the tester's CAN ID).

</details>

---

**Q4.** Your test campaign shows 100% pass for ASIL-QM and ASIL-A, but only 75% for ASIL-D
(3/4 tests pass). Gate 4 review is scheduled tomorrow. What must you present to the gate reviewer?

<details><summary>Answer</summary>

**What you cannot do:** Present a 75% pass rate and ask for a waiver on the ASIL-D failure.
ASIL-D waivers are not permitted under ISO 26262 without a complete safety case justification
signed off by a certified Functional Safety Manager — and even then, typically not for
production go/no-go.

**What you must present:**
1. The open defect report for the ASIL-D TC failure — with root cause analysis (RCA).
2. The impact assessment — which safety goal is affected, what is the residual risk.
3. A concrete fix plan with a timeline (commit, build, test).
4. Request to reschedule Gate 4 until the ASIL-D defect is CLOSED.

**What the gate reviewer will do:**
- Record the gate as BLOCKED with the specific ASIL-D defect as the blocker.
- Assign an action item to the developer team with a due date.
- Schedule a re-review after the defect is VERIFIED and CLOSED.

The gate review is not cancelled — it is formally documented as BLOCKED.

</details>

---

**Q5.** What is the difference between SWE.4 (Unit Verification), SWE.5 (Integration),
and SWE.6 (Qualification) in terms of what each phase tests and what evidence each produces?

<details><summary>Answer</summary>

| Aspect | SWE.4 Unit Verification | SWE.5 Software Integration | SWE.6 Qualification |
|--------|------------------------|---------------------------|---------------------|
| **Tests what** | Individual software units / functions | Multiple components integrated | Full SW component against SW requirements |
| **On what** | Source code (can be native host) | Integrated code build | Target binary on target (SIL or HIL) |
| **Test type** | Unit tests, static analysis, MC/DC coverage | Integration test, interface test | System test, regression test |
| **Typical ASIL** | QM, ASIL-A | ASIL-B | ASIL-C, ASIL-D |
| **Evidence produced** | Unit test report, coverage report (e.g., 100% statement coverage for ASIL-D) | Integration test report, interface test spec | Software qualification test report, test specification |
| **Day 19 equivalent** | TC01–TC04 (GROUP 1) | TC05–TC08 (GROUP 2) | TC09–TC16 (GROUPS 3 + 4) |

For ASIL-D, ISO 26262 mandates **100% MC/DC (Modified Condition/Decision Coverage)** at
unit level — every boolean branch must be tested in both true and false directions.
This is far stricter than branch coverage (80% is fine for most web applications).

</details>

---

## Key Takeaways

1. **ASPICE defines the process; ISO 26262 defines the safety requirements.**
   ASPICE says "have a traceability matrix." ISO 26262 says "label it with ASIL levels."
   Both are required by European OEM supplier contracts. Together they constitute the
   evidence package for a TÜV/SGS functional safety audit.

2. **ASIL-D test failures are not optional to fix.** They block the production gate
   with no acceptable waiver path. Your test campaign must achieve 100% ASIL-D
   coverage before Gate 4 passes.

3. **Traceability is not a document — it is a live link.** Every TC must trace to
   a requirement. Every requirement must have a TC. Orphan tests are waste; coverage
   gaps are risk.

4. **Defects have a lifecycle, not just a status.** OPEN → FIXED → VERIFIED → CLOSED.
   The tester owns VERIFIED. The developer owns FIXED. The PM owns CLOSED.
   The gate review only unblocks when CLOSED.

5. **Gate reviews are formal checkpoints, not ceremonies.** A blocked gate is a real
   stop sign. It gets documented, assigned an action owner, and tracked until cleared.
   This is what separates automotive software development from web app development.

6. **S3 timeout is an ASIL-D test for a reason.** Session management is a security
   and safety boundary. A stuck session = an unlocked ECU = a potential attack surface
   or uncontrolled command entry point. Test it. Every release cycle.

7. **The test campaign closes the loop.** In Day 1 you sent your first CAN frame.
   In Day 19, that same test runs as TC01 inside an ASPICE-compliant test campaign,
   linked to REQ-SW-0010, labelled QM, with gate evidence generated automatically.
   That is the full journey from "what is a CAN frame?" to "ship-ready."

---

## What's Next? (Day 20 Options)

| Option | Topic |
|--------|-------|
| **20A** | **DoIP (Diagnostics over IP)** — UDS over Ethernet + TCP/IP, ISO 13400-2 |
| **20B** | **OBD-II / Emission Testing** — PIDs, Mode 01–09, IUMPR readiness monitors |
| **20C** | **Automotive Cybersecurity** — UDS attack surface, AUTOSAR SecOC, IDS fuzzing |
| **20D** | **CAN FD Deep Dive** — BRS bit, ESI bit, 64-byte frames, ISO 11898-1:2015 |
| **20E** | **Masterclass Interview Prep: Days 1–19** — 20 interview rounds, all topics |

---

## Running the Simulation

```bash
cd "Day-19_Automotive_Lifecycle"
pip install python-can
python automotive_lifecycle.py
```

**What to watch:**

- **TC06 deliberately fails** — watch D001 open with `MAJOR/ASIL-B` classification
- **Gate 2 blocked** — the blockers list shows exactly why: ASIL-B failure + MAJOR open
- **Defect fix flow** — `[FIX]` → FIXED → RE-TEST → VERIFIED → CLOSED — the full lifecycle in ~5 lines
- **Gate 2 re-clears** — once D001 is CLOSED, the gate criteria are met
- **Traceability matrix** — every TC mapped to its requirement, every ASIL level visible
- **TC16** takes ~2 seconds (the deliberate S3 silence window)
- **ASIL-D: 4/4 → 100%** — production gate unlocked

> **Runtime:** approximately 4–6 seconds (dominated by TC16 S3 silence wait)

---

*Generated from a live mentoring session with Professor Embed. 🚗⚡🔥*
