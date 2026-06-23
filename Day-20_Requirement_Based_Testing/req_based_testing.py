"""
Day 20: Requirement-Based Testing
Equivalence Partitioning · Boundary Value Analysis · State Transition ·
Decision Tables · MC/DC Coverage
======================================================================
Demonstrates five systematic test design techniques applied to automotive
ECU requirements:

  GROUP 1  EP    — Equivalence Partitioning   (TC01–TC04)  [ASIL-B]
  GROUP 2  BVA   — Boundary Value Analysis    (TC05–TC08)  [ASIL-C]
  GROUP 3  STATE — State Transition Testing   (TC09–TC12)  [ASIL-B]
  GROUP 4  DT    — Decision Table Testing     (TC13–TC16)  [ASIL-B]
  GROUP 5  MCDV  — MC/DC Coverage             (TC17–TC20)  [ASIL-D]

Each TC records which requirement it traces to and which technique produced it.
TC20 prints a full coverage report per technique per requirement.

No hardware needed.
Install:  pip install python-can
Run:      python req_based_testing.py
"""

import can
import threading
import time
import struct
import random
from dataclasses import dataclass
from enum import Enum
from typing import Optional, List, Dict, Tuple

# ─── CONSTANTS ────────────────────────────────────────────────────────────────

CHANNEL      = "vcan0"
TESTER_TX_ID = 0x7E0
TESTER_RX_ID = 0x7E8

SID_SESSION  = 0x10
SID_RESET    = 0x11
SID_SEC      = 0x27
SID_READ_DID = 0x22
SID_READ_DTC = 0x19
SID_CLR_DTC  = 0x14
SID_TP       = 0x3E
SID_NEG      = 0x7F

NRC_SUBFUNC_NOT_SUPP  = 0x12
NRC_CONDITIONS_NOT_OK = 0x22
NRC_OUT_OF_RANGE      = 0x31
NRC_INVALID_KEY       = 0x35
NRC_EXCEEDED_ATTEMPTS = 0x36

SEC_SECRET       = 0xDEADBEEF
SEC_MAX_ATTEMPTS = 3

FAN_ON_C    = 90.0
FAN_OFF_C   = 85.0
OVER_TEMP_C = 105.0   # DTC condition: temp STRICTLY > 105.0

DTC_P0217_H   = 0x02
DTC_P0217_L   = 0x17
DTC_CONFIRMED = 0xAF

# ─── ISO-TP HELPERS ───────────────────────────────────────────────────────────

def build_sf(uds: list) -> bytes:
    assert 1 <= len(uds) <= 7
    return bytes([len(uds)] + list(uds) + [0] * (7 - len(uds)))

def build_ff(uds: list) -> bytes:
    n = len(uds)
    return bytes([0x10 | ((n >> 8) & 0x0F), n & 0xFF] + list(uds[:6]))

def build_cf(sn: int, chunk: list) -> bytes:
    return bytes([0x20 | (sn & 0x0F)] + list(chunk) + [0] * (7 - len(chunk)))

def build_fc(bs: int = 0, stmin: int = 5) -> bytes:
    return bytes([0x30, bs, stmin, 0, 0, 0, 0, 0])


# ─── TEST DESIGN DATA MODEL ───────────────────────────────────────────────────

class ASILLevel(Enum):
    QM = 0; A = 1; B = 2; C = 3; D = 4

@dataclass
class EPEntry:
    """Records one equivalence partition tested."""
    req_id:        str
    partition:     str    # e.g., "valid_session", "invalid_zero"
    representative:str    # e.g., "0x02", "0x00"
    expected:      str    # e.g., "positive_response", "NRC_0x12"
    tc_id:         str
    passed:        bool

@dataclass
class BVAEntry:
    """Records one boundary value test point."""
    req_id:    str
    boundary:  str    # e.g., "below_threshold", "at_threshold", "above_threshold"
    value:     str    # e.g., "104.9°C", "105.0°C"
    expected:  str    # e.g., "no_DTC", "DTC_confirmed"
    tc_id:     str
    passed:    bool

@dataclass
class StateEntry:
    """Records one state transition tested."""
    req_id:   str
    from_s:   str    # e.g., "DEFAULT"
    to_s:     str    # e.g., "EXTENDED", "BLOCKED"
    trigger:  str    # e.g., "0x10 0x03"
    tc_id:    str
    passed:   bool

@dataclass
class DecisionEntry:
    """Records one row of a decision table tested."""
    req_id:     str
    conditions: str    # e.g., "(temp=70, fan_was=OFF)"
    expected:   str    # e.g., "fan=OFF"
    tc_id:      str
    passed:     bool

@dataclass
class MCDVEntry:
    """Records one MC/DC test case."""
    req_id:      str
    varied:      str    # "reference", "temp_condition", "dtc_enabled_condition"
    description: str
    tc_id:       str
    is_reference:bool
    passed:      bool


