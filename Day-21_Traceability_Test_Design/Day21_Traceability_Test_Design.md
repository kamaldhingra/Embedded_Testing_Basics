# Day 21: Traceability and Test Design

> **Professor Embed says:** "Let me ask you a question. You've written 20 test cases.
> They all pass. Are you done? In web testing your answer might be 'probably yes.'
> In automotive, that answer earns you a TÜV finding. Because the question isn't
> *whether* the tests pass. The question is: *can you prove which requirement each test
> covers, and can you prove every requirement has a test?*
> That is traceability. It is the difference between test coverage and test evidence.
> And today we build it from scratch — requirement by requirement, link by link."
>
> **Prerequisites:** Days 1–20 (full stack + ASPICE/ISO 26262/test design techniques)

---

## Quick Recap

| Day  | Topic |
|------|-------|
| 1–11  | CAN bus, DBC, tools |
| 12–16 | UDS services, ISO-TP |
| 17    | ECU flashing |
| 18    | SIL vs HIL |
| 19    | ASPICE, ISO 26262, gate reviews |
| 20    | Test design techniques (EP, BVA, State, Decision, MC/DC) |
| **21** | **Traceability: multi-level hierarchy, bidirectional links, change impact** |

---

## What Traceability Actually Means

**Traceability** is not a document. It is a live, bidirectional connection between:

```
  Hazard Analysis (ISO 26262 HARA)
          │
          ▼
  System Requirement  (SYS-001)
          │
          ▼
  Software Requirement  (SW-001)      ◄── ASPICE SWE.1
          │
          ▼
  Unit Requirement  (UNIT-001)        ◄── ASPICE SWE.3
          │
          ▼
  Test Case  (TC-F01)                 ◄── ASPICE SWE.4 / SWE.5 / SWE.6
          │
          ▼
  Test Execution Result
  PASS [2026-06-22 11:43]
```

Every arrow must be navigable in **both directions**:
- Forward: "Which TCs verify requirement SW-001?" → TC-F01, TC-F02
- Backward: "Which requirement does TC-F07 trace to?" → SW-006

If either direction breaks, you have a compliance gap.

---

## The Three Traceability Sins

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  SIN 1: ORPHAN TEST                                                        │
│                                                                            │
│  TC-ORPHAN ───► ???  (no requirement linked)                              │
│                                                                            │
│  Problem: "What are we testing?" Cannot be answered.                       │
│  ASPICE finding: SWE.4/5/6 — no backward traceability.                    │
│  Risk: We may be spending test effort on something nobody required.        │
│        Alternatively, we may be masking a requirement under the wrong TC.  │
├─────────────────────────────────────────────────────────────────────────────┤
│  SIN 2: COVERAGE GAP                                                       │
│                                                                            │
│  SW-UNTESTED ──► (no TCs linked)                                          │
│                                                                            │
│  Problem: "Is this requirement implemented correctly?"                     │
│           Cannot be answered. It has never been tested.                   │
│  ASPICE finding: SWE.4/5/6 — no forward traceability.                     │
│  Risk: The requirement may be implemented incorrectly.                     │
│        At Gate 2+ review, this blocks advancement.                        │
├─────────────────────────────────────────────────────────────────────────────┤
│  SIN 3: ASIL MISMATCH                                                      │
│                                                                            │
│  SW-005 [ASIL-C] ──► TC-F06 [ASIL-B]  (TC is lower ASIL than req)       │
│                                                                            │
│  Problem: The test was designed with less rigour than the requirement      │
│           demands. An ASIL-B test may not apply MC/DC coverage,            │
│           may not test all boundary points, may not be qualified.          │
│  ASPICE finding: ASIL integrity violation — immediate gate blocker.        │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## The Requirements Hierarchy Built Today

