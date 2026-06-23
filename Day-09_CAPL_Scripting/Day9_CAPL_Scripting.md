# 🖥️ Day 9: Introduction to CAPL Scripting

> **Course:** Embedded & IoT Testing Masterclass
> **Instructor:** Professor Embed
> **Prerequisites:** Day 1–8 (Complete CAN fundamentals through DBC Deep Dive)

---

## 📚 Table of Contents

1. [Recap: Where We Left Off](#recap-where-we-left-off)
2. [Concept: What Is CAPL and Why Does It Exist?](#concept-what-is-capl)
3. [Concept: The Event-Driven Model — CAPL's Core Architecture](#concept-event-driven-model)
4. [Concept: CAPL Language Basics — Syntax, Types, Variables](#concept-language-basics)
5. [Concept: The Four Roles of a CAPL Node](#concept-four-roles)
6. [Concept: Sending Messages — Stimulating the Bus](#concept-sending-messages)
7. [Concept: Receiving Messages — Asserting on the Bus](#concept-receiving-messages)
8. [Concept: Timers — Periodic and One-Shot](#concept-timers)
9. [Concept: Test Nodes — Writing Formal Test Cases](#concept-test-nodes)
10. [The Big Picture: CAPL in the CANoe Architecture](#the-big-picture)
11. [Where It's Used in the Real World](#where-its-used-in-the-real-world)
12. [How a Tester Thinks About It](#how-a-tester-thinks-about-it)
13. [Hands-On Exercise: CAPL Signal Validator (python-can edition)](#hands-on-exercise)
14. [Challenge: The ABS Validation Suite](#challenge-the-abs-validation-suite)
15. [Quiz + Answers](#quiz--answers)
16. [Key Takeaways](#key-takeaways)

---

## 🔁 Recap: Where We Left Off

On **Day 7** you met CANoe — the flight simulator of automotive CAN testing. We noted that it has a built-in scripting language called **CAPL** and showed a one-line analogy:

> *"CAPL's `on message` handler is exactly Playwright's `page.on('response', handler)` — event-driven assertion that fires when a matching thing appears."*

On **Day 8** you mastered the DBC — the contract that defines every signal, its encoding, its valid range, its cycle time. You saw that `GenMsgCycleTime` and `GenSigStartValue` are attributes that *should* drive test generation automatically.

Today those two threads come together. CAPL is the programming language that:
- Reads the DBC you mastered on Day 8
- Reacts to the CAN events you understand from Days 1–6
- Writes test assertions against the signals and timing you can now reason about from first principles

If Day 8 was "know the spec," Day 9 is "automate the spec." Let's write our first CAPL test suite. 🖥️

---

## 🧠 Concept: What Is CAPL and Why Does It Exist?

### The "Embedded Playwright" Analogy 🎭

When you test a web app with Playwright, you:
1. Open a browser (set up the test environment)
2. Listen for network responses (observe the bus)
3. Click buttons / fill forms (stimulate the system)
4. Assert on DOM state or response bodies (validate signals)
5. Report pass/fail with context (structured test output)

**CAPL does exactly this for a CAN bus:**
1. Initialize a measurement (set up the test environment)
2. `on message` handlers (observe the bus)
3. `output()` calls (stimulate the system)
4. Signal value checks + `testStepPass/Fail` (validate signals)
5. Structured HTML/XML test reports (test output)

> **CAPL = Communication Access Programming Language.** It was designed by Vector Informatik specifically to program CANoe nodes. It's **C-like** in syntax — familiar if you know C, Java, C#, or even TypeScript with its curly braces and semicolons — but with one critical twist: **everything is event-driven**. There is no `main()` loop polling for data. Instead, you declare what you care about, and CAPL calls your handler when it happens.

### Why Not Just Python / C++ / Java?

Good question — and the answer reveals something important about embedded testing:

```
┌────────────────────────────────────────────────────────────┐
│  WHY CAPL INSTEAD OF GENERAL-PURPOSE LANGUAGES?            │
├────────────────────────────────────────────────────────────┤
│  1. TIGHT TOOL INTEGRATION                                 │
│     CAPL runs *inside* CANoe's measurement engine. It has  │
│     direct access to the bus at hardware interrupt speed — │
│     no OS scheduling jitter between "message arrived" and  │
│     "my handler fires."                                    │
│                                                            │
│  2. DBC-AWARE BY DEFAULT                                   │
│     CAPL can reference DBC signal names natively:          │
│       this.EngineRPM  ← no decoder call needed            │
│     The tool compiles DBC definitions into the script.     │
│                                                            │
│  3. REAL-TIME SAFE                                         │
│     CAPL runs in the same deterministic context as the bus.│
│     A Python script on the OS has no such guarantee.       │
│                                                            │
│  4. INDUSTRY STANDARD                                      │
│     Every major OEM and Tier-1 uses CAPL. Job postings     │
│     list "CANoe/CAPL" as a required skill. It's the        │
│     Playwright of the embedded world — if you know it,     │
│     you're immediately productive in any automotive lab.   │
└────────────────────────────────────────────────────────────┘
```

> 🌉 **From your world:** CAPL is to CANoe what Playwright's JavaScript API is to Chromium — a first-party, tight-integration scripting layer. You *could* drive Chrome via raw DevTools Protocol from any language, but Playwright's native binding is faster, richer, and what everyone uses. Same logic.

---

## 🧠 Concept: The Event-Driven Model — CAPL's Core Architecture

### The Node.js Event Loop Analogy 🔄

Node.js has no blocking `main()` — instead, you register listeners (`EventEmitter.on(...)`) and the event loop calls them when events fire. CAPL works identically: you register handlers, and the CANoe measurement engine calls them when matching bus events occur.

The **complete set of CAPL event handlers:**

```
┌────────────────────────────────────────────────────────────┐
│              CAPL EVENT HANDLERS — QUICK REFERENCE         │
├────────────────────────────────────────────────────────────┤
│  on start            → measurement begins (test setup)     │
│  on stopMeasurement  → measurement ends (test teardown)    │
│  on preStart         → runs before start, one-time init   │
│                                                            │
│  on message <id>     → specific message ID received       │
│  on message *        → ANY message received               │
│  on message EngineData → message matched by DBC name      │
│                                                            │
│  on timer <name>     → a timer fires (periodic or one-shot)│
│                                                            │
│  on key 'x'          → keyboard key pressed (interactive) │
│  on key 0x20         → space bar pressed                  │
│                                                            │
│  on envVar <name>    → an environment variable changes    │
│  on signal <name>    → a signal value changes (CANdb++)   │
│                                                            │
│  on errorFrame       → an error frame detected on bus     │
│  on busOff           → a node entered bus-off state       │
└────────────────────────────────────────────────────────────┘
```

> 🌉 **The software testing parallel — exact 1:1 mapping:**

| CAPL event | Software equivalent |
|---|---|
| `on start` | `beforeAll()` / `beforeEach()` in Jest/Mocha |
| `on stopMeasurement` | `afterAll()` / `afterEach()` |
| `on message EngineData` | `page.on('response', r => r.url().includes('engine'))` |
| `on timer myTimer` | `setInterval()` / `setTimeout()` |
| `on key 'r'` | A keyboard shortcut to trigger a test manually |
| `on errorFrame` | `page.on('requestfailed', handler)` |

### The Minimal Valid CAPL Script

```capl
/*
 * Minimal CAPL script — the "Hello World" of CAN testing
 * This runs inside a CANoe node's CAPL program (.can file)
 */

variables {
  // Variables declared here are persistent across all handlers
  int    messageCount = 0;
  float  lastRPM     = 0.0;
}

on start {
  // Called once when the measurement starts — your beforeAll()
  write("Measurement started. Listening for EngineData...");
  messageCount = 0;
}

on message EngineData {
  // Called every time EngineData (msg 0x0C9 per DBC) is received
  // 'this' is the incoming message object, signals decoded from DBC
  lastRPM = this.EngineRPM;
  messageCount++;

  if (messageCount % 100 == 0) {
    write("Received %d EngineData frames. Latest RPM: %.1f", messageCount, lastRPM);
  }
}

on stopMeasurement {
  // Called once at the end — your afterAll()
  write("Measurement stopped. Total frames received: %d", messageCount);
}
```

> The `write()` function is CAPL's `console.log()`. It prints to CANoe's Write window. In test nodes (coming up), `testStepPass()` and `testStepFail()` are the assertion equivalents.

---

## 🧠 Concept: CAPL Language Basics — Syntax, Types, Variables

### It's C with a Bus Attached

If you know C, Java, C#, or TypeScript, CAPL syntax is immediately readable. Here's the mental model:

```capl
// ─── DATA TYPES ─────────────────────────────────────────────
int     i = 0;          // 32-bit signed integer (like C int)
long    l = 0;          // 32-bit signed (synonym for int in CAPL)
float   f = 0.0;        // 32-bit float
double  d = 0.0;        // 64-bit float
byte    b = 0x00;       // 8-bit unsigned
word    w = 0x0000;     // 16-bit unsigned
dword   dw = 0x00000000;// 32-bit unsigned
char    c = 'A';        // character
char    s[64] = "";     // fixed-length string (no std::string!)
msTimer t1;             // millisecond timer object
mstimer t2;             // same (alias)

// ─── MESSAGE VARIABLE ──────────────────────────────────────
message EngineData txMsg;     // DBC-typed message variable
message 0x100      rawMsg;    // message by raw ID

// ─── SIGNAL ACCESS (in an on message handler) ──────────────
on message EngineData {
  float rpm  = this.EngineRPM;    // DBC-named signal access
  byte  dlc  = this.dlc;          // message DLC
  dword id   = this.id;           // message arbitration ID
  float ts   = this.time;         // hardware timestamp (ms)
  byte  dir  = this.dir;          // 0=Rx, 1=Tx, 2=TxReq
}
```

### Key Differences from "Normal" Languages

| Feature | CAPL | C/Java/Python |
|---|---|---|
| Main entry point | `on start` handler | `main()` / `__init__` |
| Loops | `while`, `for`, `do-while` (but avoid blocking!) | All supported |
| String type | `char s[64]` fixed array | `string`, `str`, etc. |
| Memory allocation | **No dynamic allocation** (no `malloc`, no `new`) | Full heap |
| Threading | **No threads** — event model replaces it | Full threading |
| Standard library | Limited — `write()`, `abs()`, `sqrt()`, `sprintf()` | Full stdlib |
| Struct/class | `struct` only (no OOP) | Both |
| Timers | First-class `msTimer` type | Library-dependent |

> ⚠️ **The #1 CAPL trap:** **Never block in a handler.** If you put a `while(true)` or a long `for` loop inside an `on message` handler, you freeze the entire measurement engine — all other handlers stop firing, the bus stops being monitored. This is the embedded equivalent of blocking the JavaScript event loop with a synchronous `sleep()`. The CAPL execution model is cooperative, not preemptive.

> 🌉 **From your world:** This is *exactly* why you never do `cy.wait(5000)` in Cypress — it blocks the event loop. Instead you use async assertions (`cy.contains(...)` which retries). CAPL has the same constraint: use timers for delays, not blocking loops.

---

## 🧠 Concept: The Four Roles of a CAPL Node

In CANoe, a CAPL script lives inside a **network node** — a simulated participant on the bus. Depending on what it does, a node plays one of four roles:

```
┌────────────────────────────────────────────────────────────┐
│            FOUR ROLES OF A CAPL NODE                       │
├─────────────────┬──────────────────────────────────────────┤
│  SIMULATION     │ Replaces a real ECU entirely. Sends the  │
│  NODE           │ ECU's periodic messages on schedule,     │
│                 │ responds to stimulus. Used in HIL when   │
│                 │ the real ECU is not present.             │
├─────────────────┼──────────────────────────────────────────┤
│  STIMULATION    │ Injects specific signals / fault cases   │
│  NODE           │ to drive the real ECU under test.       │
│                 │ The "test driver" — like Playwright's    │
│                 │ click() + fill() calls.                  │
├─────────────────┼──────────────────────────────────────────┤
│  MONITORING     │ Passive listener. Observes, records,     │
│  NODE           │ checks values. Never transmits.          │
│                 │ Like Playwright's network interceptor in │
│                 │ read-only mode.                          │
├─────────────────┼──────────────────────────────────────────┤
│  TEST NODE      │ Runs a formal test sequence: setup →     │
│  (Test Module)  │ stimulus → assert → teardown. Uses       │
│                 │ testStepPass/Fail, generates a report.   │
│                 │ Your Playwright/Cypress test file.       │
└─────────────────┴──────────────────────────────────────────┘
```

> In practice, a real CANoe project has all four types coexisting. The **simulation nodes** provide the virtual bus environment. The **stimulation node** drives the real ECU. The **monitoring node** runs continuously for safety checks. The **test node** runs structured test cases on demand.

---

## 🧠 Concept: Sending Messages — Stimulating the Bus

### Building and Sending a Message

```capl
variables {
  message EngineData txEngineData;   // declare a typed message variable
  msTimer sendTimer;                 // timer for periodic transmission
}

on start {
  // Set initial signal values
  txEngineData.EngineRPM   = 0;     // raw value (not physical!)
  txEngineData.CoolantTemp = 65;    // raw = 65 → physical = 65×1+(-40) = 25°C
  txEngineData.ThrottlePos = 0;

  setTimer(sendTimer, 10);          // start periodic send every 10ms
}

on timer sendTimer {
  // This fires every 10ms — the periodic heartbeat from Day 4
  output(txEngineData);             // transmit the message onto the bus
  setTimer(sendTimer, 10);          // re-arm for next cycle
}
```

> `output(msg)` is how CAPL puts a frame on the bus. It's the equivalent of `page.evaluate(() => fetch('/api/engine', {method:'POST', body:{rpm:2000}}))` in Playwright — it's your test **stimulating** the system.

### Simulating a Signal Ramp (Engine Startup)

```capl
on timer sendTimer {
  float physRPM;

  // Simulate engine warming up: RPM ramps from 800 to 2000 over 10 seconds
  physRPM = 800.0 + (timeNow() / 100000.0) * 1200.0;  // timeNow() in 0.1µs ticks
  if (physRPM > 2000.0) physRPM = 2000.0;

  // Convert physical → raw using the DBC formula:  raw = (physical - offset) / scale
  // EngineRPM: scale=0.25, offset=0  →  raw = physical / 0.25
  txEngineData.EngineRPM = (word)(physRPM / 0.25);

  output(txEngineData);
  setTimer(sendTimer, 10);
}
```

> 💡 **The Day 8 connection:** To SET a signal value you reverse the decode formula: `raw = (physical - offset) / scale`. To GET a signal value you use the decode formula: `physical = raw × scale + offset`. Both formulas appear in the same CAPL stimulation script.

### Sending a One-Shot Fault Injection

```capl
on key 'f' {
  // Press 'f' to inject a corrupted CoolantTemp frame
  message EngineData faultMsg;
  faultMsg.EngineRPM   = txEngineData.EngineRPM;
  faultMsg.CoolantTemp = 0xFF;    // raw 255 → 255×1-40 = 215°C (max spec value = overheat!)
  faultMsg.ThrottlePos = txEngineData.ThrottlePos;
  output(faultMsg);
  write("⚠️  Fault injected: CoolantTemp set to max (0xFF = 215°C)");
}
```

> 🌉 **From your world:** This is **fault injection / chaos engineering** — the embedded equivalent of injecting a 500 error into an API response to test your circuit breaker. You've done this with tools like `cy.intercept({url: '/api'}).as('req').then(r => r.reply({statusCode: 500}))`. CAPL's `on key` handler lets you trigger the same kind of fault injection with a single keypress in CANoe's live GUI.

---

## 🧠 Concept: Receiving Messages — Asserting on the Bus

### Passive Monitoring with Assertions

```capl
variables {
  float  maxRPM_observed = 0.0;
  int    outOfRangeCount = 0;
  dword  lastTimestamp   = 0;
  int    cycleTimeViolations = 0;
}

on message EngineData {
  float rpm     = this.EngineRPM;
  float physRPM = rpm * 0.25 + 0.0;   // manual decode: scale=0.25, offset=0

  // ── ASSERTION 1: Signal range check (from DBC min/max) ──
  if (physRPM < 0.0 || physRPM > 16383.75) {
    write("❌ EngineRPM OUT OF RANGE: %.1f RPM at t=%.3f ms", physRPM, this.time);
    outOfRangeCount++;
  } else if (physRPM > maxRPM_observed) {
    maxRPM_observed = physRPM;
  }

  // ── ASSERTION 2: Cycle time check (from DBC GenMsgCycleTime = 10ms) ──
  if (lastTimestamp > 0) {
    float interval = this.time - lastTimestamp;     // ms
    if (interval < 9.0 || interval > 11.0) {        // ±1ms jitter tolerance (Day 4)
      write("⚠️  CycleTime violation: %.3f ms (expected 10ms ±1ms)", interval);
      cycleTimeViolations++;
    }
  }
  lastTimestamp = this.time;
}

on stopMeasurement {
  write("──── EngineRPM Summary ────");
  write("Max RPM observed : %.1f", maxRPM_observed);
  write("Range violations : %d",   outOfRangeCount);
  write("Cycle violations : %d",   cycleTimeViolations);
}
```

> 🌉 **From your world:** The `on message` handler IS `page.on('response', r => { expect(r.status()).toBe(200); })` — an event-driven assertion. The cycle-time check is your Day 4 `analyze_timing()` logic, now running in real-time inside CANoe instead of post-processing a log. Real-time vs batch analysis: same math, different execution context.

### Receiving with Signal Correlation

Sometimes you need to check that **two signals in different messages** agree — a classic integration test scenario:

```capl
variables {
  float lastEngineRPM  = 0.0;
  float lastDashRPM    = 0.0;
  int   correlationFails = 0;
}

on message EngineData {
  lastEngineRPM = this.EngineRPM * 0.25;   // ECU's reported RPM
}

on message DashDisplay {
  lastDashRPM = this.DisplayedRPM * 0.25;  // Dashboard's displayed RPM

  // These two must agree within 25 RPM (one step at scale=0.25)
  float diff = lastEngineRPM - lastDashRPM;
  if (diff < 0) diff = -diff;    // abs() for float

  if (diff > 25.0) {
    write("❌ RPM MISMATCH: Engine=%.1f  Dashboard=%.1f  diff=%.1f",
          lastEngineRPM, lastDashRPM, diff);
    correlationFails++;
  }
}
```

> This is a **cross-signal consistency test** — the embedded equivalent of testing that `getUser()` and `getUserProfile()` return the same `userId`. Two different ECUs reporting the same physical reality must agree within tolerance. If they don't, either the DBC is wrong (Day 8's stale DBC) or there's a translation bug in the gateway.

---

## 🧠 Concept: Timers — Periodic and One-Shot

Timers are how CAPL drives periodic behavior without blocking. There are two patterns:

### Pattern 1: Periodic Heartbeat (re-arm each time)

```capl
variables {
  msTimer periodicTimer;
  int     tickCount = 0;
}

on start {
  setTimer(periodicTimer, 100);   // fire after 100ms
}

on timer periodicTimer {
  tickCount++;
  write("Tick #%d at %.3f ms", tickCount, timeNow() / 10000.0);
  setTimer(periodicTimer, 100);  // re-arm → effectively periodic
}
```

### Pattern 2: One-Shot Watchdog (timeout detection)

```capl
variables {
  msTimer watchdogTimer;
  int     lastFrameCount = 0;
  int     framesSinceLastCheck = 0;
}

on message EngineData {
  framesSinceLastCheck++;
  // Reset the watchdog each time a message arrives
  cancelTimer(watchdogTimer);
  setTimer(watchdogTimer, 25);   // if no message for 25ms → timeout (Day 4: 2.5× cycle)
}

on timer watchdogTimer {
  // This fires ONLY if no EngineData arrived in 25ms
  write("🚨 TIMEOUT: EngineData missing for 25ms! (expected every 10ms)");
}
```

> 🌉 **From your world:** The watchdog timer pattern is *exactly* how you implement API response timeouts in async test code — `Promise.race([fetch(url), timeout(5000)])`. If the real event wins, cancel the timeout. If the timer fires first, it means the real event never arrived. This is the CAN equivalent, implemented in hardware-accurate milliseconds. The Day 4 "dropout detection" logic from `timing_analyzer.py` is this exact pattern, now running live in CAPL.

---

## 🧠 Concept: Test Nodes — Writing Formal Test Cases

### The Jest/Mocha Parallel — Finally Explicit

A **CAPL Test Module** (the special test node type in CANoe) adds a formal `describe/it/expect` structure to CAPL:

```capl
/*
 * CAPL Test Module — EngineRPM Validation Suite
 * This is a TEST MODULE, not a simulation node.
 * It has access to: testCase(), testStep(), testStepPass(), testStepFail()
 */

variables {
  message EngineData rxEngineData;
  float   receivedRPM = 0.0;
  int     messageReceived = 0;
}

// ─────────────────────────────────────────────────────────────────
// TEST CASE 1: Verify EngineRPM is within spec at idle
// ─────────────────────────────────────────────────────────────────
testCase "EngineRPM_IdleRange" {
  // ── Arrange ──
  float minIdleRPM = 700.0;
  float maxIdleRPM = 900.0;
  messageReceived  = 0;
  float sampledRPM;

  testStep("TC01_SETUP", "Waiting for 5 consecutive EngineData frames at idle");

  // ── Act: wait for 5 frames (using a helper that blocks test execution) ──
  // Note: testWaitForMessage() is CANoe's test-safe blocking wait
  // It does NOT block the measurement engine — only this test sequence
  if (testWaitForMessage(EngineData, 100) == 1) {
    sampledRPM = EngineData.EngineRPM * 0.25;    // decode: scale=0.25, offset=0
  } else {
    testStepFail("TC01_TIMEOUT", "No EngineData received within 100ms timeout");
    stop;
  }

  // ── Assert ──
  if (sampledRPM >= minIdleRPM && sampledRPM <= maxIdleRPM) {
    testStepPass("TC01_RPM_RANGE",
      "EngineRPM = %.1f RPM — within idle spec [%.0f, %.0f]",
      sampledRPM, minIdleRPM, maxIdleRPM);
  } else {
    testStepFail("TC01_RPM_RANGE",
      "EngineRPM = %.1f RPM — OUTSIDE idle spec [%.0f, %.0f]!",
      sampledRPM, minIdleRPM, maxIdleRPM);
  }
}

// ─────────────────────────────────────────────────────────────────
// TEST CASE 2: Verify EngineRPM cycle time (Day 4!)
// ─────────────────────────────────────────────────────────────────
testCase "EngineRPM_CycleTime" {
  float timestamps[10];
  float intervals[9];
  int   i;
  float maxJitter = 0.0;
  float avgInterval;
  float sumIntervals = 0.0;

  testStep("TC02_SETUP", "Collecting 10 consecutive EngineData timestamps");

  // Collect 10 frame timestamps
  for (i = 0; i < 10; i++) {
    if (testWaitForMessage(EngineData, 50) == 1) {
      timestamps[i] = EngineData.time;
    } else {
      testStepFail("TC02_TIMEOUT", "Frame %d of 10 not received within 50ms", i+1);
      stop;
    }
  }

  // Compute intervals (Day 4 timing analysis in CAPL)
  for (i = 0; i < 9; i++) {
    intervals[i] = timestamps[i+1] - timestamps[i];
    sumIntervals += intervals[i];
    float dev = intervals[i] - 10.0;
    if (dev < 0) dev = -dev;
    if (dev > maxJitter) maxJitter = dev;
  }
  avgInterval = sumIntervals / 9.0;

  // Assert average cycle time
  if (avgInterval >= 9.0 && avgInterval <= 11.0) {
    testStepPass("TC02_AVG_CYCLE",
      "Average cycle time = %.3f ms (spec: 10ms ±1ms)", avgInterval);
  } else {
    testStepFail("TC02_AVG_CYCLE",
      "Average cycle time = %.3f ms OUTSIDE spec!", avgInterval);
  }

  // Assert jitter (no single interval outside ±1ms)
  if (maxJitter <= 1.0) {
    testStepPass("TC02_JITTER",
      "Max jitter = %.3f ms (spec: ≤1ms)", maxJitter);
  } else {
    testStepFail("TC02_JITTER",
      "Max jitter = %.3f ms EXCEEDS spec (≤1ms)!", maxJitter);
  }
}

// ─────────────────────────────────────────────────────────────────
// TEST CASE 3: Fault injection — verify ECU handles overheat signal
// ─────────────────────────────────────────────────────────────────
testCase "CoolantTemp_OverheatHandling" {
  message EngineData faultFrame;
  int     emergencyShutdownReceived = 0;

  testStep("TC03_INJECT", "Injecting overheat: CoolantTemp = 0xFF (215°C)");

  // Inject fault — send a frame with max CoolantTemp
  faultFrame.EngineRPM   = 3000 / 0.25;    // 3000 RPM in raw
  faultFrame.CoolantTemp = 0xFF;            // 215°C — over spec limit
  faultFrame.ThrottlePos = 125;             // 50% throttle raw
  output(faultFrame);

  // Wait for ECU's emergency response message (should send EngineShutdown cmd)
  if (testWaitForMessage(EngineControl, 200) == 1) {
    if (EngineControl.ShutdownCmd == 1) {
      testStepPass("TC03_SHUTDOWN_CMD",
        "ECU correctly issued shutdown command within 200ms of overheat");
    } else {
      testStepFail("TC03_SHUTDOWN_CMD",
        "ShutdownCmd not set — ECU did not respond to overheat!");
    }
  } else {
    testStepFail("TC03_TIMEOUT",
      "No EngineControl message received within 200ms of overheat injection");
  }
}
```

> 🌉 **The exact software testing parallel:**

| CAPL Test Module | Jest/Mocha/Playwright |
|---|---|
| `testCase "TC_Name" { }` | `it('TC_Name', async () => { })` |
| `testStep("id", "description")` | A descriptive comment / `test.step()` in Playwright |
| `testStepPass("id", "message")` | `expect(value).toBe(expected)` — asserts green |
| `testStepFail("id", "message")` | `expect().toFail()` / `throw new Error(message)` |
| `testWaitForMessage(Msg, 100)` | `await page.waitForResponse(url, {timeout: 100})` |
| `stop;` | `test.skip()` / early return on critical failure |
| HTML test report generated | Playwright HTML report / Jest JUnit XML |

> The structure is *identical* to what you've written thousands of times. The only differences are: you're waiting for CAN frames instead of HTTP responses, and the assertions check signal values instead of JSON fields.

---

## 🧩 The Big Picture: CAPL in the CANoe Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    CANoe + CAPL Architecture                    │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                   CANoe Measurement                       │  │
│  │                                                           │  │
│  │  ┌────────────┐  ┌────────────┐  ┌────────────────────┐  │  │
│  │  │ Simulation │  │ Stimulation│  │   TEST MODULE      │  │  │
│  │  │   Node     │  │   Node     │  │   (CAPL)           │  │  │
│  │  │  (CAPL)    │  │  (CAPL)    │  │                    │  │  │
│  │  │ "Virtual   │  │ "Drives    │  │ testCase TC_001 { }│  │  │
│  │  │  Engine    │  │  the real  │  │ testStepPass(...)  │  │  │
│  │  │  ECU sim"  │  │  ECU"      │  │ testStepFail(...)  │  │  │
│  │  └─────┬──────┘  └─────┬──────┘  └────────┬───────────┘  │  │
│  │        │               │                   │               │  │
│  │  ──────┴───────────────┴───────────────────┴──────────    │  │
│  │                   Internal Virtual CAN Bus                 │  │
│  │  ──────────────────────────┬──────────────────────────    │  │
│  │                            │                               │  │
│  │                  Vector Hardware Interface                  │  │
│  │                  (or Virtual channel)                      │  │
│  └──────────────────────────────────────────────────────────┘  │
│                            │                                    │
│               Real CAN Bus ◄──── Physical ECU Under Test        │
│                                                                 │
│  DBC Files ──────────────► All nodes and test modules          │
│  (from Day 8)               reference the same DBC             │
└─────────────────────────────────────────────────────────────────┘
```

> **Your complete Day 1–9 knowledge stack, now mapped to this architecture:**
> - Day 1: ECU = Physical ECU Under Test
> - Day 2: DBC = the arrow feeding all CAPL nodes
> - Day 3: Error handling = `on errorFrame`, `on busOff` handlers
> - Day 4: Cycle time = `setTimer(10)` periodic sends + cycle-time assertions
> - Day 5: Physical = what the Vector hardware interface manages for you
> - Day 6: Bit timing = configured once in CANoe channel settings
> - Day 7: CAN FD = `message.fd = 1`, `fd=TRUE` channel option
> - Day 8: Signals, mux, attributes = `this.EngineRPM`, `this.MuxMode`
> - Day 9: CAPL = the test automation language that ties it all together

---

## 🌍 Where It's Used in the Real World

### 🚗 Automotive
- **OEM-level regression testing:** Hundreds of CAPL test cases run overnight on every new ECU firmware build. A typical powertrain test suite has 500–2000 CAPL test cases covering signal ranges, timing, fault responses, and startup sequences.
- **Supplier acceptance testing:** When a Tier-1 delivers a new ECU version, the OEM runs a standardized CAPL test suite (delivered as a `.can` test module). Pass = accepted. Fail = supplier gets a defect report with exact test step and signal trace.
- **HIL validation:** The test sequences you write in CAPL against a virtual bus (simulation node) are often the *exact* same scripts run on a Hardware-in-the-Loop rig connected to real ECUs. The investment is portable.

### 🏥 Medical Devices
- While medical devices more commonly use Python-based test frameworks (pytest + python-can) for regulatory evidence, companies that do use CANoe for surgical robot validation generate IEC 62304-compliant test evidence directly from CAPL test module reports — the HTML/XML output feeds directly into the documentation trail.

### 🏠 Smart Home / Industrial
- Industrial automation test labs use CAPL to validate CANopen node behavior — startup sequences, PDO (Process Data Object) timing, and emergency message handling. The same CAPL patterns apply even though the application layer is CANopen rather than automotive-specific.

---

## 🔬 How a Tester Thinks About It

> CAPL is not a new paradigm — it's event-driven test automation that you already know, running in a real-time bus monitoring context. Your challenge is learning one new tool, not learning new testing concepts.

```
┌──────────────────────────────────────────────────────────────┐
│           TEST SCENARIOS BY CAPL HANDLER TYPE                │
├──────────────────────────────────────────────────────────────┤
│ on start / on stopMeasurement                                │
│  → Suite setup/teardown: initialize counters, log headers,  │
│    print summary, set initial signal values                  │
│                                                              │
│ on message <MessageName>                                     │
│  → Signal range checks (min/max from DBC)                   │
│  → Cycle time measurement (compare with GenMsgCycleTime)     │
│  → Jitter tracking (Day 4 in real-time)                     │
│  → Cross-signal correlation (RPM from Engine vs Dashboard)  │
│  → Mux value tracking (which MuxMode values observed?)      │
│                                                              │
│ on timer                                                     │
│  → Periodic stimulus (simulate a missing ECU)               │
│  → Timeout/watchdog (dropout detection)                     │
│  → Scheduled fault injection (inject at t=5s, t=30s)        │
│                                                              │
│ on errorFrame                                                │
│  → Count CAN errors, correlate with bus load / fault inject │
│  → Trigger test failure if error rate exceeds threshold     │
│                                                              │
│ testCase { }  (Test Module)                                  │
│  → Structured test sequences: arrange → act → assert        │
│  → Generates formal test report for audit trail             │
│  → Parameterized test data (loop over test vectors)         │
└──────────────────────────────────────────────────────────────┘
```

> **From your world — the final comprehensive mapping:**

| Software Testing Concept | CAPL Equivalent |
|---|---|
| `beforeAll()` / `beforeEach()` | `on start` / test case setup block |
| `afterAll()` / `afterEach()` | `on stopMeasurement` / teardown |
| `page.on('response', handler)` | `on message MessageName { }` |
| `expect(value).toBe(expected)` | `testStepPass/Fail(id, msg)` |
| `await page.waitForResponse(url)` | `testWaitForMessage(Msg, timeout_ms)` |
| Fault injection / chaos monkey | `on key 'f'` + `output(faultMsg)` |
| Periodic heartbeat simulation | `on timer { output(msg); setTimer(t, 10); }` |
| Timeout assertion | `cancelTimer(wd); setTimer(wd, 25);` |
| HTML test report | CANoe test module HTML/XML report |
| CI/CD test runner | CANoe automation interface (CANoe.Application COM) |

---

## 🛠️ Hands-On Exercise: CAPL Signal Validator (python-can edition)

> **Note:** CAPL runs inside CANoe, which requires a Vector license. For this exercise, we replicate the *exact same logic patterns* in Python using `python-can` — so you can run and understand CAPL's behavioral model on your laptop today. When you sit down at a CANoe workstation, the code structure transfers 1:1.

We'll build a Python class that mirrors CAPL's event-driven architecture: `on_start`, `on_message`, `on_timer`, and `test_case` — proving that the *patterns* are language-agnostic.

### Step 1: Setup

```bash
pip install python-can cantools
```

### Step 2: Create the DBC

Reuse `vehicle_full.dbc` from Day 8 (copy it to the Day 9 folder), or recreate:

```
VERSION ""
NS_ :
BS_:
BU_: ECU_Engine ECU_ABS ECU_Dashboard

BO_ 201 EngineData: 8 ECU_Engine
 SG_ EngineRPM    : 0|16@1+ (0.25,0) [0|16383.75] "RPM" ECU_Dashboard
 SG_ CoolantTemp  : 16|8@1+ (1,-40) [-40|215] "degC" ECU_Dashboard
 SG_ ThrottlePos  : 24|8@1+ (0.4,0) [0|100] "%" ECU_Dashboard

BO_ 400 WheelSpeed: 8 ECU_ABS
 SG_ SpeedFL : 0|16@1+  (0.01,0) [0|655.35] "km/h" ECU_Dashboard
 SG_ SpeedFR : 16|16@1+ (0.01,0) [0|655.35] "km/h" ECU_Dashboard

BA_DEF_ BO_ "GenMsgCycleTime" INT 0 10000;
BA_ "GenMsgCycleTime" BO_ 201 10;
BA_ "GenMsgCycleTime" BO_ 400 10;
```

### Step 3: Save this as `capl_patterns.py`

```python
"""
Day 9 — CAPL Patterns in Python
Implements CAPL's event-driven architecture (on_start, on_message,
on_timer, testCase) using python-can + threading.
Side-by-side CAPL comments show the exact translation to real CAPL.
"""

import can
import cantools
import threading
import time
import struct
from dataclasses import dataclass, field
from typing import Callable, Dict, List

DB = cantools.database.load_file('vehicle_full.dbc')


# ============================================================
# PART 1: THE CAPL NODE BASE CLASS
# — mirrors CANoe's node lifecycle
# ============================================================

class CAPLNode:
    """
    Base class for all CAPL-style nodes.
    Mirrors the CAPL node lifecycle:
      on_start()        → on start { }
      on_message()      → on message <id> { }
      on_stop()         → on stopMeasurement { }
    """

    def __init__(self, channel='test_channel'):
        self.bus = can.interface.Bus(interface='virtual', channel=channel)
        self._running = False
        self._rx_thread = None
        self._timers: Dict[str, threading.Timer] = {}
        self._pass_count = 0
        self._fail_count = 0

    # ── Lifecycle ──────────────────────────────────────────
    def start(self):
        """Equivalent to CANoe measurement start."""
        self._running = True
        self._rx_thread = threading.Thread(target=self._rx_loop, daemon=True)
        self._rx_thread.start()
        self.on_start()    # call user hook

    def stop(self):
        """Equivalent to stopMeasurement."""
        self._running = False
        for t in self._timers.values():
            t.cancel()
        self.on_stop()     # call user hook
        self.bus.shutdown()

    def on_start(self):
        """Override — called when measurement starts (on start in CAPL)."""
        pass

    def on_stop(self):
        """Override — called when measurement stops (on stopMeasurement in CAPL)."""
        pass

    # ── Message dispatch ───────────────────────────────────
    def _rx_loop(self):
        while self._running:
            msg = self.bus.recv(timeout=0.1)
            if msg:
                self.on_message(msg)

    def on_message(self, msg: can.Message):
        """Override — called for every received message (on message * in CAPL)."""
        pass

    # ── Output (send) ──────────────────────────────────────
    def output(self, msg: can.Message):
        """CAPL output() — transmit a message onto the bus."""
        self.bus.send(msg)

    # ── Timer support ──────────────────────────────────────
    def set_timer(self, name: str, interval_ms: float, callback: Callable):
        """CAPL setTimer() — one-shot timer. Re-arm in callback for periodic."""
        self.cancel_timer(name)
        t = threading.Timer(interval_ms / 1000.0, callback)
        self._timers[name] = t
        t.start()

    def cancel_timer(self, name: str):
        """CAPL cancelTimer()."""
        if name in self._timers:
            self._timers[name].cancel()
            del self._timers[name]

    # ── Test assertions ────────────────────────────────────
    def test_step_pass(self, step_id: str, message: str, *args):
        """CAPL testStepPass()."""
        self._pass_count += 1
        print(f"  ✅ [{step_id}] {message % args if args else message}")

    def test_step_fail(self, step_id: str, message: str, *args):
        """CAPL testStepFail()."""
        self._fail_count += 1
        print(f"  ❌ [{step_id}] {message % args if args else message}")

    def write(self, message: str, *args):
        """CAPL write() — console log."""
        print(f"  📝 {message % args if args else message}")


# ============================================================
# PART 2: SIMULATION NODE — emulates a virtual Engine ECU
# (CAPL role: simulation node / on timer periodic sender)
# ============================================================

class EngineSimNode(CAPLNode):
    """
    CAPL equivalent:
      variables { message EngineData txMsg; msTimer sendTimer; }
      on start { setTimer(sendTimer, 10); }
      on timer sendTimer { output(txMsg); setTimer(sendTimer, 10); }
    """

    def __init__(self, channel='test_channel', rpm=2000.0, temp_deg_c=85.0):
        super().__init__(channel)
        self.rpm     = rpm
        self.temp    = temp_deg_c
        self.throttle = 40.0   # 40%

    def on_start(self):
        self.write("Engine sim node started. RPM=%.0f Temp=%.0f°C", self.rpm, self.temp)
        self._send_engine_data()

    def _send_engine_data(self):
        if not self._running:
            return
        msg_def = DB.get_message_by_name('EngineData')

        # Encode physical values → raw bytes (Day 8 decode formula reversed)
        data = msg_def.encode({
            'EngineRPM':   self.rpm,
            'CoolantTemp': self.temp,
            'ThrottlePos': self.throttle,
        })
        frame = can.Message(
            arbitration_id=msg_def.frame_id,
            data=data,
            is_extended_id=False
        )
        self.output(frame)
        # Re-arm timer → periodic (CAPL: setTimer(sendTimer, 10))
        self.set_timer('send', 10, self._send_engine_data)


# ============================================================
# PART 3: MONITORING NODE — real-time signal range + cycle time checks
# (CAPL role: monitoring node)
# ============================================================

class EngineMonitorNode(CAPLNode):
    """
    CAPL equivalent:
      on message EngineData {
        if (this.EngineRPM > 16383.75) { write("OUT OF RANGE"); }
        // cycle time check ...
      }
    """

    def __init__(self, channel='test_channel'):
        super().__init__(channel)
        self.last_ts: Dict[int, float] = {}
        self.cycle_violations = 0
        self.range_violations = 0
        self.frame_counts: Dict[int, int] = {}

    def on_start(self):
        self.write("Monitor node started.")

    def on_message(self, msg: can.Message):
        now = time.time() * 1000.0   # ms

        # ── Cycle time check (Day 4 logic, real-time) ──
        if msg.arbitration_id in self.last_ts:
            interval = now - self.last_ts[msg.arbitration_id]
            try:
                db_msg = DB.get_message_by_frame_id(msg.arbitration_id)
                cycle_attr = db_msg.dbc.attributes.get('GenMsgCycleTime')
                expected_ms = cycle_attr.value if cycle_attr and hasattr(cycle_attr, 'value') else None
                if expected_ms and (interval < expected_ms * 0.8 or interval > expected_ms * 1.2):
                    self.cycle_violations += 1
                    self.write("⚠️  CycleTime: ID=0x%03X interval=%.1fms expected=%dms",
                               msg.arbitration_id, interval, expected_ms)
            except Exception:
                pass

        self.last_ts[msg.arbitration_id] = now
        self.frame_counts[msg.arbitration_id] = self.frame_counts.get(msg.arbitration_id, 0) + 1

        # ── Signal range check ──
        try:
            db_msg = DB.get_message_by_frame_id(msg.arbitration_id)
            decoded = db_msg.decode(msg.data)
            for sig_name, value in decoded.items():
                if isinstance(value, (int, float)):
                    sig = next((s for s in db_msg.signals if s.name == sig_name), None)
                    if sig and sig.minimum is not None and sig.maximum is not None:
                        if value < sig.minimum or value > sig.maximum:
                            self.range_violations += 1
                            self.write("❌ RANGE: %s.%s = %.3f outside [%.3f, %.3f]",
                                       db_msg.name, sig_name, value, sig.minimum, sig.maximum)
        except Exception:
            pass

    def on_stop(self):
        self.write("═══ Monitor Summary ═══")
        for msg_id, count in self.frame_counts.items():
            self.write("  ID=0x%03X: %d frames received", msg_id, count)
        self.write("Cycle violations : %d", self.cycle_violations)
        self.write("Range violations : %d", self.range_violations)


# ============================================================
# PART 4: TEST NODE — structured test cases with pass/fail
# (CAPL role: Test Module)
# ============================================================

class EngineTestNode(CAPLNode):
    """
    CAPL equivalent:
      testCase "EngineRPM_Range" { testWaitForMessage(); testStepPass/Fail(); }
    """

    def __init__(self, channel='test_channel'):
        super().__init__(channel)
        self._received_msgs: Dict[int, List[can.Message]] = {}
        self._msg_events: Dict[int, threading.Event] = {}

    def on_message(self, msg: can.Message):
        mid = msg.arbitration_id
        if mid not in self._received_msgs:
            self._received_msgs[mid] = []
        self._received_msgs[mid].append(msg)
        if mid in self._msg_events:
            self._msg_events[mid].set()

    def wait_for_message(self, msg_name: str, timeout_ms: float) -> can.Message:
        """
        CAPL testWaitForMessage() equivalent.
        Blocks test execution (NOT the measurement engine) until
        a matching message arrives or timeout expires.
        """
        db_msg = DB.get_message_by_name(msg_name)
        mid = db_msg.frame_id
        evt = threading.Event()
        self._msg_events[mid] = evt
        arrived = evt.wait(timeout=timeout_ms / 1000.0)
        del self._msg_events[mid]
        if arrived and self._received_msgs.get(mid):
            return self._received_msgs[mid][-1]
        return None

    def run_test_cases(self):
        """Run all test cases sequentially."""
        print(f"\n{'='*60}")
        print(f"🧪 CAPL TEST MODULE: Engine ECU Validation")
        print(f"{'='*60}")
        self._tc_rpm_range()
        self._tc_cycle_time()
        self._tc_coolant_temp_encoding()
        self._print_summary()

    # ── Test Case 1: RPM range check ──────────────────────
    def _tc_rpm_range(self):
        print(f"\n  testCase: EngineRPM_Range")
        msg = self.wait_for_message('EngineData', timeout_ms=500)
        if msg is None:
            self.test_step_fail("TC01_RECV", "No EngineData received within 500ms")
            return

        decoded = DB.get_message_by_name('EngineData').decode(msg.data)
        rpm = decoded['EngineRPM']

        if 0.0 <= rpm <= 16383.75:
            self.test_step_pass("TC01_RPM_RANGE",
                "EngineRPM = %.2f RPM — within spec [0, 16383.75]", rpm)
        else:
            self.test_step_fail("TC01_RPM_RANGE",
                "EngineRPM = %.2f RPM — OUTSIDE spec!", rpm)

    # ── Test Case 2: Cycle time check ─────────────────────
    def _tc_cycle_time(self):
        print(f"\n  testCase: EngineData_CycleTime")
        mid = DB.get_message_by_name('EngineData').frame_id
        timestamps = []

        for _ in range(5):
            msg = self.wait_for_message('EngineData', timeout_ms=100)
            if msg:
                timestamps.append(time.time() * 1000.0)

        if len(timestamps) < 2:
            self.test_step_fail("TC02_RECV", "Insufficient frames for cycle time analysis")
            return

        intervals = [timestamps[i+1] - timestamps[i] for i in range(len(timestamps)-1)]
        avg = sum(intervals) / len(intervals)
        max_dev = max(abs(iv - 10.0) for iv in intervals)

        if 9.0 <= avg <= 11.0:
            self.test_step_pass("TC02_AVG", "Avg cycle time = %.2fms (spec: 10ms ±1ms)", avg)
        else:
            self.test_step_fail("TC02_AVG", "Avg cycle time = %.2fms OUTSIDE spec!", avg)

        if max_dev <= 2.0:   # relaxed for virtual bus timing
            self.test_step_pass("TC02_JITTER", "Max jitter = %.2fms (spec: ≤2ms)", max_dev)
        else:
            self.test_step_fail("TC02_JITTER", "Max jitter = %.2fms EXCEEDS spec!", max_dev)

    # ── Test Case 3: CoolantTemp encoding spot-check ──────
    def _tc_coolant_temp_encoding(self):
        print(f"\n  testCase: CoolantTemp_Encoding")
        msg = self.wait_for_message('EngineData', timeout_ms=500)
        if msg is None:
            self.test_step_fail("TC03_RECV", "No EngineData received")
            return

        decoded = DB.get_message_by_name('EngineData').decode(msg.data)
        temp = decoded['CoolantTemp']
        raw_byte = msg.data[2]   # CoolantTemp is at byte index 2 (start_bit=16)
        manual = raw_byte * 1 + (-40)   # physical = raw × 1 + (-40)

        if abs(temp - manual) < 0.01:
            self.test_step_pass("TC03_ENCODE",
                "CoolantTemp cantools=%.1f°C, manual=%.1f°C — formulas match", temp, manual)
        else:
            self.test_step_fail("TC03_ENCODE",
                "Encoding mismatch: cantools=%.1f vs manual=%.1f", temp, manual)

        if -40.0 <= temp <= 215.0:
            self.test_step_pass("TC03_RANGE",
                "CoolantTemp = %.1f°C — within spec [-40, 215]°C", temp)
        else:
            self.test_step_fail("TC03_RANGE",
                "CoolantTemp = %.1f°C — OUT OF RANGE!", temp)

    def _print_summary(self):
        total = self._pass_count + self._fail_count
        print(f"\n{'='*60}")
        print(f"🎯 TEST SUMMARY")
        print(f"{'='*60}")
        print(f"  Passed : {self._pass_count} / {total}")
        print(f"  Failed : {self._fail_count} / {total}")
        print(f"  {'✅ ALL TESTS PASSED' if self._fail_count == 0 else '❌ FAILURES DETECTED'}")


# ============================================================
# PART 5: ORCHESTRATE — start all nodes on the same virtual bus
# ============================================================

if __name__ == "__main__":
    CHANNEL = 'capl_demo'

    # Start simulation node (virtual Engine ECU)
    engine_sim  = EngineSimNode(channel=CHANNEL, rpm=2000.0, temp_deg_c=85.0)
    monitor     = EngineMonitorNode(channel=CHANNEL)
    test_node   = EngineTestNode(channel=CHANNEL)

    print("Starting CAPL-pattern demo on virtual bus...")
    engine_sim.start()
    monitor.start()
    test_node.start()

    # Let the bus run for 300ms to collect data
    time.sleep(0.3)

    # Run test cases
    test_node.run_test_cases()

    # Collect a bit more for the monitor summary
    time.sleep(0.2)

    # Stop all nodes (on stopMeasurement)
    test_node.stop()
    monitor.stop()
    engine_sim.stop()
```

### Step 4: Run it

```bash
python capl_patterns.py
```

### ✅ Expected Output (abridged)

```
Starting CAPL-pattern demo on virtual bus...
  📝 Engine sim node started. RPM=2000 Temp=85°C
  📝 Monitor node started.

============================================================
🧪 CAPL TEST MODULE: Engine ECU Validation
============================================================

  testCase: EngineRPM_Range
  ✅ [TC01_RPM_RANGE] EngineRPM = 2000.00 RPM — within spec [0, 16383.75]

  testCase: EngineData_CycleTime
  ✅ [TC02_AVG]    Avg cycle time = 10.xx ms (spec: 10ms ±1ms)
  ✅ [TC02_JITTER] Max jitter = x.xx ms (spec: ≤2ms)

  testCase: CoolantTemp_Encoding
  ✅ [TC03_ENCODE] CoolantTemp cantools=85.0°C, manual=85.0°C — formulas match
  ✅ [TC03_RANGE]  CoolantTemp = 85.0°C — within spec [-40, 215]°C

============================================================
🎯 TEST SUMMARY
============================================================
  Passed : 5 / 5
  Failed : 0 / 5
  ✅ ALL TESTS PASSED

  📝 ═══ Monitor Summary ═══
  📝   ID=0x0C9: ~30 frames received
  📝 Cycle violations : 0
  📝 Range violations : 0
```

> 🎉 **The aha moment:** Every class in this Python code maps *exactly* to a CAPL construct. `CAPLNode.on_start()` → `on start { }`. `CAPLNode.set_timer()` → `setTimer()`. `EngineTestNode._tc_rpm_range()` → `testCase "EngineRPM_Range" { }`. `test_step_pass()` → `testStepPass()`. When you open a real CANoe CAPL editor and see these same patterns, your eyes won't glaze — they'll recognize old friends. 🎯

---

## 🎯 Challenge: The ABS Validation Suite

> **Scenario:** You're writing a CAPL test suite for a new ABS (Anti-lock Braking System) ECU. The ABS reads wheel speed from four sensors via CAN and transmits brake-pressure commands back. Your job: design and implement three key test cases.

### Challenge 1 — 🔢 The Wheel Speed Plausibility Check
The ABS ECU should detect and reject implausible wheel speed data. A "plausible" set means: no single wheel should differ from the average by more than 30 km/h (physically impossible due to vehicle dynamics — a wheel at 100 km/h can't be adjacent to one at 5 km/h in normal driving).

```capl
// CAPL hint:
on message WheelSpeed {
  float fl = this.SpeedFL;
  float fr = this.SpeedFR;
  float rl = this.SpeedRL;
  float rr = this.SpeedRR;
  float avg = (fl + fr + rl + rr) / 4.0;
  // TODO: check each wheel vs avg, flag if diff > 30 km/h
}
```

- Implement this in the Python `CAPLNode` pattern, injecting both normal (all ~100 km/h) and fault (one wheel at 5 km/h, others at 100 km/h) WheelSpeed frames.
- Assert: the plausibility check flags the fault case and not the normal case.
- *The question:* The spec says "reject implausible data." But the CAPL handler can't delete the message from the bus. What does "reject" actually mean in a CAN context — and what signal or message should the ABS send back to report the plausibility fault?

### Challenge 2 — ⏱️ The Latency-Under-Load Test (Day 4 Callback)
The ABS ECU must respond to a wheel-lock event (SpeedFL drops to near-zero while others stay at speed) with a brake-pressure command within **15ms**. But your test bus also has 10 other messages running (simulating a busy vehicle bus).

- Build a stimulation node that:
  1. Sends 10 background messages at various rates (simulating a realistic bus load)
  2. At t=2 seconds, injects the wheel-lock event (SpeedFL → 0 km/h)
  3. Starts a 15ms watchdog timer
  4. Listens for the ABS brake-pressure command message

```python
def inject_wheel_lock(self):
    # TODO: build WheelSpeed frame with SpeedFL=0, others=100 km/h
    # TODO: start watchdog timer
    # TODO: on_message for BrakePressure: cancel watchdog, assert latency < 15ms
    pass
```

- *The question:* Without the background traffic, the ABS responds in 3ms. With 70% bus load, it takes 14ms. With 85% load, it takes 17ms and fails the deadline. **Which test catches this — and is 70% load "passing" a comfort or a concern?**

### Challenge 3 — 😈 The Mux-Aware Diagnostic Message Test (Day 8 Callback)
The ABS sends a multiplexed diagnostic message (`DiagData`) with three mux modes:
- `MuxMode=0`: WheelSpeedPlausibility status (one status byte per wheel)
- `MuxMode=1`: BrakePressureStatus (four pressure readings)
- `MuxMode=2`: ABS_ActivationCount (counter since last ignition cycle)

Write a test that:
1. Runs for 30 simulated seconds
2. Asserts every mux value (0, 1, 2) was observed at least once
3. When `MuxMode=0` arrives with any wheel's status ≠ 0, asserts it matches the fault injected in Challenge 1
4. If any undefined mux value arrives, fails with "firmware-DBC mismatch"

- *The killer question:* This test drives from the DBC (the mux definitions are the spec). The ABS ECU's firmware is later updated to add `MuxMode=3` (a new ESC integration status). Before the DBC is updated, what happens to this test — and is that the right behavior?

### Hints
- Challenge 1: "reject" in CAN means the ECU notes the fault internally and transmits a diagnostic message or sets a flag in an existing message — CAN has no "reject frame" mechanism at the application layer.
- Challenge 2: background load of 70% at 500 kbps = ~350,000 bits/sec occupied. A 130-bit frame every 10ms ≈ 13,000 bits/sec per message. Calculate how many simultaneous 10ms messages approach 70%.
- Challenge 3: The "right" behavior for an undefined mux value is debatable — it depends on your test strategy: strict (fail = catch firmware drift early) vs. lenient (warn = avoid false positives). Argue both sides.

---

## ❓ Quiz

### Q1
> You write a CAPL `on message EngineData` handler that includes a `while(true)` loop waiting for a response. What happens to the measurement, and why? What is the correct CAPL pattern to achieve a "wait for X then do Y" behavior?

### Q2
> In a CAPL test module, what is the difference between `testStepPass()` / `testStepFail()` and a regular `write()` call? When would you use each?

### Q3
> You have two CAPL nodes on the same CANoe network:
> - **Node A** (simulation): sends `EngineData` every 10ms with `EngineRPM = 2000`
> - **Node B** (test): has `on message EngineData { this.EngineRPM = 5000; }` — trying to change the RPM
>
> Does Node B's assignment change what Node A transmits? Does it change what other nodes receive? What is the correct way to achieve Node B's intent?

---

### ✅ Answer 1
**The `while(true)` loop freezes the entire measurement engine.** CAPL runs event handlers cooperatively — while one handler is executing, no other handler can fire. An infinite loop in an `on message` handler means:
- All other `on message` handlers are permanently blocked
- Timer callbacks never fire
- `on errorFrame` and `on busOff` never trigger
- The CANoe GUI becomes unresponsive
- The measurement must be killed externally

The correct CAPL pattern for "wait for X then do Y":

```capl
// ❌ WRONG — blocks forever:
on message EngineData {
  while (myFlag == 0) { }   // freezes everything!
  doSomething();
}

// ✅ CORRECT — use a state flag + timer:
variables { int waitingForX = 0; msTimer watchdog; }

on message TriggerMessage {
  waitingForX = 1;
  setTimer(watchdog, 200);   // 200ms timeout
}

on message ResponseMessage {
  if (waitingForX) {
    cancelTimer(watchdog);
    waitingForX = 0;
    doSomething();    // "Y" executes here
  }
}

on timer watchdog {
  waitingForX = 0;
  write("Timeout: ResponseMessage not received!");
}

// ✅ ALSO CORRECT in test modules — testWaitForMessage() is test-safe:
testCase "WaitExample" {
  if (testWaitForMessage(ResponseMessage, 200) == 1) {
    testStepPass("WAIT_OK", "Response received");
  } else {
    testStepFail("WAIT_TIMEOUT", "No response in 200ms");
  }
}
```

> 🌉 **The Node.js parallel:** You already know never to block the Node.js event loop with `while(Date.now() < deadline) {}` — you use `setImmediate()`, `Promise`, or `await`. CAPL's `setTimer()` + flag pattern is the exact same solution: non-blocking state machine instead of a blocking wait.

### ✅ Answer 2
| | `testStepPass()` / `testStepFail()` | `write()` |
|---|---|---|
| **Purpose** | Formal test assertion — records a test outcome | Diagnostic logging — appears in Write window only |
| **Test report** | ✅ Appears in HTML/XML test report | ❌ Does NOT appear in test report |
| **Verdict contribution** | ✅ Contributes to overall PASS/FAIL verdict | ❌ No impact on verdict |
| **Requirement traceability** | ✅ Step ID links to a requirement | ❌ Just a string |
| **When to use** | Every assertion that has a spec requirement | Debug info, trace logging, intermediate values |

**Use `testStepPass/Fail` for everything that has a testable requirement** — a signal range check, a cycle time assertion, a fault response validation. Use `write()` for "I observed X" informational messages that help debug failures but don't constitute a pass/fail verdict themselves.

> 💡 The practical rule: if you'd write it as `expect(...)` in Jest, it should be `testStepPass/Fail` in CAPL. If you'd write it as `console.log(...)`, it should be `write()`.

### ✅ Answer 3
**No — Node B's assignment `this.EngineRPM = 5000` does NOT change what Node A transmits or what other nodes receive.**

In CAPL, the `this` object inside an `on message` handler is a **local copy** of the received message, not a reference to the live bus. Assigning to `this.EngineRPM` only changes the local copy inside that handler — it has **no effect** on the bus, on Node A's transmit buffer, or on any other node's receive buffer.

This is the CAPL equivalent of reassigning a function parameter — it doesn't mutate the caller's variable.

**The correct approaches to achieve "change what the bus sees for EngineRPM":**

```capl
// Option 1: Node B sends its OWN EngineData frame with RPM=5000
// (works if Node B is a simulation node — but creates two senders!)
variables { message EngineData override; }
on message EngineData {
  override = this;           // copy the received message
  override.EngineRPM = 5000 / 0.25;   // set desired RPM raw value
  output(override);          // transmit the modified version
  // ⚠️ Now BOTH Node A AND Node B send EngineData — potential collision
}

// Option 2 (correct HIL approach): Use CANoe's signal manipulation
// or environment variables to tell Node A (simulation) to change its
// transmitted RPM value — single source of truth on the bus.

// Option 3: Replace Node A entirely — make Node B the only sender,
// and have it transmit the desired value from the start.
```

> 🎯 **The embedded lesson:** Unlike test doubles in software (where you can swap out an implementation), CAN bus frames are immutable once received. To change what's on the bus, you must control the *sender*. This is why the HIL architecture has a dedicated **simulation node** for each ECU — you control the source, not the echo.

---

## 🎓 Key Takeaways

- 🎭 **CAPL is event-driven test automation that you already know.** `on start` = `beforeAll`, `on message` = network response listener, `testStepPass/Fail` = expect assertion, `setTimer` = setTimeout. The patterns are identical to Playwright/Cypress — the domain is CAN buses, not browsers.
- ⚡ **Never block a CAPL handler.** Blocking the event loop kills all other handlers. Use `setTimer()` + state flags for "wait for X then do Y" — the same reflex as never blocking Node.js with synchronous sleeps.
- 📨 **`on message` fires for every frame of that type** — it's a real-time signal interceptor. Your Day 4 timing analysis and Day 8 signal validation logic run live, frame by frame, not as post-processing.
- 🔬 **`this` in a handler is a local copy** — assigning to `this.SignalName` changes nothing on the bus. To change bus content, control the *transmitting* node.
- 🧪 **CAPL Test Modules = Playwright test files.** `testCase { }` = `it()`, `testWaitForMessage()` = `waitForResponse()`, `testStepPass/Fail()` = assertions, HTML report = Playwright report. The cognitive load of learning CAPL is zero if you already know Playwright.
- 🏗️ **The four node roles map to your existing testing vocabulary:** simulation = mock/stub, stimulation = test driver, monitoring = passive assertion, test module = test file.
- 🚗 **CAPL is the industry standard.** Every major OEM and Tier-1 requires it. Your Python/pytest knowledge is the foundation — CAPL is the automotive-industry syntax layer on top.


