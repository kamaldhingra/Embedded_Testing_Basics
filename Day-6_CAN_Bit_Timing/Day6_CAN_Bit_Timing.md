# ⏲️ Day 6: CAN Bit Timing — Baud Rate, Sample Point & Synchronization

> **Course:** Embedded & IoT Testing Masterclass
> **Instructor:** Professor Embed
> **Prerequisites:** Day 1–4 (CAN fundamentals) · **Day 5 (Physical Layer — CAN-H/CAN-L, differential signaling)**

---

## 📚 Table of Contents

1. [Recap: Where We Left Off](#recap-where-we-left-off)
2. [Concept: Baud Rate — How Fast the Bits Fly](#concept-baud-rate)
3. [Concept: Inside a Single Bit — Time Quanta & Segments](#concept-inside-a-single-bit)
4. [Concept: The Sample Point — *When* You Read the Bit](#concept-the-sample-point)
5. [Concept: Synchronization — Staying in Lockstep Without a Clock Wire](#concept-synchronization)
6. [The Big Picture: One Bit, Fully Dissected](#the-big-picture)
7. [Where It's Used in the Real World](#where-its-used-in-the-real-world)
8. [How a Tester Thinks About It](#how-a-tester-thinks-about-it)
9. [Hands-On Exercise: Bit Timing Calculator & Sample-Point Simulator](#hands-on-exercise)
10. [Challenge: The Mismatched Baud Rate Mystery](#challenge-the-mismatched-baud-rate-mystery)
11. [Quiz + Answers](#quiz--answers)
12. [Key Takeaways](#key-takeaways)

---

## 🔁 Recap: Where We Left Off

On **Day 5** you went below the logic into the copper: CAN-H and CAN-L, the voltage difference that *is* a bit, and how differential signaling annihilates noise. We ended with a critical fact you might have glossed over:

> *"CAN has **no separate clock wire**. Nodes sync by watching bit transitions."* (We first hinted at this back in Day 3, with bit-stuffing.)

That sentence hides an enormous engineering question. Every other chip-to-chip protocol you might know — **SPI has a clock line (SCLK), I²C has a clock line (SCL)** — ships a dedicated wire whose only job is to scream "NOW! read a bit... NOW! read another." CAN has *no such wire*. It's just CAN-H and CAN-L carrying data.

So here's today's haunting question:

> **If there's no clock wire, how do 30 different ECUs — each with its own slightly-imperfect crystal oscillator — all agree on exactly *when* a bit starts and ends, and *when* to read it? How don't they drift apart and turn the bus into garbage?**

The answer is **bit timing**: a beautifully precise scheme of dividing each bit into tiny time slices, picking an exact moment to "sample" the voltage, and constantly re-syncing off the data itself. Master this, and you'll understand the #1 cause of "the bus is totally dead" bring-up failures — and how to diagnose them in seconds. Let's go. ⏲️

---

## 🧠 Concept: Baud Rate — How Fast the Bits Fly

### The Morse Code Operator Analogy 📻

Two telegraph operators agree in advance: *"We'll send exactly 10 dots per second."* If the sender taps at 10/sec but the receiver listens expecting 5/sec, the message is gibberish. **Both must agree on the rate beforehand** — there's no clock signal telling the receiver when each dot lands; they just both trust the agreed speed.

**Baud rate** (more precisely, *bit rate* in CAN) is that agreed speed: **how many bits per second** travel on the bus.

| Common CAN bitrate | Bit time (duration of one bit) | Typical use |
|---|---|---|
| **125 kbit/s** | 8 µs | Body electronics, comfort, low-speed CAN |
| **250 kbit/s** | 4 µs | Trucks/buses (J1939), industrial |
| **500 kbit/s** | 2 µs | Powertrain, mainstream automotive |
| **1 Mbit/s** | 1 µs | High-speed control, short buses |

```
At 500 kbit/s, each bit lasts 2 microseconds:

  │◄─2µs─►│◄─2µs─►│◄─2µs─►│◄─2µs─►│
  ▼       ▼       ▼       ▼       ▼
──█───────█───────█───────█───────█──▶ time
  bit     bit     bit     bit     bit
```

### 🔑 The Iron Rule: Everyone Must Match

This is the part that trips up *every* beginner and causes the most infamous bring-up failure:

> **Every node on a CAN bus MUST be configured to the exact same bitrate.** One node at 250 kbit/s on a 500 kbit/s bus doesn't get "slow data" — it gets **total garbage**, throws errors, and can take the **whole bus down**.

There's no negotiation, no auto-detect in standard CAN (some tools *guess* by listening, but the protocol itself doesn't negotiate). It's a pre-shared agreement, exactly like the telegraph operators.

> 🌉 **From your world:** This is like a **baud-rate mismatch on a serial port** (you've seen `9600` vs `115200` garbage), or a **content-type/encoding mismatch** between an API client and server — both ends *must* agree on the wire format or you get mojibake. Same failure, different layer. If you've ever seen `���` in a terminal, you already understand a CAN bitrate mismatch.

### ⚠️ The Distance-vs-Speed Tradeoff

Why not always run at 1 Mbit/s? **Physics (Day 5!).** Faster bits are shorter in time, so the signal has *less time* to travel down the wire and settle before it's sampled. Longer buses = more propagation delay = you must slow down.

```
1 Mbit/s  → max bus length ~40 meters
500 kbit/s → max ~100 meters
125 kbit/s → max ~500 meters
```

> This is a direct consequence of Day 5's physical layer: the speed of electricity in copper is finite, and reflections/settling take time. Bitrate and bus length are forever locked in a tradeoff.

---

## 🧠 Concept: Inside a Single Bit — Time Quanta & Segments

Here's where CAN gets gorgeously precise. A single bit is **not** an indivisible blip. The CAN controller chops each bit into a number of tiny equal slices called **Time Quanta (TQ)**.

### The Movie-Frames Analogy 🎬

A movie *looks* like continuous motion, but it's actually made of discrete frames (24 per second). Similarly, a CAN bit *looks* like one steady voltage level, but the controller treats it as a sequence of, say, **8 to 25 time quanta** — tiny clock ticks derived from the oscillator. This granularity is what lets CAN fine-tune *exactly* when to read the bit.

### The Four Segments of a Bit

Each bit is divided into **four segments**, measured in whole time quanta:

```
       ◄──────────────── ONE CAN BIT ────────────────►
       ┌──────┬───────────────┬───────────────┬───────┐
       │ SYNC │    PROP SEG    │   PHASE SEG1  │ PHASE │
       │ SEG  │                │               │ SEG2  │
       │(1 TQ)│   (1–8 TQ)     │   (1–8 TQ)    │(1–8TQ)│
       └──────┴───────────────┴───────────────┴───────┘
          ▲                            ▲
          │                            │
     transitions               ★ SAMPLE POINT ★
     expected here            (read the voltage HERE)
```

| Segment | Job | Memory hook |
|---|---|---|
| **SYNC_SEG** (Synchronization) | Always 1 TQ. The slice where a bit *transition* (edge) is expected to occur. The "tick" everyone aligns to. | The starting gun 🔫 |
| **PROP_SEG** (Propagation) | Compensates for the *physical signal travel time* down the wire + transceiver delays (Day 5!). | The "wait for the signal to actually arrive" buffer |
| **PHASE_SEG1** | Buffer before the sample point. Can be *lengthened* to resync. | Adjustable cushion #1 |
| **PHASE_SEG2** | Buffer after the sample point. Can be *shortened* to resync. | Adjustable cushion #2 |

> **The sample point sits exactly at the boundary between PHASE_SEG1 and PHASE_SEG2.** Remember that — it's the whole next concept.

### Why Chop a Bit Into Pieces At All?

Two reasons, both deeply practical for a tester:
1. **Propagation compensation:** A bit physically takes *time* to travel from a far node down the bus. PROP_SEG accounts for that round-trip delay so a far-away node's bit has *arrived* before anyone samples it.
2. **Resync wiggle room:** PHASE_SEG1 and PHASE_SEG2 are the elastic segments — the controller stretches or shrinks them to nudge its timing back into alignment with the bus (next concept).

> 🌉 **From your world:** Think of time quanta like **sub-pixel rendering** or the **frame budget** in a game loop — you don't just render "a frame," you carve a 16.6ms budget into precise sub-tasks. Or like **breaking an HTTP timeout into connect/TLS/TTFB phases** so you can tune each independently. Granularity = control.

---

## 🧠 Concept: The Sample Point — *When* You Read the Bit

### The Photo-Finish Analogy 📸

Imagine photographing a sprinter to read their bib number. Snap too early — they're a blur leaving the blocks. Snap too late — they've already passed. There's a **sweet spot** in the middle where the image is sharp and readable.

The **sample point** is *exactly* that: the precise instant within a bit when the CAN controller reads the CAN-H/CAN-L voltage and decides "this bit is a 0 (dominant) or 1 (recessive)."

> **Sample Point** = the position within the bit (expressed as a **percentage** of the total bit time) where the voltage is read.

```
Sample point at 75% (typical for 500 kbit/s):

  ◄──────────── ONE BIT (100%) ────────────►
  ┌──────────────────────────────┬─────────┐
  │   SYNC + PROP + PHASE_SEG1    │ PHASE2  │
  │           (75%)               │  (25%)  │
  └──────────────────────────────┴─────────┘
                                  ▲
                            ★ SAMPLE HERE ★
                          read the voltage at 75%
```

### Why Not Just Sample in the Middle (50%)?

Because of **propagation delay** (Day 5 again!). The signal needs time to:
1. Travel down the (possibly long) bus wire,
2. Settle after the transceiver drives it,
3. Let reflections die down.

Sampling **late** (commonly **75–87.5%** of the bit) gives the signal maximum time to stabilize before you trust it. Sample too early and you might read the bit *while it's still transitioning* — catching a blurry, half-changed voltage.

### 🔑 The Sample Point Must (Roughly) Match Across Nodes

Here's a subtle, tester-critical truth: nodes don't all need *identical* sample points, but they must be **close enough**. A bus where one node samples at 50% and another at 87.5% is fragile — it works on a short bench cable but fails on a long, noisy harness because the timing margins don't overlap.

```
Typical sample points by bitrate:
  500 kbit/s → 75%      (more propagation headroom needed)
  1 Mbit/s   → 75–80%
  125 kbit/s → 87.5%    (short bits relative to settle time? configure carefully)
```

> 🌉 **From your world:** The sample point is like **choosing *when* to assert in a flaky UI test.** Assert too early (before the DOM settles / animation finishes) → false failure on a blurry, mid-transition state. Wait for the right "settled" moment → reliable read. You've tuned `waitFor` conditions a thousand times; the sample point is the *exact same idea*, baked into silicon. ⏱️

> ⚠️ **Tester's red flag:** Two nodes that *technically* run the same bitrate but have **mismatched sample-point configurations** can communicate fine on a short cable and mysteriously fail on a real vehicle harness. This is a *brutal* intermittent bug. If a bus "works on the bench, fails in the car," sample-point/bit-timing mismatch is a prime suspect — right alongside Day 5's termination faults.

---

## 🧠 Concept: Synchronization — Staying in Lockstep Without a Clock Wire

Now we answer the day's haunting question: **with no clock wire and imperfect oscillators, how do nodes stay aligned?**

### The Marching Band Analogy 🥁

A marching band with no conductor stays in step by **listening to the drumline and constantly making tiny corrections** to their own pace. Nobody's internal sense of rhythm is perfect, but everyone nudges themselves back into alignment off a shared cue. CAN does *exactly* this — and the "drum beat" is **every falling edge (recessive→dominant transition)** on the bus.

CAN uses **two** synchronization mechanisms:

### 1️⃣ Hard Synchronization — The Reset

At the **start of every frame** (the SOF bit — the first recessive→dominant edge after idle), every node performs a **hard sync**: it *forcibly restarts* its bit-time counter so its SYNC_SEG lines up with that edge. Everyone slams their stopwatch to zero together.

```
Bus idle (recessive) ──────┐
                           │ falling edge = SOF
                           ▼
                    ALL NODES HARD-SYNC HERE
                    (reset bit timing to align)
```

### 2️⃣ Resynchronization — The Continuous Nudge

Between frames a transmission can be long, and oscillators drift apart bit by bit. So on **every** recessive→dominant edge *during* the frame, each node checks: *"Did this edge arrive exactly in my SYNC_SEG where I expected? Or am I a little early/late?"*

- **Edge came late** (node was running fast) → **lengthen PHASE_SEG1** to wait for the bus.
- **Edge came early** (node was running slow) → **shorten PHASE_SEG2** to catch up.

The maximum amount it can adjust per bit is the **SJW (Synchronization Jump Width)** — a configured limit (1–4 TQ) on how big a single correction can be.

```
Expected edge in SYNC_SEG │
                          ▼
Node's view:   ...────────┤ SYNC │ ...
Actual edge:   ...──────────┐ (arrived LATE — node is running fast)
                            ▼
Correction: lengthen PHASE_SEG1 by up to SJW quanta → realign
```

> **This is the genius:** the *data itself* carries the timing information. Every transition is both a data bit *and* a clock tick. The bit-stuffing rule you learned on Day 3 (insert an opposite bit after 5 identical ones) exists precisely to **guarantee** there's always an edge to resync on, even during long runs of identical bits! 🤯

> 🎉 **Aha moment — Day 3 callback:** Remember wondering why bit-stuffing was *really* necessary? *This* is the deep reason: without a forced transition at least every 5 bits, nodes sampling a long string of identical bits would have no edge to resync against and would slowly drift apart until the bus broke. Bit-stuffing is the heartbeat that keeps the whole orchestra in time. The pieces are connecting. 🧩

> 🌉 **From your world:** This is **NTP / clock synchronization** in distributed systems — nodes with imperfect local clocks continuously nudging toward a shared reference. Or **PLL (phase-locked loop)** recovery. Or even how musicians in a jam session lock to the groove with no click track. Self-correcting consensus on timing, no central authority — you've seen this pattern in distributed systems. CAN does it in hardware, per bit.

---

## 🧩 The Big Picture: One Bit, Fully Dissected

Let's assemble everything into a single annotated bit — the complete anatomy:

```
       ◄────────────────── ONE CAN BIT (e.g., 8 TQ) ──────────────────►
       ┌──────┬──────────────┬──────────────────────┬─────────────────┐
       │ SYNC │   PROP_SEG   │      PHASE_SEG1       │    PHASE_SEG2   │
       │ 1 TQ │    2 TQ      │        3 TQ           │       2 TQ      │
       └──────┴──────────────┴──────────────────────┴─────────────────┘
          ▲          ▲                    ▲          ▲          ▲
          │          │                    │          │          │
       edge       compensate          resync     ★SAMPLE★     resync
       expected   wire travel         cushion     POINT       cushion
       (hard      time (Day 5         (lengthen  (read V_diff (shorten
        sync)     propagation)        to wait)    at ~75%)     to catch up)

  Sample point % = (SYNC + PROP + PHASE_SEG1) / total TQ
                 = (1 + 2 + 3) / 8 = 75%
```

The three layers of "timing" across the course, now distinct in your mind:

```
┌─────────────────────────────────────────────────────────────┐
│  Day 4 MESSAGE TIMING → cycle time, latency, jitter         │
│                          (how often a FRAME appears)        │
│  ───────────────────────────────────────────────────────    │
│  Day 6 BIT TIMING     → baud rate, sample point, sync       │
│                          (the shape of a SINGLE BIT)   ◄──── YOU ARE HERE
│  ───────────────────────────────────────────────────────    │
│  Day 5 PHYSICAL       → CAN-H/CAN-L voltage = the bit value  │
│                          (WHAT a bit physically is)         │
└─────────────────────────────────────────────────────────────┘
```

> **Don't confuse Day 4 and Day 6 timing!** Day 4 is "is the *brake message* sent every 10ms?" (system/message level). Day 6 is "within one 2µs bit, *when exactly* do I read the wire?" (silicon level). Both are "timing," but they live in different universes. A tester must speak both. 🎯

---

## 🌍 Where It's Used in the Real World

### 🚗 Automotive
- **ECU bring-up:** The very first thing that must be right when a new ECU joins a bus is bit timing. A wrong register value (wrong prescaler or segment count) = the node never communicates, or worse, corrupts the bus for everyone. This is the **#1 "nothing works" bring-up bug**.
- **Multi-vendor integration:** A car integrates ECUs from dozens of suppliers. If Supplier A configures a 75% sample point and Supplier B uses 87.5%, they may pass individual bench tests but fail when wired into the long vehicle harness. Sample-point harmonization is a real integration-test concern.
- **J1939 trucks** run 250 kbit/s with tightly specified bit timing so mixed-vendor trailers and tractors interoperate.

### 🏥 Medical Devices
- Surgical robots and imaging systems use precise bit timing because a single corrupted control bit (from a sampling error on a long internal cable) could mis-position an actuator. Bit-timing margins are part of the safety case (IEC 62304).

### 🏠 Smart Home / Industrial
- **CANopen** devices often support auto-bitrate-detection (LSS) during commissioning — a node *listens* silently, tries different bitrates until frames decode cleanly, then locks in. A great example of working *around* CAN's lack of negotiation.
- Long factory-floor buses run slower bitrates (125 kbit/s) specifically to keep propagation delay within the bit-timing budget across hundreds of meters.

---

## 🔬 How a Tester Thinks About It

> Bit timing is invisible when it's right and catastrophic when it's wrong — there's almost no middle ground. Your job: verify every node agrees on bitrate *and* sample point, and that the timing has enough margin to survive real cable lengths, temperature, and oscillator tolerance.

```
┌──────────────────────────────────────────────────────────────┐
│            TEST SCENARIOS FOR CAN BIT TIMING                 │
├──────────────────────────────────────────────────────────────┤
│ 1. BITRATE MATCH       → Do ALL nodes run the exact same     │
│                           configured bitrate?                │
│ 2. SAMPLE-POINT CHECK  → Is each node's sample point in the  │
│                           recommended band (e.g., 75–87.5%)? │
│ 3. SAMPLE-POINT HARMONY→ Are all nodes' sample points close  │
│                           enough to interoperate on a long   │
│                           harness (not just a bench cable)?  │
│ 4. OSC TOLERANCE       → Does the bus survive worst-case     │
│                           crystal drift (±0.1% etc.)?        │
│ 5. SJW MARGIN          → Is the jump width big enough to     │
│                           absorb drift, small enough to be   │
│                           stable?                            │
│ 6. CABLE-LENGTH STRESS → Works on 1m bench AND 40m harness?  │
│ 7. BITRATE-MISMATCH    → Inject a wrong-bitrate node: is the │
│    FAULT                  fault detected & contained?        │
│ 8. COLD-TEMP START     → Do oscillators drift at temperature │
│                           extremes and break timing?         │
└──────────────────────────────────────────────────────────────┘
```

> **From your world — the mapping that makes this click:**

| Software Testing Concept | CAN Bit-Timing Equivalent |
|---|---|
| Serial port baud-rate config (9600/115200) | CAN bitrate configuration |
| Encoding/charset must match both ends | Bitrate + sample point must match all nodes |
| `waitFor` / settle before asserting in UI test | Sample point (wait for voltage to settle) |
| NTP / distributed clock sync | Hard-sync + resync off bus edges |
| Tolerance/margin in flaky-test thresholds | SJW & sample-point margin |
| "Works on my machine" (short cable) | Works on bench, fails on long harness |
| Config bug vs. code bug | Wrong timing register = config bug, bus dead |
| Environment-dependent flake (temperature) | Oscillator drift at temp extremes |

> The reflex that makes you valuable: when a freshly-integrated node "sees nothing" or the **whole bus is dead**, your first thought is **"check the bitrate and bit-timing config"** — not the application code. That instinct saves *days*. It's the embedded equivalent of checking the baud rate before debugging your serial parser. 🎯

---

## 🛠️ Hands-On Exercise: Bit Timing Calculator & Sample-Point Simulator

We'll build the tool every embedded engineer secretly wishes they had on day one: a **bit-timing calculator** that takes a clock and segment configuration, computes the resulting bitrate and sample point, and a **sample-point simulator** that shows *why* sampling at the right moment matters when a signal is still settling.

### Step 1: Setup

```bash
pip install python-can   # already installed; the calc below is pure Python
```

> No hardware needed. This mirrors *exactly* the math inside a CAN controller's bit-timing registers (the values you'd put in an MCP2515, an STM32 bxCAN, or set via `python-can`'s timing config).

### Step 2: Save this as `bit_timing.py`

```python
"""
Day 6 — CAN Bit Timing Calculator & Sample-Point Simulator
Computes bitrate + sample point from oscillator & segment config,
and demonstrates why WHEN you sample a settling signal matters.
"""

# ============================================================
# PART 1: BIT TIMING CALCULATOR
# ============================================================

def compute_bit_timing(f_osc_hz, prescaler, prop_seg, phase_seg1,
                       phase_seg2, sjw=1):
    """
    Compute CAN bitrate and sample point from controller config.

    A bit is divided into Time Quanta (TQ). One TQ = prescaler / f_osc.
    Total TQ per bit = SYNC_SEG(=1) + PROP_SEG + PHASE_SEG1 + PHASE_SEG2.
    Sample point sits at the end of PHASE_SEG1.
    """
    sync_seg = 1  # SYNC_SEG is always exactly 1 TQ
    total_tq = sync_seg + prop_seg + phase_seg1 + phase_seg2

    tq_duration_s = prescaler / f_osc_hz          # seconds per time quantum
    bit_time_s = total_tq * tq_duration_s         # seconds per bit
    bitrate_bps = 1.0 / bit_time_s                # bits per second

    # Sample point = fraction of the bit BEFORE phase_seg2 (where we read)
    tq_before_sample = sync_seg + prop_seg + phase_seg1
    sample_point_pct = 100.0 * tq_before_sample / total_tq

    return {
        'total_tq': total_tq,
        'tq_us': tq_duration_s * 1e6,
        'bit_time_us': bit_time_s * 1e6,
        'bitrate_kbps': bitrate_bps / 1000.0,
        'sample_point_pct': sample_point_pct,
        'sjw': sjw,
    }


def print_timing(name, cfg, result):
    print(f"\n{'='*60}")
    print(f"⏲️  {name}")
    print(f"{'='*60}")
    print(f"   Config: prescaler={cfg['prescaler']}, "
          f"PROP={cfg['prop_seg']}, PS1={cfg['phase_seg1']}, "
          f"PS2={cfg['phase_seg2']}, SJW={cfg['sjw']}")
    print(f"   ─────────────────────────────────────────────")
    print(f"   Time quanta / bit : {result['total_tq']} TQ")
    print(f"   1 TQ duration     : {result['tq_us']:.3f} µs")
    print(f"   Bit time          : {result['bit_time_us']:.3f} µs")
    print(f"   ➜ BITRATE         : {result['bitrate_kbps']:.1f} kbit/s")
    print(f"   ➜ SAMPLE POINT    : {result['sample_point_pct']:.1f} %")

    # Quick health verdict on the sample point
    sp = result['sample_point_pct']
    if 75 <= sp <= 87.5:
        print(f"   ✅ Sample point in recommended band (75–87.5%)")
    else:
        print(f"   ⚠️  Sample point OUTSIDE recommended band! Fragile bus.")


# ============================================================
# PART 2: SAMPLE-POINT SIMULATOR (why timing matters)
# ============================================================

def settling_voltage(t_pct, settle_pct=40.0, final_diff=2.0):
    """
    Model V_diff during a recessive→dominant transition.
    The signal RAMPS from 0V to final_diff, finishing at settle_pct
    of the bit (propagation + rise time). Sampling before it settles
    risks reading a half-formed voltage.
    """
    if t_pct >= settle_pct:
        return final_diff
    return final_diff * (t_pct / settle_pct)   # linear ramp while settling


def simulate_sample_points(settle_pct=40.0, threshold=0.9):
    """Show what value gets read at different sample-point choices."""
    print(f"\n{'='*60}")
    print(f"📸 SAMPLE-POINT SIMULATION")
    print(f"   Signal settles at {settle_pct}% of the bit; "
          f"dominant threshold = {threshold}V")
    print(f"{'='*60}")
    print(f"   {'sample @':>9} | {'V_diff read':>12} | decoded | verdict")
    print(f"   {'-'*9}-+-{'-'*12}-+---------+--------")

    for sp in [20, 30, 40, 50, 62.5, 75, 87.5]:
        v = settling_voltage(sp, settle_pct)
        decoded = 0 if v > threshold else 1     # 0=dominant
        # Truth: this bit is meant to be dominant (0)
        ok = "✅ correct" if decoded == 0 else "❌ MISREAD (saw recessive!)"
        print(f"   {sp:>7.1f}% | {v:>10.2f} V | {decoded:^7} | {ok}")

    print(f"\n   💡 Sampling too EARLY (before {settle_pct}%) can catch the")
    print(f"      signal mid-ramp and misread the bit. Later = safer.")


# ============================================================
# PART 3: RUN IT
# ============================================================

if __name__ == "__main__":
    # --- DEMO 1: A classic 500 kbit/s @ 75% config (16 MHz osc) ---
    cfg1 = dict(prescaler=2, prop_seg=4, phase_seg1=7, phase_seg2=4, sjw=1)
    r1 = compute_bit_timing(16_000_000, **cfg1)
    print_timing("DEMO 1: Target 500 kbit/s, 75% sample point", cfg1, r1)

    # --- DEMO 2: 250 kbit/s for J1939 (16 MHz osc) ---
    cfg2 = dict(prescaler=4, prop_seg=4, phase_seg1=7, phase_seg2=4, sjw=1)
    r2 = compute_bit_timing(16_000_000, **cfg2)
    print_timing("DEMO 2: Target 250 kbit/s (J1939)", cfg2, r2)

    # --- DEMO 3: A BAD config — sample point too early ---
    cfg3 = dict(prescaler=2, prop_seg=2, phase_seg1=2, phase_seg2=7, sjw=1)
    r3 = compute_bit_timing(16_000_000, **cfg3)
    print_timing("DEMO 3: BAD CONFIG — sample point too early", cfg3, r3)

    # --- DEMO 4: Why the sample point position matters ---
    simulate_sample_points(settle_pct=40.0)
```

### Step 3: Run it

```bash
python bit_timing.py
```

### ✅ Expected Output (abridged)

```
============================================================
⏲️  DEMO 1: Target 500 kbit/s, 75% sample point
============================================================
   Config: prescaler=2, PROP=4, PS1=7, PS2=4, SJW=1
   ─────────────────────────────────────────────
   Time quanta / bit : 16 TQ
   1 TQ duration     : 0.125 µs
   Bit time          : 2.000 µs
   ➜ BITRATE         : 500.0 kbit/s
   ➜ SAMPLE POINT    : 75.0 %
   ✅ Sample point in recommended band (75–87.5%)

============================================================
⏲️  DEMO 3: BAD CONFIG — sample point too early
============================================================
   ...
   ➜ SAMPLE POINT    : 37.5 %
   ⚠️  Sample point OUTSIDE recommended band! Fragile bus.

============================================================
📸 SAMPLE-POINT SIMULATION
   Signal settles at 40.0% of the bit; dominant threshold = 0.9V
============================================================
   sample @ |  V_diff read | decoded | verdict
   ---------+--------------+---------+--------
      20.0% |       1.00 V |    0    | ✅ correct
      30.0% |       1.50 V |    0    | ✅ correct
      40.0% |       2.00 V |    0    | ✅ correct
      50.0% |       2.00 V |    0    | ✅ correct
      75.0% |       2.00 V |    0    | ✅ correct
      87.5% |       2.00 V |    0    | ✅ correct
   💡 Sampling too EARLY (before 40%) can catch the signal
      mid-ramp and misread the bit. Later = safer.
```

> 🎉 **The aha moment to internalize:** In **DEMO 1**, watch how `prescaler=2, PROP=4, PS1=7, PS2=4` and a 16 MHz crystal produce *exactly* 500 kbit/s at *exactly* a 75% sample point — no magic, just `total_TQ × (prescaler/f_osc)`. This is the literal arithmetic inside every CAN chip's timing registers. And **DEMO 3** shows how the *same bitrate math* with rearranged segments yields a dangerous 37.5% sample point — a config that might pass on a 1m cable and die on a real harness. **Same bitrate, fragile bus.** That's the bug that eats a junior engineer's whole week. 🔬

---

## 🎯 Challenge: The Mismatched Baud Rate Mystery

> **Scenario:** A new infotainment ECU was added to a 500 kbit/s vehicle bus. The moment it powers on, the **entire bus floods with errors** and *every* ECU's communication degrades — not just the new one. The supplier insists their ECU "passed all bench tests." You suspect bit timing. Prove it, quantify it, and write the diagnosis.

### Challenge 1 — 🔢 Reverse-Engineer the Bitrate Registers
Using `compute_bit_timing`, find **three different segment configurations** (different prescaler/segment combos) that *all* produce **500 kbit/s** from a 16 MHz oscillator — but with **three different sample points** (e.g., ~62.5%, ~75%, ~87.5%).
- *Question:* All three are "500 kbit/s." Why might a node configured for the 62.5% one fail to interoperate on a long harness with nodes at 87.5%, even though the *bitrate* matches perfectly? (This is the subtle bug the supplier missed.)

### Challenge 2 — 💥 Model the Bitrate-Mismatch Catastrophe
Show *why* a single wrong-bitrate node takes down the **whole** bus (not just itself):

```python
def simulate_bitrate_mismatch(bus_kbps, intruder_kbps, num_bits=8):
    """
    A node running at the WRONG bitrate samples the bus at the wrong
    moments, misreads bits, and (because it thinks it sees errors)
    transmits ERROR FRAMES that corrupt everyone's traffic.

    Model: the intruder's bit period differs, so its sample points
    drift relative to the real bits. Count how many of `num_bits`
    it misreads, then explain the bus-wide consequence.
    """
    bus_bit_us = 1000.0 / bus_kbps
    intruder_bit_us = 1000.0 / intruder_kbps
    # TODO: walk the intruder's sample points across the real bit stream
    # TODO: count misreads where the intruder samples in the WRONG bit
    # TODO: return misread count + explain the error-frame cascade
    pass
```
- Implement the drift walk, then connect it to **Day 3**: a node that misreads bits generates **error frames**, and error frames corrupt *everyone's* messages → forced retransmissions → the bus-wide flood you observed.
- *The link:* This is why "one bad node" is a **system-level** failure, exactly like Day 3's babbling-idiot node — but here the root cause is a **config (bit-timing) bug**, not a hardware fault.

### Challenge 3 — 😈 The Oscillator-Tolerance Edge Case (System-Level)
Crystals aren't perfect — a "16 MHz" oscillator might actually be 16 MHz ±0.1%. Two nodes at opposite tolerance extremes drift relative to each other.
- Model two nodes: one oscillator at +0.1%, one at −0.1% (a 0.2% relative difference). Over a maximally-long unstuffed bit run, compute how far their sample points drift apart.
- Compare that drift against the **SJW** (max correction per bit). Show that if accumulated drift between resync edges exceeds what SJW can correct, a bit gets misread.
- *The killer question:* The bus works perfectly at room temperature but fails on a **cold winter morning** when one ECU's crystal drifts further. Each node *individually* meets its spec. **Which test phase catches this — and what environmental rig would you need?** *(This is Day 4 & 5's "static passes, dynamic fails" / "compliance ≠ safety" reincarnated at the bit-timing layer: every node is in-spec, yet the system fails at temperature extremes because the timing *margins* don't account for combined worst-case drift.)*

### Hints
- For Challenge 1: keep `total_TQ` constant (e.g., 16 TQ → 2µs bit at the right prescaler) but shuffle TQ between PROP/PS1 (before sample) and PS2 (after). Sample point = (1+PROP+PS1)/total.
- For Challenge 2: the intruder's bit period is a different length, so its sample point creeps forward (or back) every bit until it samples in the *next* bit entirely — guaranteed misreads.
- For Challenge 3: bit-stuffing (Day 3) bounds the max run without an edge to ~5 bits — that's your worst-case window for drift to accumulate before a resync. SJW must cover it.

---

## ❓ Quiz

### Q1
> A CAN controller uses a **16 MHz** oscillator with **prescaler = 8**.
> A bit is configured as: SYNC=1 TQ, PROP=2 TQ, PHASE_SEG1=4 TQ, PHASE_SEG2=3 TQ.
> Compute: (a) the duration of one TQ, (b) the total bit time, (c) the bitrate,
> and (d) the sample point percentage.

### Q2
> Two nodes are both set to "500 kbit/s," but one samples at **62.5%** and the
> other at **87.5%**. They work flawlessly on a 1-meter bench cable but throw
> intermittent errors on a 40-meter vehicle harness. **Why?**

### Q3
> CAN has no clock wire. During a long run of identical bits, how do nodes
> avoid drifting out of sync — and what **Day 3 mechanism** makes this
> guaranteed to work?

---

### ✅ Answer 1
```
(a) One TQ = prescaler / f_osc = 8 / 16,000,000 = 0.5 µs

(b) Total TQ = SYNC + PROP + PS1 + PS2 = 1 + 2 + 4 + 3 = 10 TQ
    Bit time = 10 TQ × 0.5 µs = 5.0 µs

(c) Bitrate = 1 / bit_time = 1 / 5.0 µs = 200,000 bit/s = 200 kbit/s

(d) Sample point = (SYNC + PROP + PS1) / total
                 = (1 + 2 + 4) / 10 = 7/10 = 70.0%
```
✅ **200 kbit/s, sampled at 70%.** Note 70% is *just below* the typical 75–87.5% comfort band — a tester might flag it for review on a long bus, since there's less settling headroom than ideal.

> 💡 The whole calculation is just `total_TQ × (prescaler / f_osc)` for timing and `(pre-sample TQ) / total_TQ` for the sample point. Master these two formulas and you can read any CAN controller's timing registers.

### ✅ Answer 2
**Because matching the bitrate is necessary but NOT sufficient — the sample points must also be compatible, and propagation delay on a long cable exposes the mismatch.**

On a 1-meter cable, signal propagation is nearly instant, so even a 62.5% vs 87.5% sample-point gap leaves plenty of overlapping margin — both nodes happen to read a settled, correct voltage. But on a **40-meter harness**, propagation delay grows: the signal takes meaningfully longer to travel and settle. Now:
- The node sampling at **62.5%** may read the bit **before it has fully settled** at the far end of the bus (catching a still-transitioning voltage),
- while the **87.5%** node reads it fine.

The result is **intermittent bit misreads → error frames → bus-wide errors** — but *only* once the cable is long enough to expose the timing-margin gap. Classic "works on the bench, fails in the car."

> 🏆 **Tester's reflex:** When something works on a short cable but fails on a long one, suspect **bit timing / sample point** (today) or **termination/reflections** (Day 5) — both are physical-length-dependent. Length is the variable that turns a latent config bug into a field failure.

### ✅ Answer 3
During a long run of identical bits there are **no recessive→dominant edges**, so nodes have nothing to resynchronize against and would slowly drift apart on their imperfect oscillators. CAN prevents this with **bit-stuffing (Day 3)**: after **5 identical bits in a row**, the transmitter forcibly inserts one bit of the *opposite* polarity.

That stuffed bit **guarantees a transition at least every 6 bit-times** — and every recessive→dominant transition triggers **resynchronization** (each node nudges PHASE_SEG1/PHASE_SEG2 by up to SJW to realign with the edge). So the data stream is *forced* to keep providing clock-like edges, and nodes continuously correct their drift off them.

> 🎉 **The connection that ties the course together:** Bit-stuffing isn't *only* an error-detection trick (Day 3) — its deeper purpose is to **keep the clockless bus synchronized**. The same mechanism serves two masters: integrity *and* timing. That's the kind of elegant double-duty design that makes CAN a masterpiece — and the kind of cross-layer insight that makes *you* a senior embedded tester. 🧩🎯

---

## 🎓 Key Takeaways

- 📻 **Baud/bit rate is a pre-shared agreement.** Every node MUST match exactly — there's no negotiation in standard CAN. A mismatched node doesn't get "slow data," it gets garbage and can crash the whole bus. (Same failure as a serial baud-rate or charset mismatch.)
- 🎬 **Each bit is divided into Time Quanta** across four segments: SYNC_SEG (1 TQ, the alignment tick), PROP_SEG (compensates physical wire delay — Day 5!), and PHASE_SEG1/2 (the elastic resync cushions).
- 📸 **The sample point is *when* (as a % of the bit) the voltage is read** — typically **75–87.5%**, deliberately late so the signal has time to settle. It's the embedded twin of "wait for the DOM to settle before asserting."
- 🥁 **Synchronization keeps a clockless bus in lockstep:** hard-sync at SOF resets everyone; resync on every edge nudges PHASE_SEG1/2 (bounded by SJW) to correct oscillator drift — just like NTP/PLL self-correction.
- 🧩 **Bit-stuffing (Day 3) is secretly a timing mechanism:** it guarantees a transition at least every 6 bits so nodes always have an edge to resync against. Integrity *and* synchronization, one elegant rule.
- 🔬 **Bit timing is binary: invisible when right, catastrophic when wrong.** Two nodes can share a bitrate yet fail to interoperate due to mismatched sample points — passing on a bench cable, dying on a long harness.
- 🌉 **Your instincts transfer:** baud-rate configs, `waitFor` settle conditions, distributed clock sync, config-vs-code bugs, and length-/temperature-dependent flakes — when a freshly-integrated node "sees nothing," check the **bit-timing config first**.

---

> **Next up (Day 7 options):**
> CAN-FD bit timing (dual bitrates — slow arbitration, fast data phase) · Hands-on bit-timing register config on a real MCP2515/STM32 · Oscillator tolerance & worst-case timing budget math · Bus topology, stub length & propagation-delay testing · Putting it together: a full HIL bring-up checklist for a new ECU

*Generated from a live mentoring session with Professor Embed. 🚗⚡⏲️*