class TestDesignReport:
    """
    Aggregates results from all five test design techniques.
    Records which partitions / boundary points / transitions / rows / MC/DC
    tests were exercised, and prints a structured coverage report.
    """

    def __init__(self) -> None:
        self.ep:       List[EPEntry]       = []
        self.bva:      List[BVAEntry]      = []
        self.state:    List[StateEntry]    = []
        self.decision: List[DecisionEntry] = []
        self.mcdv:     List[MCDVEntry]     = []
        self._passed:  List[str]           = []
        self._failed:  List[str]           = []

    # ── Recording ─────────────────────────────────────────────────────────────

    def pass_tc(self, tc_id: str, detail: str = "") -> None:
        tag = f"  ✅ PASS  {tc_id}  {detail}"
        print(tag)
        self._passed.append(tc_id)

    def fail_tc(self, tc_id: str, detail: str = "") -> None:
        tag = f"  ❌ FAIL  {tc_id}  {detail}"
        print(tag)
        self._failed.append(tc_id)

    def summary(self) -> Tuple[int, int]:
        return len(self._passed), len(self._failed)

    # ── Coverage Report ───────────────────────────────────────────────────────

    def print_coverage(self) -> None:
        icon = lambda p: "✅" if p else "❌"

        print(f"\n{'═' * 66}")
        print(f"  TEST DESIGN COVERAGE REPORT")
        print(f"{'═' * 66}")

        # ── EP ────────────────────────────────────────────────────────────────
        print(f"\n  EQUIVALENCE PARTITIONING")
        print(f"  {'Partition':<28} {'Representative':<18} {'Expected':<22} {'TC':<6} Status")
        print(f"  {'─' * 84}")
        req_id = self.ep[0].req_id if self.ep else "—"
        print(f"  Req: {req_id}")
        for e in self.ep:
            print(f"    {e.partition:<26} {e.representative:<18} "
                  f"{e.expected:<22} {e.tc_id:<6} {icon(e.passed)}")
        ep_cov = sum(1 for e in self.ep if e.passed)
        print(f"  Coverage: {ep_cov}/{len(self.ep)} partitions  "
              f"({round(ep_cov/max(len(self.ep),1)*100)}%)")

        # ── BVA ───────────────────────────────────────────────────────────────
        print(f"\n  BOUNDARY VALUE ANALYSIS")
        print(f"  {'Boundary Point':<28} {'Value':<16} {'Expected':<22} {'TC':<6} Status")
        print(f"  {'─' * 84}")
        for e in self.bva:
            print(f"    {e.boundary:<26} {e.value:<16} "
                  f"{e.expected:<22} {e.tc_id:<6} {icon(e.passed)}")
        bva_cov = sum(1 for e in self.bva if e.passed)
        print(f"  Coverage: {bva_cov}/{len(self.bva)} boundary points  "
              f"({round(bva_cov/max(len(self.bva),1)*100)}%)")

        # ── State Transition ──────────────────────────────────────────────────
        print(f"\n  STATE TRANSITION TESTING")
        print(f"  {'From → To':<28} {'Trigger':<20} {'TC':<6} Status")
        print(f"  {'─' * 62}")
        req_id = self.state[0].req_id if self.state else "—"
        print(f"  Req: {req_id}")
        for e in self.state:
            arrow = f"{e.from_s} → {e.to_s}"
            print(f"    {arrow:<26} {e.trigger:<20} {e.tc_id:<6} {icon(e.passed)}")
        st_cov = sum(1 for e in self.state if e.passed)
        print(f"  Coverage: {st_cov}/{len(self.state)} transitions  "
              f"({round(st_cov/max(len(self.state),1)*100)}%)")

        # ── Decision Table ────────────────────────────────────────────────────
        print(f"\n  DECISION TABLE TESTING")
        print(f"  {'Conditions':<36} {'Expected Output':<22} {'TC':<6} Status")
        print(f"  {'─' * 72}")
        req_id = self.decision[0].req_id if self.decision else "—"
        print(f"  Req: {req_id}")
        for e in self.decision:
            print(f"    {e.conditions:<34} {e.expected:<22} {e.tc_id:<6} {icon(e.passed)}")
        dt_cov = sum(1 for e in self.decision if e.passed)
        print(f"  Coverage: {dt_cov}/{len(self.decision)} decision rows  "
              f"({round(dt_cov/max(len(self.decision),1)*100)}%)")

        # ── MC/DC ─────────────────────────────────────────────────────────────
        print(f"\n  MC/DC COVERAGE  [ASIL-D]")
        print(f"  Expression:  temp > {OVER_TEMP_C} AND dtc_enabled")
        print(f"  {'Condition Varied':<28} {'Description':<34} {'TC':<6} Status")
        print(f"  {'─' * 72}")
        req_id = self.mcdv[0].req_id if self.mcdv else "—"
        print(f"  Req: {req_id}")
        for e in self.mcdv:
            ref_tag = "  ← reference" if e.is_reference else ""
            print(f"    {e.varied:<26} {e.description:<34} {e.tc_id:<6} "
                  f"{icon(e.passed)}{ref_tag}")
        mc_cov = sum(1 for e in self.mcdv if e.passed)
        if mc_cov == len(self.mcdv) == 3:
            print(f"  MC/DC ACHIEVED: both conditions independently affect outcome ✓")
        print(f"  Coverage: {mc_cov}/{len(self.mcdv)} MC/DC tests")

        # ── Summary ───────────────────────────────────────────────────────────
        total  = len(self._passed) + len(self._failed)
        passed = len(self._passed)
        print(f"\n{'═' * 66}")
        print(f"  OVERALL: {passed}/{total} TCs PASS  |  "
              f"5 techniques applied  |  "
              f"5 requirements covered")
        print(f"{'═' * 66}")


# ─── SIMULATED ECU ────────────────────────────────────────────────────────────