```
SYS-001  [ASIL-C]  Vehicle shall detect engine over-temperature
  └─ SW-001  [ASIL-C]  DTC P0217 confirmed when coolant temp > 105°C
       └─ UNIT-001 [ASIL-C]  DTC P0217 status byte shall be 0xAF (confirmed)
            ├── TC-F01  DTC P0217 confirmed at 110°C           BVA
            └── TC-F02  DTC P0217 NOT set at 104°C             BVA

SYS-002  [ASIL-B]  Diagnostic interface accessible via CAN
  ├─ SW-002  [ASIL-B]  Extended session includes P2/P2* timing bytes
  │    └─ UNIT-002 [ASIL-B]  Session response bytes 3–5 encode P2/P2*
  │         └── TC-F03  Extended session P2 timing bytes present  BVA
  ├─ SW-003  [ASIL-B]  DID 0xF189 returns SW version ASCII string
  │    └─ UNIT-003 [QM]   DID 0xF189 data is null-padded ASCII
  │         └── TC-F04  ReadDID 0xF189 returns SW version  EP
  ├─ SW-004  [ASIL-B]  Unknown DID returns NRC 0x31
  │    └── TC-F05  ReadDID unknown DID → NRC 0x31  EP
  ├─ SW-005  [ASIL-C]  SecurityAccess locks after 3 wrong key attempts
  │    └── TC-F06  SecurityAccess lockout after 3 wrong keys  BVA
  └─ SW-006  [ASIL-D]  S3 timeout drops session to defaultSession
       └── TC-F07  S3 timeout drops session  State Transition
```

**Key observations:**
- SW-001 has **two** TCs (TC-F01, TC-F02) — because it's ASIL-C, one BVA test pair is minimum
- TC-F01 links to **both** SW-001 and UNIT-001 — one TC, two requirement levels (many-to-many)
- SW-006 (ASIL-D) has the most formal specification — TC-F07 has a full ASPICE artifact ID

---

## Multi-Level Traceability: Why Three Levels?

The three levels (SYSTEM → SOFTWARE → UNIT) exist because different stakeholders own them:

```
┌──────────────────────────────────────────────────────────────────────────┐
│  LEVEL       WHO WRITES IT           ASPICE ACTIVITY   ASIL SOURCE      │
├──────────────────────────────────────────────────────────────────────────┤
│  SYSTEM      Systems engineer        SYS.2 / SWE.1     ISO 26262 HARA  │
│  (SYS-001)   (e.g., Bosch, BMW)     System req spec    ASIL derived     │
│                                                         from hazard       │
├──────────────────────────────────────────────────────────────────────────┤
│  SOFTWARE    SW architect            SWE.1 / SWE.2      ASIL inherited  │
│  (SW-001)    (your team)            SW req spec          or decomposed   │
├──────────────────────────────────────────────────────────────────────────┤
│  UNIT        SW developer            SWE.3               ASIL inherited  │
│  (UNIT-001)  (individual module)    Detailed design spec from SW parent  │
└──────────────────────────────────────────────────────────────────────────┘
```

**The chain rule:** If SYS-001 is ASIL-C, then SW-001 (its child) must be ASIL-C or higher.
If SW-001 is ASIL-C, then TC-F01 must be ASIL-C or higher.

Downgrading ASIL at any level requires a formal **ASIL decomposition** (ISO 26262 Part 9) —
you split the requirement into two ASIL-B components that together achieve ASIL-C.
Without the decomposition, the lower ASIL is an audit finding.

---

## Bidirectional Navigation

The `TraceabilityMatrix` maintains two maps:

```python
_req_to_tcs: Dict[str, List[str]]  # "SW-001" → ["TC-F01", "TC-F02"]
_tc_to_reqs: Dict[str, List[str]]  # "TC-F01" → ["SW-001", "UNIT-001"]
```

**Forward query** ("which TCs cover this requirement?"):
```python
tm._req_to_tcs["SW-001"]     # → ["TC-F01", "TC-F02"]
```

**Backward query** ("which requirements does this TC cover?"):
```python
tm._tc_to_reqs["TC-F01"]     # → ["SW-001", "UNIT-001"]
```

**Why both directions matter:**

| Direction | What it proves | Who needs it |
|-----------|---------------|--------------|
| Forward (req → TC) | Coverage — every req is tested | ASPICE auditor, gate reviewer |
| Backward (TC → req) | Traceability — every test has a purpose | QA manager, test reviewer |

In DOORS (IBM), Polarion (Siemens), or Jira with Xray, these links are maintained
graphically. Our `TraceabilityMatrix` implements the same concept in pure Python.

> 🌉 **From your world:** In TestRail or Zephyr for Jira, every test case has a
> "Requirement" field. Every user story in Jira has a "Tests" linked-issue panel.
> That's the same bidirectional link — just with a UI. The database structure underneath
> is identical to our `_req_to_tcs` / `_tc_to_reqs` dictionaries.

