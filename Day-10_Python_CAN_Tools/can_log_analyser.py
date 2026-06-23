"""
Day 10 Mini-Project: Python + python-can + cantools
====================================================
Complete CAN log analyser: generate a synthetic drive-cycle log,
then decode every frame, run signal-range + cycle-time assertions,
and print a structured test report.

No hardware needed. Everything runs on python-can's virtual bus
with an ASC file as the intermediary.

Install:
    pip install python-can cantools
"""

import can
import cantools
import random
import struct
import threading
import time
from collections import defaultdict
from pathlib import Path

# ─── DBC & CONFIG ────────────────────────────────────────────────────────────

DBC_PATH   = Path(__file__).parent / "vehicle_day10.dbc"
LOG_PATH   = Path(__file__).parent / "drive_cycle.asc"
CHANNEL    = "vcan0"
DURATION_S = 3.0   # simulate 3 seconds of drive-cycle data


# ─── STEP 1: GENERATE A SYNTHETIC LOG ────────────────────────────────────────

def encode_engine_data(rpm: float, coolant_c: float,
                       throttle_pct: float, load_pct: float) -> bytes:
    """Manually encode EngineData (0x201) — raw struct, no cantools."""
    rpm_raw      = int(rpm)
    coolant_raw  = int((coolant_c + 40) / 0.5)
    throttle_raw = int(throttle_pct / 0.392157)
    load_raw     = int(load_pct / 0.392157)

    coolant_raw  = max(0, min(255, coolant_raw))
    throttle_raw = max(0, min(255, throttle_raw))
    load_raw     = max(0, min(255, load_raw))

    return struct.pack("<HBBBBBB",
                       rpm_raw & 0xFFFF,
                       coolant_raw,
                       throttle_raw,
                       load_raw,
                       0, 0, 0)[:8]


def encode_trans_data(gear: int, target: int,
                      speed_kmh: float, accel_pct: float) -> bytes:
    nibbles  = (gear & 0xF) | ((target & 0xF) << 4)
    spd_raw  = int(speed_kmh / 0.01)
    acc_raw  = max(0, min(255, int(accel_pct / 0.392157)))
    return struct.pack("<BHBB", nibbles, spd_raw & 0xFFFF, acc_raw, 0)[:8]


def encode_wheel_speed(fl: float, fr: float, rl: float, rr: float) -> bytes:
    return struct.pack("<HHHH",
                       int(fl / 0.01) & 0xFFFF,
                       int(fr / 0.01) & 0xFFFF,
                       int(rl / 0.01) & 0xFFFF,
                       int(rr / 0.01) & 0xFFFF)


def generate_log(log_path: Path, duration_s: float = 3.0) -> None:
    """
    Simulate a short drive cycle and record it to an ASC log file.
    Injects two deliberate faults for the analyser to catch:
      - Frame at t≈1.5s with RPM=9500 (above max 8000) ← range violation
      - Missing EngineData burst around t≈2.0s          ← cycle time violation
    """
    print(f"\n{'='*60}")
    print("STEP 1: Generating synthetic drive-cycle log...")
    print(f"{'='*60}")

    tx_bus = can.Bus(interface="virtual", channel=CHANNEL, receive_own_messages=False)
    rx_bus = can.Bus(interface="virtual", channel=CHANNEL)

    with can.ASCWriter(str(log_path)) as writer:
        notifier = can.Notifier(rx_bus, [writer])

        t_start       = time.monotonic()
        t_last_engine = t_start
        t_last_trans  = t_start
        t_last_wheel  = t_start
        frame_count   = 0

        while True:
            now     = time.monotonic()
            elapsed = now - t_start
            if elapsed >= duration_s:
                break

            # EngineData every 10 ms
            if now - t_last_engine >= 0.010:
                rpm = 800 + (elapsed / duration_s) * 3000 + random.gauss(0, 10)

                # 💣 FAULT 1: inject an out-of-range RPM at ~1.5 s
                if 1.45 <= elapsed <= 1.46:
                    rpm = 9500

                # 💣 FAULT 2: suppress EngineData for 80 ms around t=2.0 s
                if 1.95 <= elapsed <= 2.03:
                    t_last_engine = now
                    continue

                coolant  = 85 + random.gauss(0, 0.5)
                throttle = min(100, max(0, 20 + elapsed * 10))
                load     = throttle * 0.8

                data = encode_engine_data(rpm, coolant, throttle, load)
                tx_bus.send(can.Message(
                    arbitration_id=0x201, data=data, is_extended_id=False))
                t_last_engine = now
                frame_count  += 1

            # TransData every 20 ms
            if now - t_last_trans >= 0.020:
                speed = elapsed * 30
                data  = encode_trans_data(3, 3, speed, 20)
                tx_bus.send(can.Message(
                    arbitration_id=0x300, data=data, is_extended_id=False))
                t_last_trans = now
                frame_count += 1

            # WheelSpeed every 10 ms
            if now - t_last_wheel >= 0.010:
                speed = elapsed * 30
                skew  = random.gauss(0, 0.2)
                data  = encode_wheel_speed(speed, speed + skew,
                                           speed - skew * 0.5, speed + skew * 0.3)
                tx_bus.send(can.Message(
                    arbitration_id=0x400, data=data, is_extended_id=False))
                t_last_wheel = now
                frame_count += 1

            time.sleep(0.001)

        notifier.stop()

    tx_bus.shutdown()
    rx_bus.shutdown()
    print(f"  ✓ Logged {frame_count} frames → {log_path}")