class SimulatedECU(threading.Thread):
    """
    ECU for Day 20.  Notable additions vs Day 19:
      - dtc_enabled flag (for MC/DC testing): when False, DTC P0217 is never set
        regardless of temperature, making the second MC/DC condition testable.
      - _fan_on flag: tracks fan state independently (enables decision-table tests
        where initial fan state is set directly via SIL).
      - DID 0xF186 (activeSession): returns current session byte.
      - DID 0xF406 (fanDuty): returns 100 if fan is on, 0 if off.
      - _update_control(): fan hysteresis + DTC — runs on every dispatch.
    """

    def __init__(self) -> None:
        super().__init__(daemon=True, name="SimECU")
        self.bus          = can.Bus(interface="virtual", channel=CHANNEL)
        self._stop        = threading.Event()
        self.session      = 0x01
        self._last_diag_t = time.monotonic()
        # ── SIL-controllable state ────────────────────────────────────────────
        self.test_temperature: float = 25.0
        self.dtc_enabled:      bool  = True    # MC/DC second condition
        self._fan_on:          bool  = False   # SIL: set directly for decision-table tests
        # ── Security state ────────────────────────────────────────────────────
        self._unlocked        = False
        self._seed            = 0
        self._seed_issued     = False
        self._fail_count      = 0
        self._lockout_until   = 0.0
        # ── DTC store ─────────────────────────────────────────────────────────
        self._dtcs: Dict[Tuple[int, int], int] = {}
        # ── Multi-frame receive state ─────────────────────────────────────────
        self._mf_active = False
        self._mf_total  = 0
        self._mf_buf:   List[int] = []

    def stop(self) -> None:
        self._stop.set()
        self.bus.shutdown()

    # ── ISO-TP send ───────────────────────────────────────────────────────────

    def _send(self, payload: list) -> None:
        if len(payload) <= 7:
            self.bus.send(can.Message(arbitration_id=TESTER_RX_ID,
                                      data=build_sf(payload),
                                      is_extended_id=False))
            return
        ff = build_ff(payload)
        self.bus.send(can.Message(arbitration_id=TESTER_RX_ID,
                                  data=ff, is_extended_id=False))
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            f = self.bus.recv(timeout=0.05)
            if f and f.arbitration_id == TESTER_TX_ID:
                break
        sn, offset = 1, 6
        while offset < len(payload):
            chunk = payload[offset: offset + 7]
            self.bus.send(can.Message(arbitration_id=TESTER_RX_ID,
                                      data=build_cf(sn, chunk),
                                      is_extended_id=False))
            sn     = (sn + 1) & 0x0F
            offset += 7
            time.sleep(0.005)

    def _neg(self, sid: int, nrc: int) -> None:
        self._send([SID_NEG, sid, nrc])

    # ── Control logic (fan hysteresis + DTC) ─────────────────────────────────

    def _update_control(self) -> None:
        # Fan hysteresis:  FAN_OFF_C < hysteresis_zone ≤ FAN_ON_C
        if self.test_temperature > FAN_ON_C:
            self._fan_on = True
        elif self.test_temperature < FAN_OFF_C:
            self._fan_on = False
        # else: hysteresis zone [FAN_OFF, FAN_ON] → no change

        # DTC P0217: strictly greater than OVER_TEMP_C AND dtc_enabled
        key = (DTC_P0217_H, DTC_P0217_L)
        if self.dtc_enabled and self.test_temperature > OVER_TEMP_C:
            self._dtcs[key] = DTC_CONFIRMED
        # DTCs removed only via 0x14 ClearDiagnosticInformation

    # ── UDS service handlers ──────────────────────────────────────────────────

    def _handle_session(self, sub: int) -> None:
        if sub not in (0x01, 0x02, 0x03):
            self._neg(SID_SESSION, NRC_SUBFUNC_NOT_SUPP); return
        self._unlocked    = False
        self._seed_issued = False
        self._fail_count  = 0
        self.session      = sub
        self._last_diag_t = time.monotonic()
        self._send([SID_SESSION + 0x40, sub, 0x00, 0x19, 0x01, 0xF4])

    def _handle_reset(self, sub: int) -> None:
        if sub not in (0x01, 0x02, 0x03):
            self._neg(SID_RESET, NRC_SUBFUNC_NOT_SUPP); return
        self._send([SID_RESET + 0x40, sub])
        def _do_reset():
            time.sleep(0.1)
            self.session      = 0x01
            self._unlocked    = False
            self._seed_issued = False
            self._fail_count  = 0
        threading.Thread(target=_do_reset, daemon=True).start()

    def _handle_security(self, uds: list) -> None:
        if self.session not in (0x02, 0x03):
            self._neg(SID_SEC, NRC_CONDITIONS_NOT_OK); return
        if time.monotonic() < self._lockout_until:
            self._neg(SID_SEC, NRC_EXCEEDED_ATTEMPTS); return
        sub = uds[1]
        if sub == 0x01:
            if self._unlocked:
                self._send([SID_SEC + 0x40, sub, 0, 0, 0, 0])
            else:
                seed              = random.randint(1, 0xFFFFFFFF)
                self._seed        = seed
                self._seed_issued = True
                self._send([SID_SEC + 0x40, sub,
                            (seed >> 24) & 0xFF, (seed >> 16) & 0xFF,
                            (seed >> 8) & 0xFF,  seed & 0xFF])
        elif sub == 0x02:
            if len(uds) < 6:
                self._neg(SID_SEC, 0x13); return
            if not self._seed_issued:
                self._neg(SID_SEC, 0x24); return
            self._seed_issued = False
            key      = struct.unpack(">I", bytes(uds[2:6]))[0]
            expected = self._seed ^ SEC_SECRET
            if key == expected:
                self._unlocked   = True
                self._fail_count = 0
                self._send([SID_SEC + 0x40, sub])
            else:
                self._fail_count += 1
                if self._fail_count >= SEC_MAX_ATTEMPTS:
                    self._lockout_until = time.monotonic() + 3.0
                    self._fail_count    = 0
                    self._neg(SID_SEC, NRC_EXCEEDED_ATTEMPTS)
                else:
                    self._neg(SID_SEC, NRC_INVALID_KEY)
        else:
            self._neg(SID_SEC, NRC_SUBFUNC_NOT_SUPP)

    def _handle_read_did(self, uds: list) -> None:
        if len(uds) < 3:
            self._neg(SID_READ_DID, 0x13); return
        did = (uds[1] << 8) | uds[2]
        if did == 0xF186:   # activeSession (ISO 14229 standardised DID)
            self._send([SID_READ_DID + 0x40, uds[1], uds[2], self.session])
        elif did == 0xF189:   # SW version
            ver = list("Day20-ECU-v1.0".encode("ascii"))
            self._send([SID_READ_DID + 0x40, uds[1], uds[2]] + ver)
        elif did == 0xF405:   # coolant temp × 10, 16-bit BE
            raw = int(self.test_temperature * 10)
            self._send([SID_READ_DID + 0x40, uds[1], uds[2],
                        (raw >> 8) & 0xFF, raw & 0xFF])
        elif did == 0xF406:   # fan duty: 0 or 100
            duty = 100 if self._fan_on else 0
            self._send([SID_READ_DID + 0x40, uds[1], uds[2], duty])
        else:
            self._neg(SID_READ_DID, NRC_OUT_OF_RANGE)

    def _handle_read_dtc(self, uds: list) -> None:
        sub = uds[1] if len(uds) >= 2 else 0
        if sub != 0x02:
            self._neg(SID_READ_DTC, NRC_SUBFUNC_NOT_SUPP); return
        mask    = uds[2] if len(uds) >= 3 else 0xFF
        payload = [SID_READ_DTC + 0x40, sub, 0xFF]
        for (h, l), status in self._dtcs.items():
            if status & mask:
                payload += [h, l, status]
        self._send(payload)

    def _handle_clr_dtc(self, uds: list) -> None:
        if (len(uds) >= 4
                and uds[1] == 0xFF and uds[2] == 0xFF and uds[3] == 0xFF):
            self._dtcs.clear()
            self._send([SID_CLR_DTC + 0x40])
        else:
            self._neg(SID_CLR_DTC, NRC_OUT_OF_RANGE)

    def _handle_tp(self, uds: list) -> None:
        if len(uds) >= 2 and (uds[1] & 0x80):
            return
        self._send([SID_TP + 0x40, uds[1] if len(uds) >= 2 else 0x00])

    # ── Dispatcher ────────────────────────────────────────────────────────────

    def _dispatch(self, uds: list) -> None:
        self._last_diag_t = time.monotonic()
        self._update_control()
        sid = uds[0]
        if   sid == SID_SESSION  and len(uds) >= 2: self._handle_session(uds[1])
        elif sid == SID_RESET    and len(uds) >= 2: self._handle_reset(uds[1])
        elif sid == SID_SEC      and len(uds) >= 2: self._handle_security(uds)
        elif sid == SID_READ_DID:                    self._handle_read_did(uds)
        elif sid == SID_READ_DTC:                    self._handle_read_dtc(uds)
        elif sid == SID_CLR_DTC:                     self._handle_clr_dtc(uds)
        elif sid == SID_TP:                          self._handle_tp(uds)
        else:
            self._neg(sid, 0x11)

    # ── Main loop ─────────────────────────────────────────────────────────────

    def run(self) -> None:
        while not self._stop.is_set():
            frame = self.bus.recv(timeout=0.02)
            if frame is None or frame.arbitration_id != TESTER_TX_ID:
                continue
            data     = bytes(frame.data)
            pci_type = (data[0] >> 4) & 0x0F
            if pci_type == 0:
                length = data[0] & 0x0F
                uds    = list(data[1: 1 + length])
                if uds:
                    self._dispatch(uds)
            elif pci_type == 1:
                self._mf_total  = ((data[0] & 0x0F) << 8) | data[1]
                self._mf_buf    = list(data[2:8])
                self._mf_active = True
                self.bus.send(can.Message(arbitration_id=TESTER_RX_ID,
                                          data=build_fc(),
                                          is_extended_id=False))
            elif pci_type == 2:
                if self._mf_active:
                    self._mf_buf += list(data[1:8])
                    if len(self._mf_buf) >= self._mf_total:
                        uds = self._mf_buf[:self._mf_total]
                        self._mf_active = False
                        if uds:
                            self._dispatch(uds)