---

## The Change Impact Scenario

In the simulation (TC13–TC16), SW-001 receives a version bump:

```
[BEFORE]  SW-001 v1  threshold = strictly > 105.0°C
[CHANGE]  New thermal analysis: threshold recalculated to be validated
          → SW-001 v2  (same threshold, but evidence package refreshed)
```

The impact analysis immediately answers: **which tests must re-run?**

```python
tm.impact_analysis("SW-001")   # → ["TC-F01", "TC-F02"]
```

```
TC-F01:  DTC P0217 at 110°C        ← tests above-threshold behaviour
TC-F02:  DTC P0217 NOT at 104°C   ← tests below-threshold behaviour (BVA pair)
```

Both are flagged `change_impact = True`. Both are re-executed on the live ECU.
Both pass. Both flags are cleared. SW-001 version 2 is now validated.

**What this prevents:** A developer changes a requirement (perhaps a threshold, a timing
value, or an algorithm) but nobody knows which tests need re-running. Three months later,
a TC that was written for the old behaviour passes when it shouldn't (or fails in
production when it should have been updated). Change impact analysis closes this gap.

```
Without traceability:   "Something changed. Run everything. That takes 3 days."
With traceability:      "SW-001 changed. Impact: TC-F01, TC-F02. Re-run 2 tests."
```

> 🌉 **From your world:** Change impact analysis is exactly what happens in code coverage
> tools when you run `git diff` and the CI pipeline only re-runs tests that touch the
> changed files. The difference in automotive: the mapping is at the **requirement level**,
> not the file level. A requirement change may affect tests that touch completely
> different source files.

---

## The ASPICE TestCaseSpec Artifact

TC17 generates a formal test specification for TC-F07 (the S3 timeout ASIL-D test):

```
════════════════════════════════════════════════════════════════
  TEST CASE SPECIFICATION   (ASPICE SWE.6 / SWTS)
────────────────────────────────────────────────────────────────
  TC ID:        TC-F07
  Title:        S3 timeout drops session to defaultSession
  Req(s):       SW-006
  ASIL:         D
  Technique:    State Transition
  Artifact ID:  SWE.6-SWTS-TC-F07-v1.0
────────────────────────────────────────────────────────────────
  PRECONDITION:
    ECU powered on; defaultSession; no active session or TesterPresent running

  TEST STEPS:
    1. Send 0x10 0x03 (enter extendedDiagSession)
    2. Verify response positive and DID 0xF186 = 0x03
    3. Wait 2.0 s without sending any UDS message
    4. Send 0x27 0x01 (SecurityAccess requestSeed)
    5. Receive response

  EXPECTED RESULT:
    Negative response 0x7F 0x27 0x22 (conditionsNotCorrect — session dropped)
════════════════════════════════════════════════════════════════
```

**The 7 mandatory ASPICE fields** (TC19 verifies all are present):

| Field | Why mandatory |
|-------|---------------|
| `tc_id` | Unique identifier — referenced in test report and change log |
| `title` | Human-readable description — for review and audit |
| `aspice_artifact_id` | Version-controlled ID — ties spec to a specific release |
| `precondition` | Reproducibility — test is only valid in this starting state |
| `steps` | Repeatability — any qualified tester can follow and reproduce |
| `expected` | Objectivity — pass criterion is not subjective |
| `technique` | Justification — why these specific test cases were chosen (EP/BVA/etc.) |

A test spec that is missing any of these fields is an **ASPICE SWE.4 finding**.
Specifically: "Test case specification does not provide sufficient information to
determine pass/fail without subjective judgement."

---

## Coverage at SYSTEM vs SOFTWARE/UNIT Level

An important design decision in the simulation: the `find_gap_reqs()` and
`asil_coverage()` methods check only **SOFTWARE and UNIT** level requirements, not SYSTEM.

Why? System requirements (SYS-001, SYS-002) are tested *transitively* through their
software children. You don't write a test case that says "test SYS-001" — you write tests
for SW-001 (a software-level refinement of SYS-001). If all SW-001 tests pass, SYS-001
is covered by implication.

```
Correct automotive practice:
  SYS-001 covered by: SW-001 → {TC-F01, TC-F02}  ✓  (transitive)

Incorrect approach:
  SYS-001: add direct TC link to TC-F01  ← creates link-level confusion
  SW-001: also links to TC-F01           ← duplicate link, no added value
```