# ─── STEP 2: DECODE & ANALYSE ────────────────────────────────────────────────

class TestResult:
    """Accumulate pass/fail results like a mini pytest report."""

    def __init__(self):
        self.passed: list = []
        self.failed: list = []

    def step_pass(self, name: str, detail: str = "") -> None:
        tag = f"  ✅ PASS  {name}" + (f"  [{detail}]" if detail else "")
        self.passed.append(tag)
        print(tag)

    def step_fail(self, name: str, detail: str = "") -> None:
        tag = f"  ❌ FAIL  {name}" + (f"  [{detail}]" if detail else "")
        self.failed.append(tag)
        print(tag)

    def summary(self) -> None:
        total = len(self.passed) + len(self.failed)
        print(f"\n{'='*60}")
        print(f"  TEST SUMMARY: {len(self.passed)}/{total} passed, "
              f"{len(self.failed)} failed")
        print(f"{'='*60}")
        if self.failed:
            print("\n  Failed checks:")
            for f in self.failed:
                print(f"    {f.strip()}")


def analyse_log(log_path: Path, dbc_path: Path) -> None:
    """
    Read a CAN ASC log and run three categories of checks:
      A. Signal range validation   (against DBC min/max)
      B. Cycle time validation     (against GenMsgCycleTime attribute)
      C. Wheel speed consistency   (all 4 wheels within 5 km/h of each other)
    """
    print(f"\n{'='*60}")
    print("STEP 2: Loading DBC...")
    print(f"{'='*60}")

    db = cantools.db.load_file(str(dbc_path))
    tr = TestResult()

    # Build expected cycle times from DBC attributes
    expected_cycle: dict = {}
    for msg in db.messages:
        attr = msg.dbc.attributes.get("GenMsgCycleTime") if msg.dbc else None
        if attr is not None:
            val = attr.value if hasattr(attr, "value") else attr
            if isinstance(val, (int, float)) and val > 0:
                expected_cycle[msg.frame_id] = val / 1000.0  # ms → seconds

    print(f"  Cycle-time oracles loaded from DBC: {expected_cycle}")

    print(f"\n{'='*60}")
    print("STEP 3: Parsing log file...")
    print(f"{'='*60}")

    last_timestamp: dict = {}
    intervals:      dict = defaultdict(list)
    frame_counts:   dict = defaultdict(int)
    range_failures: dict = defaultdict(list)

    total_frames = 0
    unknown_ids  = set()

    with can.ASCReader(str(log_path)) as reader:
        for frame in reader:
            arb_id = frame.arbitration_id
            total_frames += 1

            # Cycle time tracking
            if arb_id in last_timestamp:
                gap = frame.timestamp - last_timestamp[arb_id]
                intervals[arb_id].append(gap)
            last_timestamp[arb_id] = frame.timestamp

            # Decode
            try:
                msg_def = db.get_message_by_frame_id(arb_id)
            except KeyError:
                unknown_ids.add(arb_id)
                continue

            try:
                signals = msg_def.decode(bytes(frame.data))
            except Exception as e:
                tr.step_fail(f"Decode error  ID=0x{arb_id:03X}", str(e))
                continue

            frame_counts[arb_id] += 1

            # Signal range check
            for sig in msg_def.signals:
                val = signals.get(sig.name)
                if val is None:
                    continue
                lo = sig.minimum
                hi = sig.maximum
                if lo is not None and hi is not None:
                    if not (lo <= val <= hi):
                        range_failures[sig.name].append(
                            (frame.timestamp, val, lo, hi))

    print(f"  Parsed {total_frames} frames total")
    if unknown_ids:
        print(f"  ⚠  Unknown IDs (not in DBC): "
              f"{[hex(i) for i in sorted(unknown_ids)]}")

    # ═══════════════════════════════════════════════════════════════════
    # CHECK A: Signal Range Validation
    # ═══════════════════════════════════════════════════════════════════
    print(f"\n{'─'*60}")
    print("CHECK A: Signal Range Validation")
    print(f"{'─'*60}")

    all_signals = {sig.name for msg in db.messages for sig in msg.signals}
    violated    = set(range_failures.keys())
    clean       = all_signals - violated

    for name in sorted(clean):
        tr.step_pass(f"Range OK  {name}")

    for name, violations in sorted(range_failures.items()):
        for ts, val, lo, hi in violations[:3]:
            tr.step_fail(f"Range VIOLATION  {name}",
                         f"t={ts:.3f}s  value={val:.2f}  "
                         f"expected [{lo}, {hi}]")
        if len(violations) > 3:
            print(f"       ... and {len(violations)-3} more violations")

    # ═══════════════════════════════════════════════════════════════════
    # CHECK B: Cycle Time Validation
    # ═══════════════════════════════════════════════════════════════════
    print(f"\n{'─'*60}")
    print("CHECK B: Cycle Time Validation")
    print(f"{'─'*60}")

    TOLERANCE = 0.50   # allow ±50% of nominal before flagging average
    GAP_RATIO  = 2.5   # flag individual gaps > 2.5× nominal

    for arb_id, expected_s in expected_cycle.items():
        gaps = intervals.get(arb_id, [])
        try:
            msg_name = db.get_message_by_frame_id(arb_id).name
        except KeyError:
            msg_name = f"0x{arb_id:03X}"

        if not gaps:
            tr.step_fail(f"CycleTime  {msg_name}", "no frames received")
            continue

        avg_gap = sum(gaps) / len(gaps)
        max_gap = max(gaps)
        jitter  = max(gaps) - min(gaps)

        deviation = abs(avg_gap - expected_s) / expected_s
        if deviation <= TOLERANCE:
            tr.step_pass(
                f"CycleTime avg  {msg_name}",
                f"avg={avg_gap*1000:.1f}ms  expected={expected_s*1000:.0f}ms  "
                f"jitter={jitter*1000:.1f}ms")
        else:
            tr.step_fail(
                f"CycleTime avg  {msg_name}",
                f"avg={avg_gap*1000:.1f}ms  expected={expected_s*1000:.0f}ms  "
                f"deviation={deviation*100:.0f}%")

        if max_gap > expected_s * GAP_RATIO:
            tr.step_fail(
                f"CycleTime max-gap  {msg_name}",
                f"worst gap={max_gap*1000:.1f}ms  "
                f"threshold={expected_s*GAP_RATIO*1000:.0f}ms  "
                f"→ possible silent ECU / message loss")
        else:
            tr.step_pass(f"CycleTime max-gap  {msg_name}",
                         f"worst gap={max_gap*1000:.1f}ms OK")

    # ═══════════════════════════════════════════════════════════════════
    # CHECK C: Wheel Speed Cross-Signal Consistency
    # ═══════════════════════════════════════════════════════════════════
    print(f"\n{'─'*60}")
    print("CHECK C: Wheel Speed Cross-Signal Consistency")
    print(f"{'─'*60}")

    wheel_id   = 0x400
    max_spread = 5.0   # km/h
    spread_violations = 0

    try:
        wheel_msg = db.get_message_by_frame_id(wheel_id)
        with can.ASCReader(str(log_path)) as reader:
            for frame in reader:
                if frame.arbitration_id != wheel_id:
                    continue
                signals = wheel_msg.decode(bytes(frame.data))
                speeds  = [signals["SpeedFL"], signals["SpeedFR"],
                           signals["SpeedRL"], signals["SpeedRR"]]
                spread  = max(speeds) - min(speeds)
                if spread > max_spread:
                    spread_violations += 1

        if spread_violations == 0:
            tr.step_pass("WheelSpeed consistency",
                         f"all frames within {max_spread} km/h spread")
        else:
            tr.step_fail("WheelSpeed consistency",
                         f"{spread_violations} frames exceeded "
                         f"{max_spread} km/h spread → check ABS / skid event")
    except KeyError:
        tr.step_fail("WheelSpeed consistency", "0x400 not in DBC")

    tr.summary()


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main() -> None:
    print("\n" + "🐍 " * 20)
    print("  Day 10 Mini-Project: CAN Log Analyser")
    print("  python-can + cantools + structured assertions")
    print("🐍 " * 20)

    if not DBC_PATH.exists():
        print(f"\n❌  DBC not found: {DBC_PATH}")
        print("    Make sure vehicle_day10.dbc is in the same folder.")
        return

    if not LOG_PATH.exists():
        generate_log(LOG_PATH, DURATION_S)
    else:
        print(f"\nℹ  Reusing existing log: {LOG_PATH}")
        print("   Delete drive_cycle.asc to regenerate.")

    analyse_log(LOG_PATH, DBC_PATH)


if __name__ == "__main__":
    main()