# ─── UDS TESTER ───────────────────────────────────────────────────────────────

class UDSTester:
    TIMEOUT_S = 3.0
    STMIN_MS  = 5

    def __init__(self, bus: can.BusABC) -> None:
        self.bus = bus

    def shutdown(self) -> None:
        self.bus.shutdown()

    def _send(self, uds: list) -> None:
        if len(uds) <= 7:
            self.bus.send(can.Message(arbitration_id=TESTER_TX_ID,
                                      data=build_sf(uds), is_extended_id=False))
            return
        self.bus.send(can.Message(arbitration_id=TESTER_TX_ID,
                                  data=build_ff(uds), is_extended_id=False))
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            f = self.bus.recv(timeout=0.05)
            if f and f.arbitration_id == TESTER_RX_ID:
                break
        sn, offset = 1, 6
        while offset < len(uds):
            chunk = uds[offset: offset + 7]
            self.bus.send(can.Message(arbitration_id=TESTER_TX_ID,
                                      data=build_cf(sn, chunk),
                                      is_extended_id=False))
            sn     = (sn + 1) & 0x0F
            offset += 7
            time.sleep(self.STMIN_MS / 1000.0)

    def _recv(self, timeout: float = None) -> Optional[list]:
        deadline  = time.monotonic() + (timeout or self.TIMEOUT_S)
        collected = []
        total_exp = 0
        while time.monotonic() < deadline:
            remaining = max(0.01, deadline - time.monotonic())
            frame     = self.bus.recv(timeout=remaining)
            if frame is None or frame.arbitration_id != TESTER_RX_ID:
                continue
            fb       = frame.data[0]
            pci_type = (fb >> 4) & 0x0F
            if pci_type == 0:
                length = fb & 0x0F
                uds    = list(frame.data[1: 1 + length])
                if uds and uds[0] == SID_NEG and len(uds) >= 3 and uds[2] == 0x78:
                    deadline += 5.0
                    continue
                return uds
            elif pci_type == 1:
                total_exp = ((fb & 0x0F) << 8) | frame.data[1]
                collected = list(frame.data[2:])
                self.bus.send(can.Message(arbitration_id=TESTER_TX_ID,
                                          data=build_fc(0, self.STMIN_MS),
                                          is_extended_id=False))
            elif pci_type == 2:
                collected += list(frame.data[1:])
                if len(collected) >= total_exp:
                    return collected[:total_exp]
        return collected[:total_exp] if collected else None

    def sr(self, uds: list, timeout: float = None) -> Optional[list]:
        """send + recv."""
        self._send(uds)
        return self._recv(timeout=timeout)

    def switch_session(self, sub: int) -> None:
        self._send([SID_SESSION, sub])
        self._recv()


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _dtc_count(resp: Optional[list]) -> int:
    """Count DTCs in a ReadDTC response (3 bytes each after the 3-byte header)."""
    if resp and resp[0] == SID_READ_DTC + 0x40 and len(resp) >= 3:
        return (len(resp) - 3) // 3
    return -1

def _dtc_present(resp: Optional[list], h: int, l: int) -> bool:
    """Check if a specific DTC is in a ReadDTC response."""
    if resp is None or resp[0] != SID_READ_DTC + 0x40:
        return False
    section = resp[3:]
    i = 0
    while i + 2 < len(section):
        if section[i] == h and section[i + 1] == l:
            return True
        i += 3
    return False

def _clean_dtc_at(ecu: SimulatedECU, t: UDSTester, safe_temp: float = 25.0) -> None:
    """
    Set ECU to safe temp, then ClearDTC.
    Ensures _update_control() does NOT re-add the DTC during the clear command.
    """
    ecu.test_temperature = safe_temp
    t.sr([SID_CLR_DTC, 0xFF, 0xFF, 0xFF])

def _read_did_byte(t: UDSTester, did_hi: int, did_lo: int) -> Optional[int]:
    """Read a DID and return the first data byte, or None on failure."""
    resp = t.sr([SID_READ_DID, did_hi, did_lo])
    if resp and resp[0] == SID_READ_DID + 0x40 and len(resp) >= 4:
        return resp[3]
    return None


# ─── GROUP 1: EQUIVALENCE PARTITIONING (TC01–TC04) ────────────────────────────