The tooling in DOORS/Polarion enforces this: you link test cases to the requirement
level they directly verify. Coverage at higher levels is computed transitively.

---

## The Traceability Dashboard Output

After TC20, the simulation prints this live dashboard:

```
╔══════════════════════════════════════════════════════════════╗
║   TRACEABILITY DASHBOARD                                     ║
╚══════════════════════════════════════════════════════════════╝

  REQUIREMENTS HIERARCHY  (REQ → UNIT → TC)

  SYS-001 [ASIL-C]  Vehicle shall detect engine over-temperature
  ├─ SW-001 [ASIL-C] v2 ⚡  DTC P0217 confirmed when temp > 105°C
  │   └─ UNIT-001 [ASIL-C]  DTC P0217 status byte shall be 0xAF
  │        └── TC-F01 ✅  DTC P0217 confirmed at 110 °C
  │   └─── TC-F02 ✅  DTC P0217 NOT set at 104 °C

  SYS-002 [ASIL-B]  Diagnostic interface accessible via CAN bus
  ├─ SW-002 [ASIL-B]  Extended session includes P2/P2* timing bytes
  │   └─ UNIT-002 [ASIL-B]  Session response bytes 3–5 encode P2/P2*
  │        └── TC-F03 —  Extended session P2 timing bytes present
  ├─ SW-003 [ASIL-B]  DID 0xF189 returns SW version ASCII string
  │   └─ UNIT-003 [ASIL-QM]  DID 0xF189 data format
  │        └── TC-F04 —  ReadDID 0xF189 returns SW version
  ├─ SW-004 [ASIL-B]  Unknown DID returns NRC 0x31
  │   └─── TC-F05 —  ReadDID unknown DID → NRC 0x31
  ├─ SW-005 [ASIL-C]  SecurityAccess locks after 3 wrong key attempts
  │   └─── TC-F06 —  SecurityAccess lockout after 3 wrong keys
  ├─ SW-006 [ASIL-D]  S3 timeout drops session to defaultSession
  │   └─── TC-F07 ✅  S3 timeout drops session

  COVERAGE METRICS
  ASIL       Reqs     Covered    %
  ASIL-QM    1        1         100%  ██████████
  ASIL-B     4        4         100%  ██████████
  ASIL-C     3        3         100%  ██████████
  ASIL-D     1        1         100%  ██████████
  TOTAL      9        9         100%

  HEALTH INDICATORS
  Change Impact:  0 TCs flagged  ✅
  Orphan TCs:     0  ✅
  Coverage Gaps:  0  ✅
```

The `⚡` marker on SW-001 shows it was changed this session. The `v2` shows the version
was bumped. TC-F03 through TC-F06 are `—` (NOT_RUN) because they were not executed in
this session — but they ARE linked to requirements, so no coverage gap exists.

---

## Test Cases Overview

| TC | Group | What It Verifies | Key Assertion |
|----|-------|-----------------|---------------|
| TC01 | Traceability | All 6 SW reqs have ≥1 TC | `find_gap_reqs()` = empty |
| TC02 | Traceability | All 7 TCs have ≥1 req | `find_orphan_tcs()` = empty |
| TC03 | Traceability | SYS→SW→UNIT→TC chain intact | All 4 links exist |
| TC04 | Traceability | Every TC ASIL ≥ linked req ASIL | `asil_consistency_errors()` = [] |
| TC05 | Coverage | ASIL-B coverage = 100% | 4/4 ASIL-B reqs covered |
| TC06 | Coverage | ASIL-C coverage = 100% | 3/3 ASIL-C reqs covered |
| TC07 | Coverage | ASIL-D coverage = 100% | 1/1 ASIL-D req covered |
| TC08 | Coverage | Overall coverage = 100% | 9/9 SW+UNIT reqs covered |
| TC09 | Gap/Orphan | Orphan TC detected | TC-ORPHAN in `find_orphans()` |
| TC10 | Gap/Orphan | Untested req detected | SW-UNTESTED in `find_gaps()` |
| TC11 | Gap/Orphan | Link resolves orphan | `find_orphans()` = empty |
| TC12 | Gap/Orphan | Link resolves gap | `find_gaps()` = empty |
| TC13 | Change Impact | SW-001 bump → impact_analysis | Returns [TC-F01, TC-F02] |
| TC14 | Change Impact | TCs flagged for re-run | `change_impact = True` |
| TC15 | Change Impact | Re-run TCs on live ECU | DTC at 110°C ✓, no DTC at 104°C ✓ |
| TC16 | Change Impact | Flags cleared after re-run | `change_impact = False` |
| TC17 | Spec Artifact | Formal spec printed for TC-F07 | All 7 ASPICE fields present |
| TC18 | Spec Artifact | Execute spec on live ECU | NRC 0x22 after S3 timeout |
| TC19 | Spec Artifact | All 7 specs have mandatory fields | No empty required fields |
| TC20 | Dashboard | Full dashboard, all indicators green | 0 orphans, 0 gaps, 0 impact |