def tc01_ep_valid_session(t: UDSTester, rpt: TestDesignReport) -> None:
    """EP valid class: session sub-function 0x02 ∈ {0x01, 0x02, 0x03} → positive response."""
    resp = t.sr([SID_SESSION, 0x02])
    ok = bool(resp and resp[0] == SID_SESSION + 0x40 and resp[1] == 0x02)
    rpt.ep.append(EPEntry("REQ-RBT-001", "valid_session",
                          "0x02 (extendedDiag)", "positive_response", "TC01", ok))
    if ok:
        rpt.pass_tc("TC01", "EP valid class: 0x10 0x02 → 0x50 0x02 ✓")
    else:
        rpt.fail_tc("TC01", f"resp={resp}")


def tc02_ep_invalid_zero(t: UDSTester, rpt: TestDesignReport) -> None:
    """EP invalid class: sub-function 0x00 ∉ {0x01,0x02,0x03} → NRC 0x12."""
    t.switch_session(0x01)
    resp = t.sr([SID_SESSION, 0x00])
    ok = bool(resp and resp[0] == SID_NEG
              and len(resp) >= 3
              and resp[1] == SID_SESSION and resp[2] == NRC_SUBFUNC_NOT_SUPP)
    rpt.ep.append(EPEntry("REQ-RBT-001", "invalid_zero",
                          "0x00", "NRC_0x12", "TC02", ok))
    if ok:
        rpt.pass_tc("TC02", "EP invalid class: 0x10 0x00 → NRC 0x12 ✓")
    else:
        rpt.fail_tc("TC02", f"resp={resp}")


def tc03_ep_valid_did(t: UDSTester, rpt: TestDesignReport) -> None:
    """EP valid class: DID 0xF189 (known) → positive response."""
    t.switch_session(0x01)
    resp = t.sr([SID_READ_DID, 0xF1, 0x89])
    ok = bool(resp and resp[0] == SID_READ_DID + 0x40)
    rpt.ep.append(EPEntry("REQ-RBT-001", "valid_DID",
                          "0xF189 (SW version)", "positive_response", "TC03", ok))
    if ok:
        rpt.pass_tc("TC03", "EP valid DID: 0x22 0xF189 → 0x62 ✓")
    else:
        rpt.fail_tc("TC03", f"resp={resp}")


def tc04_ep_invalid_did(t: UDSTester, rpt: TestDesignReport) -> None:
    """EP invalid class: DID 0xABCD (unknown) → NRC 0x31."""
    resp = t.sr([SID_READ_DID, 0xAB, 0xCD])
    ok = bool(resp and resp[0] == SID_NEG
              and len(resp) >= 3
              and resp[1] == SID_READ_DID and resp[2] == NRC_OUT_OF_RANGE)
    rpt.ep.append(EPEntry("REQ-RBT-001", "invalid_DID",
                          "0xABCD (unknown)", "NRC_0x31", "TC04", ok))
    if ok:
        rpt.pass_tc("TC04", "EP invalid DID: 0x22 0xABCD → NRC 0x31 ✓")
    else:
        rpt.fail_tc("TC04", f"resp={resp}")


# ─── GROUP 2: BOUNDARY VALUE ANALYSIS (TC05–TC08) ─────────────────────────────

def tc05_bva_dtc_below(t: UDSTester, ecu: SimulatedECU, rpt: TestDesignReport) -> None:
    """BVA: 104.9°C — just BELOW the strict > 105.0 threshold → no DTC."""
    _clean_dtc_at(ecu, t)
    ecu.test_temperature = 104.9
    t.sr([SID_TP, 0x00])   # trigger _update_control
    resp = t.sr([SID_READ_DTC, 0x02, 0xFF])
    count = _dtc_count(resp)
    ok = (count == 0)
    rpt.bva.append(BVAEntry("REQ-RBT-002", "below_threshold (104.9)",
                             "104.9°C", "no_DTC", "TC05", ok))
    if ok:
        rpt.pass_tc("TC05", "BVA 104.9°C: no DTC ✓  (below strict > 105.0)")
    else:
        rpt.fail_tc("TC05", f"expected 0 DTCs, got {count}")


def tc06_bva_dtc_at_boundary(t: UDSTester, ecu: SimulatedECU, rpt: TestDesignReport) -> None:
    """BVA: 105.0°C — AT threshold, but STRICTLY > means 105.0 does NOT trigger DTC."""
    _clean_dtc_at(ecu, t)
    ecu.test_temperature = 105.0
    t.sr([SID_TP, 0x00])
    resp = t.sr([SID_READ_DTC, 0x02, 0xFF])
    count = _dtc_count(resp)
    ok = (count == 0)
    rpt.bva.append(BVAEntry("REQ-RBT-002", "at_threshold (105.0)",
                             "105.0°C", "no_DTC (strict >)", "TC06", ok))
    if ok:
        rpt.pass_tc("TC06", "BVA 105.0°C: no DTC ✓  (105.0 is NOT strictly > 105.0)")
    else:
        rpt.fail_tc("TC06", f"REQ says STRICTLY > 105. Got {count} DTCs at 105.0°C")


def tc07_bva_dtc_above(t: UDSTester, ecu: SimulatedECU, rpt: TestDesignReport) -> None:
    """BVA: 105.1°C — just ABOVE threshold → DTC P0217 confirmed."""
    _clean_dtc_at(ecu, t)
    ecu.test_temperature = 105.1
    t.sr([SID_TP, 0x00])
    resp = t.sr([SID_READ_DTC, 0x02, 0xFF])
    found = _dtc_present(resp, DTC_P0217_H, DTC_P0217_L)
    ok = found
    rpt.bva.append(BVAEntry("REQ-RBT-002", "above_threshold (105.1)",
                             "105.1°C", "DTC_P0217_confirmed", "TC07", ok))
    if ok:
        rpt.pass_tc("TC07", "BVA 105.1°C: P0217 confirmed ✓")
    else:
        rpt.fail_tc("TC07", f"P0217 not found in response: {resp}")
    # Cleanup
    _clean_dtc_at(ecu, t)


def tc08_bva_sa_lockout_boundary(t: UDSTester, ecu: SimulatedECU,
                                  rpt: TestDesignReport) -> None:
    """
    BVA: SecurityAccess lockout boundary.
    Below boundary (2nd wrong key): NRC 0x35 (still has one more attempt).
    At boundary   (3rd wrong key):  NRC 0x36 (lockout triggered — boundary!).
    """
    t.switch_session(0x03)
    nrc_on_2nd = None
    nrc_on_3rd = None

    for attempt in range(1, SEC_MAX_ATTEMPTS + 1):
        resp_seed = t.sr([SID_SEC, 0x01])
        if resp_seed is None or resp_seed[0] != SID_SEC + 0x40:
            break
        resp_key = t.sr([SID_SEC, 0x02, 0xDE, 0xAD, 0x00, 0x00])   # deliberate wrong key
        if resp_key and resp_key[0] == SID_NEG and len(resp_key) >= 3:
            nrc = resp_key[2]
            if attempt == 2:
                nrc_on_2nd = nrc
            if attempt == 3:
                nrc_on_3rd = nrc

    below_ok = (nrc_on_2nd == NRC_INVALID_KEY)       # 0x35 — below lockout boundary
    at_ok    = (nrc_on_3rd == NRC_EXCEEDED_ATTEMPTS)  # 0x36 — at lockout boundary

    rpt.bva.append(BVAEntry("REQ-RBT-006", "below_lockout_boundary (2nd attempt)",
                             "attempt #2", "NRC_0x35", "TC08a", below_ok))
    rpt.bva.append(BVAEntry("REQ-RBT-006", "at_lockout_boundary (3rd attempt)",
                             "attempt #3", "NRC_0x36", "TC08b", at_ok))

    ok = below_ok and at_ok
    if ok:
        rpt.pass_tc("TC08",
                    "BVA SA lockout: 2nd→NRC 0x35 (below) / 3rd→NRC 0x36 (at boundary) ✓")
    else:
        rpt.fail_tc("TC08",
                    f"2nd={hex(nrc_on_2nd) if nrc_on_2nd else '?'}  "
                    f"3rd={hex(nrc_on_3rd) if nrc_on_3rd else '?'}")

    # SIL power: clear lockout so subsequent TCs are not blocked
    ecu._lockout_until = 0.0
    ecu._fail_count    = 0


# ─── GROUP 3: STATE TRANSITION TESTING (TC09–TC12) ────────────────────────────

def tc09_state_default_to_extended(t: UDSTester, rpt: TestDesignReport) -> None:
    """State transition: DEFAULT → EXTENDED via 0x10 0x03."""
    # Start: ensure DEFAULT
    t.switch_session(0x01)
    pre = _read_did_byte(t, 0xF1, 0x86)

    # Transition
    t.switch_session(0x03)
    post = _read_did_byte(t, 0xF1, 0x86)

    ok = (pre == 0x01 and post == 0x03)
    rpt.state.append(StateEntry("REQ-RBT-003", "DEFAULT", "EXTENDED",
                                "0x10 0x03", "TC09", ok))
    if ok:
        rpt.pass_tc("TC09", "State DEFAULT(0x01) → EXTENDED(0x03) ✓  (DID 0xF186 confirmed)")
    else:
        rpt.fail_tc("TC09", f"pre_session={pre}  post_session={post}")


def tc10_state_extended_to_programming(t: UDSTester, rpt: TestDesignReport) -> None:
    """State transition: EXTENDED → PROGRAMMING via 0x10 0x02."""
    # Pre-condition: should be EXTENDED from TC09
    pre = _read_did_byte(t, 0xF1, 0x86)

    t.switch_session(0x02)
    post = _read_did_byte(t, 0xF1, 0x86)

    ok = (pre == 0x03 and post == 0x02)
    rpt.state.append(StateEntry("REQ-RBT-003", "EXTENDED", "PROGRAMMING",
                                "0x10 0x02", "TC10", ok))
    if ok:
        rpt.pass_tc("TC10", "State EXTENDED(0x03) → PROGRAMMING(0x02) ✓")
    else:
        rpt.fail_tc("TC10", f"pre_session={pre}  post_session={post}")


def tc11_state_any_to_default_via_reset(t: UDSTester, rpt: TestDesignReport) -> None:
    """State transition: PROGRAMMING → DEFAULT via ECUReset 0x11 0x01."""
    pre = _read_did_byte(t, 0xF1, 0x86)

    resp = t.sr([SID_RESET, 0x01])
    if resp is None or resp[0] != SID_RESET + 0x40:
        rpt.state.append(StateEntry("REQ-RBT-003", "PROGRAMMING", "DEFAULT",
                                    "0x11 0x01", "TC11", False))
        rpt.fail_tc("TC11", f"reset response: {resp}"); return

    time.sleep(0.3)   # simulated reboot
    post = _read_did_byte(t, 0xF1, 0x86)

    ok = (post == 0x01)
    rpt.state.append(StateEntry("REQ-RBT-003", "PROGRAMMING", "DEFAULT",
                                "0x11 0x01 (hardReset)", "TC11", ok))
    if ok:
        rpt.pass_tc("TC11", f"State PROGRAMMING({pre}) → DEFAULT(0x01) via ECUReset ✓")
    else:
        rpt.fail_tc("TC11", f"post_session={post}")


def tc12_state_invalid_security_in_default(t: UDSTester, rpt: TestDesignReport) -> None:
    """
    Invalid state transition: SecurityAccess (0x27 0x01) in DEFAULT session
    → NRC 0x22 (conditionsNotCorrect).  SecurityAccess requires EXTENDED or PROGRAMMING.
    """
    # Confirm we are in DEFAULT (from TC11 reset)
    session = _read_did_byte(t, 0xF1, 0x86)
    if session != 0x01:
        t.switch_session(0x01)

    resp = t.sr([SID_SEC, 0x01])
    ok = bool(resp and resp[0] == SID_NEG
              and len(resp) >= 3 and resp[2] == NRC_CONDITIONS_NOT_OK)
    rpt.state.append(StateEntry("REQ-RBT-003", "DEFAULT", "BLOCKED",
                                "0x27 0x01 (requires non-default)", "TC12", ok))
    if ok:
        rpt.pass_tc("TC12",
                    "Invalid transition: SecurityAccess in DEFAULT → NRC 0x22 ✓")
    else:
        rpt.fail_tc("TC12", f"expected NRC 0x22, got {resp}")