---

## Software QA Bridge

| Automotive Concept | Your World Equivalent |
|-------------------|-----------------------|
| **Traceability matrix** | Test case ↔ user story link in TestRail/Zephyr/Xray |
| **Orphan test** | Test case with no linked Jira story — floating test that nobody knows what it covers |
| **Coverage gap** | User story with no linked test cases in the sprint — unverified acceptance criteria |
| **ASIL mismatch** | Test marked P1/critical but story is P3/low — risk misclassification |
| **Forward traceability** | "Show me all tests for story JIRA-1234" filter in TestRail |
| **Backward traceability** | Clicking a test case → "Linked Requirements" panel |
| **Multi-level hierarchy** | Epic → Story → Task → Test → Execution |
| **Change impact analysis** | `git diff` + coverage report — "which tests touch the changed file?" |
| **ASPICE TestCaseSpec** | TestRail test case with all required fields: precondition, steps, expected result |
| **`aspice_artifact_id`** | Test case version in TestRail (e.g., "TC-F07 v1.0") |
| **Version bump on requirement** | Story edited after sprint start → all linked tests marked "needs review" |
| **SW-006 ASIL-D needs MC/DC** | P0/critical test requires 100% branch coverage + code review |
| **SYSTEM req tested transitively** | Epic acceptance criteria validated via story tests (not tested directly) |
| **TÜV / ASPICE audit** | SOC 2 Type II audit — auditor checks that every control has evidence |

---

## Quiz

**Q1.** You have 50 requirements and 45 test cases. Running `find_gap_reqs()` returns 5
requirements. Running `find_orphan_tcs()` returns 0. What does this tell you, and what
is the risk?

<details><summary>Answer</summary>

**What it tells you:**
- 5 requirements have no test cases (coverage gaps) — they have never been directly verified
- 0 test cases lack a requirement link — all 45 TCs are traceable to at least one requirement
- This means some TCs link to MORE than one requirement (45 TCs cover 45 of 50 requirements)

**The risk:**
The 5 untested requirements are a compliance gap. If any of them are ASIL-B or higher,
this is a blocking gate review finding. Even for QM requirements, an auditor will ask
"can you demonstrate these were implemented correctly?" — and the answer would be no.

**Resolution options:**
1. Write new test cases for the 5 requirements
2. Find existing TCs that implicitly test the requirements and add the link
3. For truly non-testable requirements (e.g., "the system shall be documented"), create
   documentation-based test cases ("verify the document exists and is up to date")
4. Formally accept the risk (requires a signed-off risk acceptance with justification
   — only valid for QM requirements)

</details>

---

**Q2.** A requirement is changed from "DTC P0217 confirmed when temp > 105°C" to
"DTC P0217 confirmed when temp > 103°C" (new threshold from thermal analysis).
Your impact analysis returns TC-F01 and TC-F02. TC-F01 re-runs and PASSES.
TC-F02 re-runs and FAILS. What must happen?

<details><summary>Answer</summary>

TC-F02 was written to verify "DTC NOT set at 104°C" — which was the correct BVA
test for the old threshold of 105°C (104°C was "just below threshold"). Now that the
threshold is 103°C, 104°C is **above** the new threshold — so the DTC SHOULD be set
at 104°C. TC-F02 now fails because it expected no DTC but the DTC is present.

**What must happen:**

1. TC-F02 FAILS → open a defect: "TC-F02 fails after SW-001 threshold change"
2. Analyse the root cause: the test expectation is now wrong (not the ECU code)
3. Update TC-F02: change the BVA test point from 104°C to 102°C (new "just below"
   threshold for the 103°C boundary)