# ─── GROUP 4: DECISION TABLE TESTING (TC13–TC16) ──────────────────────────────
#
# Decision table for fan hysteresis controller:
#
#  Condition 1: temp > FAN_ON_C (90°C)
#  Condition 2: fan_was_ON
#  ┌───────────────────┬───────┬───────┬───────┬───────┐
#  │ Condition 1 (>90) │  F    │  T    │  F    │  F    │
#  │ Condition 2 (on)  │  F    │  F    │  T    │  T    │
#  │ Also: temp < 85?  │  Yes  │  No   │  No   │  Yes  │
#  ├───────────────────┼───────┼───────┼───────┼───────┤
#  │ Fan output        │  OFF  │  ON   │ ON(hy)│  OFF  │
#  │ Test case         │ TC13  │ TC14  │ TC15  │ TC16  │
#  └───────────────────┴───────┴───────┴───────┴───────┘

def tc13_decision_fan_off_below_fanon(t: UDSTester, ecu: SimulatedECU,
                                       rpt: TestDesignReport) -> None:
    """DT Row 1: temp=70°C (< FAN_ON), fan_was=OFF → fan stays OFF."""
    ecu.test_temperature = 70.0
    ecu._fan_on          = False
    t.sr([SID_TP, 0x00])   # trigger _update_control
    duty = _read_did_byte(t, 0xF4, 0x06)
    ok   = (duty == 0)
    rpt.decision.append(DecisionEntry("REQ-RBT-004",
                                       "(temp=70<90, fan_was=OFF)",
                                       "fan=OFF (duty=0)", "TC13", ok))
    if ok:
        rpt.pass_tc("TC13", "DT Row 1: (temp=70, fan_was=OFF) → fan=OFF ✓")
    else:
        rpt.fail_tc("TC13", f"duty={duty}")


def tc14_decision_fan_on_above_fanon(t: UDSTester, ecu: SimulatedECU,
                                      rpt: TestDesignReport) -> None:
    """DT Row 2: temp=95°C (> FAN_ON=90), fan_was=OFF → fan turns ON."""
    ecu.test_temperature = 95.0
    ecu._fan_on          = False
    t.sr([SID_TP, 0x00])
    duty = _read_did_byte(t, 0xF4, 0x06)
    ok   = (duty == 100)
    rpt.decision.append(DecisionEntry("REQ-RBT-004",
                                       "(temp=95>90, fan_was=OFF)",
                                       "fan=ON (duty=100)", "TC14", ok))
    if ok:
        rpt.pass_tc("TC14", "DT Row 2: (temp=95, fan_was=OFF) → fan=ON ✓")
    else:
        rpt.fail_tc("TC14", f"duty={duty}")


def tc15_decision_fan_hysteresis(t: UDSTester, ecu: SimulatedECU,
                                   rpt: TestDesignReport) -> None:
    """
    DT Row 3: temp=87°C (FAN_OFF=85 < 87 ≤ FAN_ON=90), fan_was=ON
    → fan STAYS ON (hysteresis dead band — no switching).
    """
    ecu.test_temperature = 87.0
    ecu._fan_on          = True    # was already on
    t.sr([SID_TP, 0x00])
    duty = _read_did_byte(t, 0xF4, 0x06)
    ok   = (duty == 100)
    rpt.decision.append(DecisionEntry("REQ-RBT-004",
                                       "(temp=87, 85<87≤90, fan_was=ON)",
                                       "fan=ON (hysteresis)", "TC15", ok))
    if ok:
        rpt.pass_tc("TC15", "DT Row 3: (temp=87, fan_was=ON) → fan STAYS ON ✓ (hysteresis)")
    else:
        rpt.fail_tc("TC15", f"duty={duty}  (expected hysteresis to hold fan ON)")


def tc16_decision_fan_off_below_fanoff(t: UDSTester, ecu: SimulatedECU,
                                        rpt: TestDesignReport) -> None:
    """DT Row 4: temp=80°C (< FAN_OFF=85), fan_was=ON → fan turns OFF."""
    ecu.test_temperature = 80.0
    ecu._fan_on          = True    # was on
    t.sr([SID_TP, 0x00])
    duty = _read_did_byte(t, 0xF4, 0x06)
    ok   = (duty == 0)
    rpt.decision.append(DecisionEntry("REQ-RBT-004",
                                       "(temp=80<85, fan_was=ON)",
                                       "fan=OFF (duty=0)", "TC16", ok))
    if ok:
        rpt.pass_tc("TC16", "DT Row 4: (temp=80, fan_was=ON) → fan=OFF ✓")
    else:
        rpt.fail_tc("TC16", f"duty={duty}")


# ─── GROUP 5: MC/DC COVERAGE (TC17–TC20) ──────────────────────────────────────
#
# ISO 26262 ASIL-D mandates MC/DC (Modified Condition/Decision Coverage).
# For a compound boolean expression, each condition must independently
# affect the outcome in at least one test case.
#
# Expression under test:  should_set_dtc = temp > 105.0  AND  dtc_enabled
#
# MC/DC test set (minimal):
#   TC17  (T, T) → True   [reference case]
#   TC18  (F, T) → False  [temp condition independently affects outcome]
#   TC19  (T, F) → False  [dtc_enabled condition independently affects outcome]
#
# TC17 vs TC18: same dtc_enabled, different temp → temp changes outcome ✓
# TC17 vs TC19: same temp,         different dtc → dtc changes outcome ✓

def tc17_mcdv_reference(t: UDSTester, ecu: SimulatedECU, rpt: TestDesignReport) -> None:
    """MC/DC reference: temp=110°C (T), dtc_enabled=True (T) → DTC confirmed (True)."""
    _clean_dtc_at(ecu, t)
    ecu.dtc_enabled      = True
    ecu.test_temperature = 110.0
    t.sr([SID_TP, 0x00])
    resp  = t.sr([SID_READ_DTC, 0x02, 0xFF])
    found = _dtc_present(resp, DTC_P0217_H, DTC_P0217_L)
    rpt.mcdv.append(MCDVEntry("REQ-RBT-005", "reference",
                               "(T,T)→T : temp=110 > 105, dtc_en=True",
                               "TC17", True, found))
    if found:
        rpt.pass_tc("TC17", "MC/DC (T,T)→True: temp=110>105 AND dtc_en=True → DTC ✓")
    else:
        rpt.fail_tc("TC17", f"DTC not found: {resp}")