4. Add a new BVA test TC-F02b at 103°C (the new exact boundary — "DTC NOT set")
5. Add a new BVA test TC-F02c at 103.1°C (just above — "DTC confirmed")
6. Re-run all updated TCs → verify PASS
7. Close the defect; resolve change impact flags
8. Update the test specification artifact to v1.1

The key insight: the test failure here is NOT an ECU bug — it's the test spec that
needs updating to match the new requirement. Change impact analysis surfaces this
automatically, preventing silent drift between requirement and test.

</details>

---

**Q3.** An ASPICE assessor checks your traceability matrix and finds that TC-F06
(SecurityAccess lockout — ASIL-C) has `aspice_artifact_id = ""` (empty).
What is the finding, and at what ASPICE capability level is this an issue?

<details><summary>Answer</summary>

**The finding:** TC-F06 has no version-controlled artifact identifier. This means:
- There is no way to determine which version of the test was executed
- The test result cannot be unambiguously linked to a specific test specification
- If the test spec is updated later, there is no audit trail showing which version produced the recorded result

**ASPICE Capability Level impact:**
- **CL1 (Performed):** Minor finding — the test exists and runs.
- **CL2 (Managed):** Major finding — CL2 requires that work products are managed under version control with unique identifiers. Missing `aspice_artifact_id` means the test specification is not uniquely identified. This would be raised against the "Work Product Management" generic practice (GP 2.3.3).
- **CL3 (Defined):** Major finding — CL3 requires processes to be defined and followed. The test specification template should require an artifact ID; deviating from the template is a CL3 finding.

**Resolution:** Assign `aspice_artifact_id = "SWE.6-SWTS-TC-F06-v1.0"` and commit
the spec to version control. The ID format typically encodes: SWE level, artifact type,
TC identifier, and version.

</details>

---

**Q4.** TC04 (ASIL consistency check) is passing — all TCs have ASIL ≥ the highest ASIL
of their linked requirements. Now a colleague adds TC-F08 (ASIL-A) and links it to
SW-006 (ASIL-D). TC04 runs and FAILS. What exactly is the violation, and why is it dangerous?

<details><summary>Answer</summary>

**The violation:** TC-F08 is labelled ASIL-A but SW-006 requires ASIL-D verification.
An ASIL-A test does not meet the rigour requirements for ASIL-D:
- ASIL-A does not require MC/DC coverage
- ASIL-A does not require all BVA boundary points (just representative values)
- ASIL-A does not require formal test specification with qualified tools
- ASIL-A tests may not require independent review

**Why it's dangerous:**
If TC-F08 passes (even correctly), the organisation might record "SW-006 — PASS" in the
test report. An auditor looks at the test result and sees "PASS." But the test was
designed with insufficient rigour for ASIL-D. The behaviour was not fully verified.
A real fault condition might exist at a boundary that was never tested because ASIL-A
doesn't mandate BVA at exact thresholds.

In a worst case: the S3 timeout (SW-006) has a firmware bug that only manifests at
exactly S3_TIMEOUT_S (not at S3_TIMEOUT_S + 0.5 which the inadequate test checks).
The ASIL-A test misses it. The car ships. The ECU stays in extended session permanently
after a tester disconnects. A malicious CAN node exploits the open session.

**Resolution:** Re-classify TC-F08 to ASIL-D, apply all ASIL-D test design requirements
(MC/DC, full BVA, formal spec, qualified tool), and re-run under the correct rigour.

</details>

---

**Q5.** You are asked to integrate the `TraceabilityMatrix` into a CI/CD pipeline
that runs on every git commit. Which checks should run automatically, and which should
be reserved for human review at gate reviews?

<details><summary>Answer</summary>

**Automated on every commit (fast checks, fail the build):**
- `find_orphan_tcs()` → fail if any TC has no requirement link
- `find_gap_reqs()` → fail if any SOFTWARE or UNIT req has no TC link
- `asil_consistency_errors()` → fail if any TC ASIL < linked requirement ASIL
- `asil_coverage()["D"]["covered"] == asil_coverage()["D"]["total"]` → fail if any ASIL-D req is uncovered

**Automated on every commit (warn, do not fail):**
- `find_gap_reqs()` for QM requirements → warning only (gaps are acceptable with risk justification)
- Changed requirement `version > 1` with `change_impact` TCs not yet re-run → warning