def tc18_mcdv_vary_temp(t: UDSTester, ecu: SimulatedECU, rpt: TestDesignReport) -> None:
    """MC/DC: temp=100°C (F), dtc_enabled=True (T) → no DTC (False). Temp is deciding factor."""
    _clean_dtc_at(ecu, t)
    ecu.dtc_enabled      = True
    ecu.test_temperature = 100.0   # below threshold: temp condition → False
    t.sr([SID_TP, 0x00])
    resp  = t.sr([SID_READ_DTC, 0x02, 0xFF])
    found = _dtc_present(resp, DTC_P0217_H, DTC_P0217_L)
    ok    = not found
    rpt.mcdv.append(MCDVEntry("REQ-RBT-005", "temp_condition",
                               "(F,T)→F : temp=100 < 105, dtc_en=True",
                               "TC18", False, ok))
    if ok:
        rpt.pass_tc("TC18", "MC/DC (F,T)→False: temp condition is deciding factor ✓")
    else:
        rpt.fail_tc("TC18", f"DTC unexpectedly found at 100°C")


def tc19_mcdv_vary_dtc_enabled(t: UDSTester, ecu: SimulatedECU,
                                 rpt: TestDesignReport) -> None:
    """
    MC/DC: temp=110°C (T), dtc_enabled=False (F) → no DTC (False).
    dtc_enabled flag is the deciding factor.
    """
    _clean_dtc_at(ecu, t)
    ecu.dtc_enabled      = False   # disable DTC regardless of temperature
    ecu.test_temperature = 110.0   # temp is above threshold
    t.sr([SID_TP, 0x00])
    resp  = t.sr([SID_READ_DTC, 0x02, 0xFF])
    found = _dtc_present(resp, DTC_P0217_H, DTC_P0217_L)
    ok    = not found
    rpt.mcdv.append(MCDVEntry("REQ-RBT-005", "dtc_enabled_condition",
                               "(T,F)→F : temp=110 > 105, dtc_en=False",
                               "TC19", False, ok))
    if ok:
        rpt.pass_tc("TC19", "MC/DC (T,F)→False: dtc_enabled is deciding factor ✓")
    else:
        rpt.fail_tc("TC19", f"DTC unexpectedly set when dtc_enabled=False")
    # Restore
    ecu.dtc_enabled = True
    _clean_dtc_at(ecu, t)


def tc20_coverage_report(rpt: TestDesignReport) -> None:
    """Print the full test design coverage report and declare 20/20."""
    rpt.print_coverage()
    passed, failed = rpt.summary()
    if failed == 0:
        rpt.pass_tc("TC20",
                    "All test design techniques applied; coverage report generated ✓")
    else:
        rpt.fail_tc("TC20",
                    f"{failed} TC(s) failed — coverage report generated with failures")


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def banner(title: str) -> None:
    print(f"\n{'─' * 66}\n  {title}\n{'─' * 66}")


def main() -> None:
    print("\n" + "📋🔬  " * 10)
    print("  Day 20 — Requirement-Based Testing")
    print("  EP · BVA · State Transition · Decision Table · MC/DC")
    print("📋🔬  " * 10)

    rpt = TestDesignReport()
    ecu = SimulatedECU()
    bus = can.Bus(interface="virtual", channel=CHANNEL)
    t   = UDSTester(bus)

    ecu.start()
    time.sleep(0.1)

    print(f"\n  Requirements under test: 6")
    print(f"    REQ-RBT-001  Session sub-function validation     [ASIL-B]  → EP")
    print(f"    REQ-RBT-002  DTC P0217 at strictly > 105°C       [ASIL-C]  → BVA")
    print(f"    REQ-RBT-003  ECU session state machine            [ASIL-B]  → State Transition")
    print(f"    REQ-RBT-004  Fan hysteresis control               [ASIL-B]  → Decision Table")
    print(f"    REQ-RBT-005  DTC set condition (2-condition expr) [ASIL-D]  → MC/DC")
    print(f"    REQ-RBT-006  SecurityAccess lockout at attempt 3  [ASIL-C]  → BVA")

    banner("GROUP 1: Equivalence Partitioning  [ASIL-B]")
    print("  Technique: divide input space into classes where all values")
    print("  behave identically; pick ONE representative per class.\n")
    tc01_ep_valid_session(t, rpt)
    tc02_ep_invalid_zero(t, rpt)
    tc03_ep_valid_did(t, rpt)
    tc04_ep_invalid_did(t, rpt)

    banner("GROUP 2: Boundary Value Analysis  [ASIL-C]")
    print("  Technique: test at the boundary itself, just below, and just above.")
    print("  Most implementation bugs live at boundaries, not in the middle.\n")
    tc05_bva_dtc_below(t, ecu, rpt)
    tc06_bva_dtc_at_boundary(t, ecu, rpt)
    tc07_bva_dtc_above(t, ecu, rpt)
    tc08_bva_sa_lockout_boundary(t, ecu, rpt)

    banner("GROUP 3: State Transition Testing  [ASIL-B]")
    print("  Technique: model the ECU as a state machine; test every valid")
    print("  transition AND at least one invalid/blocked transition.\n")
    tc09_state_default_to_extended(t, rpt)
    tc10_state_extended_to_programming(t, rpt)
    tc11_state_any_to_default_via_reset(t, rpt)
    tc12_state_invalid_security_in_default(t, rpt)

    banner("GROUP 4: Decision Table Testing  [ASIL-B]")
    print("  Technique: enumerate all meaningful combinations of input conditions.")
    print("  Fan controller: 4 combinations of (temp_zone, was_fan_on) → fan_state.\n")
    tc13_decision_fan_off_below_fanon(t, ecu, rpt)
    tc14_decision_fan_on_above_fanon(t, ecu, rpt)
    tc15_decision_fan_hysteresis(t, ecu, rpt)
    tc16_decision_fan_off_below_fanoff(t, ecu, rpt)

    banner("GROUP 5: MC/DC Coverage  [ASIL-D — ISO 26262 mandate]")
    print("  Technique: every boolean condition in a compound expression must")
    print("  independently affect the outcome in at least one test case.")
    print("  Expression: temp > 105.0  AND  dtc_enabled\n")
    tc17_mcdv_reference(t, ecu, rpt)
    tc18_mcdv_vary_temp(t, ecu, rpt)
    tc19_mcdv_vary_dtc_enabled(t, ecu, rpt)
    tc20_coverage_report(rpt)

    passed, failed = rpt.summary()
    total = passed + failed
    print(f"\n{'=' * 66}")
    print(f"  TEST SUMMARY: {passed}/{total} TCs pass  |  {failed} fail")
    print(f"{'=' * 66}")

    ecu.stop()
    t.shutdown()


if __name__ == "__main__":
    main()