**Reserved for gate review (human judgement required):**
- ASIL decomposition decisions ("can ASIL-C be decomposed into 2× ASIL-B?")
- Risk acceptance for non-testable QM requirements
- Review of test case quality (are the steps clear enough for an independent tester?)
- Verification that test execution records were generated by a qualified tool
- Review of formal TestCaseSpec artifact IDs and version control

**The principle:** Structural checks (links, coverage, ASIL consistency) are objective
and fast — automate them. Judgement calls (quality, risk acceptance, qualification) require
human expertise — gate them.

</details>

---

## Key Takeaways

1. **Traceability is bidirectional.** Forward: "which TCs cover this req?" Backward:
   "which req does this TC cover?" Both must be navigable. If either direction is broken,
   you have either an orphan or a gap.

2. **Orphans waste effort; gaps create risk.** An orphan test burns test time on something
   nobody specified. A coverage gap means a requirement was never verified. Both are
   ASPICE findings. Both block gate reviews.

3. **ASIL consistency is not optional.** If a requirement is ASIL-C, its test case must
   be at least ASIL-C. Downgrading the test while keeping the requirement at ASIL-C
   is a safety violation, not just a process gap.

4. **Change impact analysis is the ROI of traceability.** The entire investment in
   building a traceability matrix pays back the moment a requirement changes.
   Instead of re-running everything (expensive) or nothing (dangerous),
   you re-run exactly the affected tests.

5. **SYSTEM level requirements are covered transitively.** You don't write test cases
   directly against SYS-001. You write them against SW-001 and UNIT-001, which are
   derived from SYS-001. Coverage propagates up the hierarchy.

6. **The ASPICE TestCaseSpec is evidence, not documentation.** The 7 mandatory fields
   (ID, title, artifact ID, precondition, steps, expected, technique) exist so that any
   qualified tester can reproduce the test, and an auditor can confirm the result was
   produced by a specific, versioned procedure.

7. **The matrix is a living document.** Requirements change. Test cases change.
   Version numbers track this. The `⚡` change indicator in the dashboard shows
   SW-001 was modified this session. Every change must be accompanied by a re-run of
   the impacted TCs before the change_impact flags can be cleared.

---

## What's Next? (Day 22 Options)

| Option | Topic |
|--------|-------|
| **22A** | **DoIP (Diagnostics over IP)** — UDS over Ethernet + TCP/IP, ISO 13400-2 |
| **22B** | **OBD-II / Emission Testing** — PIDs, Mode 01–09, IUMPR readiness monitors |
| **22C** | **Automotive Cybersecurity** — UDS attack surface, AUTOSAR SecOC, fuzz testing |
| **22D** | **CAN FD Deep Dive** — BRS bit, ESI bit, 64-byte frames, ISO 11898-1:2015 |
| **22E** | **Masterclass Interview Prep: Days 1–21** — 21 interview rounds, all topics |

---

## Running the Simulation

```bash
cd "Day-21_Traceability_Test_Design"
pip install python-can
python traceability_matrix.py
```

**What to watch:**

- **TC01–TC04:** Matrix integrity checks run instantly — pure data structure operations
- **TC09–TC12:** Watch the orphan/gap injection cycle. TC09 adds TC-ORPHAN (instantly
  detected). TC10 adds SW-UNTESTED (detected). TC11 links them. TC12 verifies clean state.
- **TC13–TC14:** The `⚡` indicator and `v2` appear on SW-001 after the version bump.
  TC-F01 and TC-F02 are immediately flagged.
- **TC15:** Two live ECU calls — the DTC test at 110°C and 104°C. Watch the
  `[RE-RUN]` lines. Both pass; flags clear.
- **TC17:** The formal spec prints with step numbers, precondition, and artifact ID.
- **TC18:** `[WAIT] 2.0 s S3 silence window...` — deliberate pause, then NRC 0x22 confirms
  the watchdog fired.
- **TC20 dashboard:** The tree shows the full SYS→SW→UNIT→TC hierarchy. `v2 ⚡` on SW-001.
  COVERAGE METRICS bar chart. All health indicators green.

> **Runtime:** approximately 3–5 seconds (dominated by TC18 S3 silence window)

---

*Generated from a live mentoring session with Professor Embed. 🚗⚡🔥*
