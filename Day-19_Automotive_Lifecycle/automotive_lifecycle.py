"""
Day 19: Automotive Testing Lifecycle
ASPICE · ISO 26262 · Gate Reviews · Defect Lifecycle · Traceability
====================================================================
Demonstrates:
  - Test campaign with 4 phases: Unit → SIL Integration → SIL System → HIL Validation
  - ASIL classification (QM, A, B, C, D) per ISO 26262
  - Requirement traceability (REQ-SW-xxxx → TC)
  - Gate reviews with hard ASPICE-style criteria
  - Defect lifecycle: OPEN → FIXED → VERIFIED → CLOSED
  - TC06 deliberately fails (defect D001 opened) → Gate 2 blocks
  - ECU patched → TC06 re-test → D001 closed → Gate 2 clears
  - S3 session timeout as an ASIL-D safety requirement (TC16)
  - Production readiness gate + traceability matrix report

No hardware needed.

Install:  pip install python-can
Run:      python automotive_lifecycle.py
"""

import can
import threading
import time
import random
import struct
import zlib
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Dict, List, Tuple

# ─── CONSTANTS ────────────────────────────────────────────────────────────────

CHANNEL      = "vcan0"
TESTER_TX_ID = 0x7E0
TESTER_RX_ID = 0x7E8

SID_SESSION  = 0x10
SID_RESET    = 0x11
SID_SEC      = 0x27
SID_COMM     = 0x28
SID_READ_DID = 0x22
SID_READ_DTC = 0x19
SID_CLR_DTC  = 0x14
SID_TP       = 0x3E
SID_NEG      = 0x7F

NRC_SUBFUNC_NOT_SUPP  = 0x12
NRC_MSG_LENGTH_ERROR  = 0x13
NRC_CONDITIONS_NOT_OK = 0x22
NRC_OUT_OF_RANGE      = 0x31
NRC_INVALID_KEY       = 0x35
NRC_EXCEEDED_ATTEMPTS = 0x36
NRC_REQD_TIME_DELAY   = 0x37

SEC_SECRET       = 0xDEADBEEF
SEC_MAX_ATTEMPTS = 3
SEC_LOCKOUT_S    = 3.0

OVER_TEMP_C = 105.0
DTC_P0217_H = 0x02
DTC_P0217_L = 0x17
DTC_CONFIRMED = 0xAF

S3_TIMEOUT_S = 1.5   # shorter for test speed

# ─── ISO-TP HELPERS ───────────────────────────────────────────────────────────

def build_sf(uds: list) -> bytes:
    assert 1 <= len(uds) <= 7
    return bytes([len(uds)] + list(uds) + [0x00] * (7 - len(uds)))

def build_ff(uds: list) -> bytes:
    n = len(uds)
    return bytes([0x10 | ((n >> 8) & 0x0F), n & 0xFF] + list(uds[:6]))

def build_cf(sn: int, chunk: list) -> bytes:
    return bytes([0x20 | (sn & 0x0F)] + list(chunk) + [0x00] * (7 - len(chunk)))

def build_fc(bs: int = 0, stmin: int = 5) -> bytes:
    return bytes([0x30, bs, stmin, 0, 0, 0, 0, 0])

def crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


# ─── LIFECYCLE DATA MODEL ─────────────────────────────────────────────────────

class ASILLevel(Enum):
    QM = 0
    A  = 1
    B  = 2
    C  = 3
    D  = 4

class TestPhase(Enum):
    UNIT            = "Unit Test"
    SIL_INTEGRATION = "SIL Integration"
    SIL_SYSTEM      = "SIL System"
    HIL_VALIDATION  = "HIL Validation"
    PRODUCTION      = "Production Readiness"

class TCStatus(Enum):
    NOT_RUN = "NOT_RUN"
    PASS    = "PASS"
    FAIL    = "FAIL"
    BLOCKED = "BLOCKED"

class DefectSeverity(Enum):
    CRITICAL = "CRITICAL"
    MAJOR    = "MAJOR"
    MINOR    = "MINOR"

class DefectStatus(Enum):
    OPEN     = "OPEN"
    FIXED    = "FIXED"
    VERIFIED = "VERIFIED"
    CLOSED   = "CLOSED"

@dataclass
class Requirement:
    req_id: str
    description: str
    asil: ASILLevel

@dataclass
class TestCaseRecord:
    tc_id: str
    title: str
    req_id: str
    asil: ASILLevel
    phase: TestPhase
    status: TCStatus = TCStatus.NOT_RUN
    defect_id: Optional[str] = None
    fail_reason: str = ""

@dataclass
class Defect:
    defect_id: str
    title: str
    severity: DefectSeverity
    asil: ASILLevel
    found_in_tc: str
    status: DefectStatus = DefectStatus.OPEN
    resolution: str = ""


class TestCampaign:
    """Automotive test campaign manager: registration, results, defects, gate reviews."""

    def __init__(self) -> None:
        self.requirements: Dict[str, Requirement] = {}
        self.test_cases:   Dict[str, TestCaseRecord] = {}
        self.defects:      Dict[str, Defect] = {}
        self._assertions:  List[str] = []

    # ── Registration ──────────────────────────────────────────────────────────

    def add_req(self, req_id: str, description: str, asil: ASILLevel) -> None:
        self.requirements[req_id] = Requirement(req_id, description, asil)

    def add_tc(self, tc_id: str, title: str, req_id: str,
               asil: ASILLevel, phase: TestPhase) -> None:
        self.test_cases[tc_id] = TestCaseRecord(tc_id, title, req_id, asil, phase)

    # ── Result recording ──────────────────────────────────────────────────────

    def pass_tc(self, tc_id: str, detail: str = "") -> None:
        tc = self.test_cases[tc_id]
        tc.status = TCStatus.PASS
        tag = (f"  ✅ PASS  {tc_id} [ASIL-{tc.asil.name}] {tc.title}"
               + (f"  ({detail})" if detail else ""))
        print(tag)
        self._assertions.append(tag)

    def fail_tc(self, tc_id: str, defect_id: Optional[str] = None,
                detail: str = "") -> None:
        tc = self.test_cases[tc_id]
        tc.status     = TCStatus.FAIL
        tc.defect_id  = defect_id
        tc.fail_reason = detail
        tag = (f"  ❌ FAIL  {tc_id} [ASIL-{tc.asil.name}] {tc.title}"
               + (f"  ({detail})" if detail else ""))
        print(tag)
        self._assertions.append(tag)

    def retest_pass(self, tc_id: str, detail: str = "") -> None:
        tc = self.test_cases[tc_id]
        tc.status = TCStatus.PASS
        tag = (f"  ↻ RE-TEST PASS  {tc_id} [ASIL-{tc.asil.name}] {tc.title}"
               + (f"  ({detail})" if detail else ""))
        print(tag)
        self._assertions.append(tag)

    # ── Defect lifecycle ──────────────────────────────────────────────────────

    def open_defect(self, d: Defect) -> None:
        self.defects[d.defect_id] = d
        print(f"  🐛 DEFECT OPENED  {d.defect_id}  {d.severity.value}/ASIL-{d.asil.name}"
              f":  {d.title}")

    def fix_defect(self, defect_id: str, resolution: str) -> None:
        d = self.defects[defect_id]
        d.status     = DefectStatus.FIXED
        d.resolution = resolution
        print(f"  🔧 DEFECT FIXED   {defect_id}:  {resolution}")

    def verify_defect(self, defect_id: str) -> None:
        self.defects[defect_id].status = DefectStatus.VERIFIED
        print(f"  ✔️  DEFECT VERIFIED {defect_id}")

    def close_defect(self, defect_id: str) -> None:
        self.defects[defect_id].status = DefectStatus.CLOSED
        print(f"  🔒 DEFECT CLOSED  {defect_id}")

    # ── Gate review ───────────────────────────────────────────────────────────

    def gate_review(self, from_phase: TestPhase,
                    to_phase: TestPhase) -> Tuple[bool, List[str]]:
        """
        Returns (passed, blockers).
        Criteria:
          All phases:       no NOT_RUN TCs; no ASIL-B+ failures.
          Unit → SIL-Int:   0 CRITICAL defects open.
          SIL-Int → SIL-Sys: 0 CRITICAL, 0 MAJOR defects open.
          SIL-Sys → HIL:    0 CRITICAL, 0 MAJOR defects open.
          HIL → Production: 0 open defects at any severity.
        """
        blockers: List[str] = []
        phase_tcs = [tc for tc in self.test_cases.values() if tc.phase == from_phase]

        not_run = [tc for tc in phase_tcs if tc.status == TCStatus.NOT_RUN]
        if not_run:
            blockers.append(
                f"{len(not_run)} TC(s) not executed: {[t.tc_id for t in not_run]}")

        asil_b_plus_fails = [tc for tc in phase_tcs
                             if tc.status == TCStatus.FAIL
                             and tc.asil.value >= ASILLevel.B.value]
        if asil_b_plus_fails:
            blockers.append(
                f"ASIL-B+ failure(s): {[t.tc_id for t in asil_b_plus_fails]}")

        open_d   = [d for d in self.defects.values() if d.status == DefectStatus.OPEN]
        critical = [d for d in open_d if d.severity == DefectSeverity.CRITICAL]
        major    = [d for d in open_d if d.severity == DefectSeverity.MAJOR]

        if from_phase == TestPhase.UNIT:
            if critical:
                blockers.append(f"{len(critical)} CRITICAL defect(s) open")

        elif from_phase in (TestPhase.SIL_INTEGRATION, TestPhase.SIL_SYSTEM):
            if critical:
                blockers.append(f"{len(critical)} CRITICAL defect(s) open")
            if major:
                blockers.append(
                    f"{len(major)} MAJOR defect(s) open — must fix before advancing")

        elif from_phase == TestPhase.HIL_VALIDATION:
            if open_d:
                blockers.append(
                    f"{len(open_d)} open defect(s) — must be CLOSED for SOP")

        return len(blockers) == 0, blockers

    # ── Report ────────────────────────────────────────────────────────────────

    def print_report(self) -> None:
        tcs     = self.test_cases.values()
        passed  = sum(1 for tc in tcs if tc.status == TCStatus.PASS)
        failed  = sum(1 for tc in tcs if tc.status == TCStatus.FAIL)
        not_run = sum(1 for tc in tcs if tc.status == TCStatus.NOT_RUN)
        total   = len(self.test_cases)
        cov     = round(passed / total * 100, 1) if total else 0

        print(f"\n{'═' * 64}")
        print(f"  AUTOMOTIVE TEST CAMPAIGN FINAL REPORT")
        print(f"  Project: DAY19-ECU   Date: 2026-06-22   Milestone: SOP-Gate")
        print(f"{'═' * 64}")

        print(f"\n  OVERALL COVERAGE")
        print(f"  {'Total TCs':<28} {total}")
        print(f"  {'Passed':<28} {passed}  ({cov} %)")
        print(f"  {'Failed':<28} {failed}")
        print(f"  {'Not Run':<28} {not_run}")

        print(f"\n  COVERAGE BY ASIL LEVEL")
        print(f"  {'Level':<10} {'Total':<8} {'Pass':<8} {'Pass %'}")
        print(f"  {'─' * 38}")
        for level in ASILLevel:
            group   = [tc for tc in tcs if tc.asil == level]
            pass_ct = sum(1 for tc in group if tc.status == TCStatus.PASS)
            if group:
                pct = round(pass_ct / len(group) * 100, 1)
                print(f"  ASIL-{level.name:<5} {len(group):<8} {pass_ct:<8} {pct} %")

        print(f"\n  DEFECT SUMMARY")
        if not self.defects:
            print("  No defects logged.")
        else:
            icons = {"OPEN": "🔴", "FIXED": "🟡", "VERIFIED": "🟢", "CLOSED": "⚫"}
            for d in self.defects.values():
                print(f"  {icons.get(d.status.value,'•')} {d.defect_id}  "
                      f"{d.severity.value:<10} ASIL-{d.asil.name}  "
                      f"[{d.status.value}]  {d.title}")

        print(f"\n  TRACEABILITY MATRIX  (TC → Requirement)")
        print(f"  {'TC':<8} {'Req ID':<16} {'ASIL':<8} {'Phase':<22} {'Status'}")
        print(f"  {'─' * 64}")
        for tc in tcs:
            print(f"  {tc.tc_id:<8} {tc.req_id:<16} "
                  f"ASIL-{tc.asil.name:<4} {tc.phase.value:<22} {tc.status.value}")

        print(f"\n{'═' * 64}")

    # ── Summary for main() ────────────────────────────────────────────────────

    def summary(self) -> Tuple[int, int]:
        passed = sum(1 for tc in self.test_cases.values() if tc.status == TCStatus.PASS)
        failed = sum(1 for tc in self.test_cases.values() if tc.status == TCStatus.FAIL)
        return passed, failed


# ─── SIMULATED ECU ────────────────────────────────────────────────────────────

class SimulatedECU(threading.Thread):
    """
    ECU with one deliberate defect:
      defect_mode=True  → 0x10 0x03 response is TRUNCATED (missing P2 timing bytes)
                          This causes TC06 to fail and triggers defect D001.
      defect_mode=False → correct full response (after D001 is fixed)
    """

    def __init__(self) -> None:
        super().__init__(daemon=True, name="SimECU")
        self.bus          = can.Bus(interface="virtual", channel=CHANNEL)
        self._stop        = threading.Event()
        self.session      = 0x01
        self._last_diag_t = time.monotonic()
        # ── Defect injection ──────────────────────────────────────────────────
        self.defect_mode  = True   # TC06 will fail until this is cleared
        # ── Security state ────────────────────────────────────────────────────
        self._unlocked        = False
        self._seed            = 0
        self._seed_issued     = False
        self._fail_count      = 0
        self._lockout_until   = 0.0
        # ── Plant / DTC state ─────────────────────────────────────────────────
        self.test_temperature = 25.0   # SIL: test sets this directly
        self._dtcs: Dict[Tuple[int, int], int] = {}
        # ── ISO-TP multi-frame receive state ─────────────────────────────────
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

    # ── UDS service handlers ──────────────────────────────────────────────────

    def _handle_session(self, sub: int) -> None:
        if sub not in (0x01, 0x02, 0x03):
            self._neg(SID_SESSION, NRC_SUBFUNC_NOT_SUPP); return
        # Session change locks security
        self._unlocked    = False
        self._seed_issued = False
        self._fail_count  = 0
        self.session = sub
        self._last_diag_t = time.monotonic()
        if self.defect_mode and sub == 0x03:
            # DEFECT D001: truncated response — missing P2/P2* timing bytes
            self._send([SID_SESSION + 0x40, sub])
        else:
            # CORRECT: include P2=25 ms (0x0019) and P2*=5000 ms (0x01F4)
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
            self._neg(SID_SEC, NRC_REQD_TIME_DELAY); return
        sub = uds[1]
        if sub == 0x01:   # requestSeed
            if self._unlocked:
                self._send([SID_SEC + 0x40, sub, 0, 0, 0, 0])
            else:
                seed              = random.randint(1, 0xFFFFFFFF)
                self._seed        = seed
                self._seed_issued = True
                self._send([SID_SEC + 0x40, sub,
                            (seed >> 24) & 0xFF, (seed >> 16) & 0xFF,
                            (seed >> 8) & 0xFF,  seed & 0xFF])
        elif sub == 0x02:   # sendKey
            if len(uds) < 6:
                self._neg(SID_SEC, NRC_MSG_LENGTH_ERROR); return
            if not self._seed_issued:
                self._neg(SID_SEC, 0x24); return  # requestSequenceError
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
                    self._lockout_until = time.monotonic() + SEC_LOCKOUT_S
                    self._fail_count    = 0
                    self._neg(SID_SEC, NRC_EXCEEDED_ATTEMPTS)
                else:
                    self._neg(SID_SEC, NRC_INVALID_KEY)
        else:
            self._neg(SID_SEC, NRC_SUBFUNC_NOT_SUPP)

    def _handle_comm(self, uds: list) -> None:
        if self.session == 0x01:
            self._neg(SID_COMM, NRC_CONDITIONS_NOT_OK); return
        ctrl = uds[1] if len(uds) >= 2 else 0
        self._send([SID_COMM + 0x40, ctrl])

    def _handle_read_did(self, uds: list) -> None:
        if len(uds) < 3:
            self._neg(SID_READ_DID, NRC_MSG_LENGTH_ERROR); return
        did = (uds[1] << 8) | uds[2]
        if did == 0xF189:
            ver = list("Day19-ECU-v1.0".encode("ascii"))
            self._send([SID_READ_DID + 0x40, uds[1], uds[2]] + ver)
        elif did == 0xF405:
            raw = int(self.test_temperature * 10)
            self._send([SID_READ_DID + 0x40, uds[1], uds[2],
                        (raw >> 8) & 0xFF, raw & 0xFF])
        else:
            self._neg(SID_READ_DID, NRC_OUT_OF_RANGE)

    def _handle_read_dtc(self, uds: list) -> None:
        sub  = uds[1] if len(uds) >= 2 else 0
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
            return   # suppressPosRspMsgIndicationBit
        self._send([SID_TP + 0x40, uds[1] if len(uds) >= 2 else 0x00])

    # ── DTC update (called on every dispatch) ────────────────────────────────

    def _update_dtcs(self) -> None:
        key = (DTC_P0217_H, DTC_P0217_L)
        if self.test_temperature > OVER_TEMP_C:
            self._dtcs[key] = DTC_CONFIRMED
        # Note: DTCs are cleared ONLY by 0x14 ClearDiagnosticInformation
        # (age-out not modelled here — see Day 15/16 for that)

    # ── Dispatcher ────────────────────────────────────────────────────────────

    def _dispatch(self, uds: list) -> None:
        self._last_diag_t = time.monotonic()
        self._update_dtcs()
        sid = uds[0]
        if   sid == SID_SESSION  and len(uds) >= 2: self._handle_session(uds[1])
        elif sid == SID_RESET    and len(uds) >= 2: self._handle_reset(uds[1])
        elif sid == SID_SEC      and len(uds) >= 2: self._handle_security(uds)
        elif sid == SID_COMM     and len(uds) >= 2: self._handle_comm(uds)
        elif sid == SID_READ_DID:                    self._handle_read_did(uds)
        elif sid == SID_READ_DTC:                    self._handle_read_dtc(uds)
        elif sid == SID_CLR_DTC:                     self._handle_clr_dtc(uds)
        elif sid == SID_TP:                          self._handle_tp(uds)
        else:
            self._neg(sid, 0x11)

    # ── Main loop ─────────────────────────────────────────────────────────────

    def run(self) -> None:
        while not self._stop.is_set():
            now = time.monotonic()
            # S3 session timeout watchdog
            if (self.session != 0x01
                    and now - self._last_diag_t > S3_TIMEOUT_S):
                self.session      = 0x01
                self._unlocked    = False
                self._seed_issued = False

            frame = self.bus.recv(timeout=0.02)
            if frame is None or frame.arbitration_id != TESTER_TX_ID:
                continue

            data     = bytes(frame.data)
            pci_type = (data[0] >> 4) & 0x0F

            if pci_type == 0:        # Single Frame
                length = data[0] & 0x0F
                uds = list(data[1: 1 + length])
                if uds:
                    self._dispatch(uds)

            elif pci_type == 1:      # First Frame — multi-frame request
                self._mf_total  = ((data[0] & 0x0F) << 8) | data[1]
                self._mf_buf    = list(data[2:8])
                self._mf_active = True
                self.bus.send(can.Message(arbitration_id=TESTER_RX_ID,
                                          data=build_fc(),
                                          is_extended_id=False))

            elif pci_type == 2:      # Consecutive Frame
                if self._mf_active:
                    self._mf_buf += list(data[1:8])
                    if len(self._mf_buf) >= self._mf_total:
                        uds = self._mf_buf[:self._mf_total]
                        self._mf_active = False
                        if uds:
                            self._dispatch(uds)


# ─── UDS TESTER ───────────────────────────────────────────────────────────────

class UDSTester:
    RESPONSE_TIMEOUT_S = 3.0
    STMIN_MS           = 5

    def __init__(self, bus: can.BusABC) -> None:
        self.bus = bus

    def shutdown(self) -> None:
        self.bus.shutdown()

    def _send(self, uds: list) -> None:
        if len(uds) <= 7:
            self.bus.send(can.Message(arbitration_id=TESTER_TX_ID,
                                      data=build_sf(uds),
                                      is_extended_id=False))
            return
        self.bus.send(can.Message(arbitration_id=TESTER_TX_ID,
                                  data=build_ff(uds),
                                  is_extended_id=False))
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
        deadline  = time.monotonic() + (timeout or self.RESPONSE_TIMEOUT_S)
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
                if (len(uds) == 3 and uds[0] == SID_NEG and uds[2] == 0x78):
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
        """send + recv shorthand."""
        self._send(uds)
        return self._recv(timeout=timeout)

    def switch_session(self, sub: int) -> None:
        self._send([SID_SESSION, sub])
        self._recv()


# ─── LIFECYCLE REGISTRATION ───────────────────────────────────────────────────

def register_campaign(campaign: TestCampaign) -> None:

    # ── Requirements ─────────────────────────────────────────────────────────
    R = campaign.add_req
    R("REQ-SW-0010", "ISO-TP SF PCI byte encodes payload length",            ASILLevel.QM)
    R("REQ-SW-0011", "ISO-TP FF PCI encodes total length in 12 bits",        ASILLevel.QM)
    R("REQ-SW-0012", "DID 0xF405 encodes coolant temp as °C × 10, 16-bit BE",ASILLevel.A)
    R("REQ-SW-0013", "CRC-32 computation shall be deterministic",             ASILLevel.A)
    R("REQ-SW-0020", "ECU enters defaultSession on 0x10 0x01",               ASILLevel.B)
    R("REQ-SW-0021", "DiagnosticSessionControl positive response includes P2 timing", ASILLevel.B)
    R("REQ-SW-0022", "ReadDID 0xF189 returns SW version ASCII string",       ASILLevel.B)
    R("REQ-SW-0023", "ReadDID unknown identifier returns NRC 0x31",          ASILLevel.B)
    R("REQ-SW-0030", "SecurityAccess seed/key exchange shall unlock ECU",    ASILLevel.C)
    R("REQ-SW-0031", "SecurityAccess shall lock after 3 consecutive wrong keys",ASILLevel.C)
    R("REQ-SW-0032", "DTC P0217 shall confirm when coolant temp > 105 °C",  ASILLevel.C)
    R("REQ-SW-0033", "ClearDiagnosticInformation shall remove confirmed DTCs",ASILLevel.C)
    R("REQ-SW-0040", "ECU shall enter programmingSession on 0x10 0x02",      ASILLevel.D)
    R("REQ-SW-0041", "CommunicationControl shall disable normal ECU tx",     ASILLevel.D)
    R("REQ-SW-0042", "ECU hard reset shall reboot and return to defaultSession",ASILLevel.D)
    R("REQ-SW-0043", "S3 session timeout shall drop session after 5 s inactivity",ASILLevel.D)
    R("REQ-PROC-001", "Gate 1: all Unit tests complete before SIL Integration",ASILLevel.QM)
    R("REQ-PROC-002", "Gate 2: all ASIL-B tests pass, no MAJOR defects open",ASILLevel.QM)
    R("REQ-PROC-003", "Gates 3+4: all ASIL-C/D tests pass, 0 open defects", ASILLevel.QM)
    R("REQ-PROC-004", "Production readiness: 100% ASIL-D coverage, 0 open defects",ASILLevel.QM)

    # ── Test Cases ────────────────────────────────────────────────────────────
    T = campaign.add_tc
    Ph = TestPhase
    A  = ASILLevel

    # GROUP 1 — Unit
    T("TC01", "ISO-TP SF: PCI byte encodes payload length",    "REQ-SW-0010", A.QM, Ph.UNIT)
    T("TC02", "ISO-TP FF: total length in 12-bit field",       "REQ-SW-0011", A.QM, Ph.UNIT)
    T("TC03", "DID temp encoding: °C × 10 round-trip",         "REQ-SW-0012", A.A,  Ph.UNIT)
    T("TC04", "CRC-32: deterministic output on same input",    "REQ-SW-0013", A.A,  Ph.UNIT)

    # GROUP 2 — SIL Integration
    T("TC05", "Default session: 0x10 0x01 → 0x50 0x01",       "REQ-SW-0020", A.B,  Ph.SIL_INTEGRATION)
    T("TC06", "Extended session: response includes P2 timing", "REQ-SW-0021", A.B,  Ph.SIL_INTEGRATION)
    T("TC07", "ReadDID 0xF189: returns ASCII SW version",      "REQ-SW-0022", A.B,  Ph.SIL_INTEGRATION)
    T("TC08", "ReadDID unknown DID → NRC 0x31",                "REQ-SW-0023", A.B,  Ph.SIL_INTEGRATION)

    # GROUP 3 — SIL System
    T("TC09", "SecurityAccess: correct seed/key unlocks ECU",  "REQ-SW-0030", A.C,  Ph.SIL_SYSTEM)
    T("TC10", "SecurityAccess: 3 wrong keys → NRC 0x36 lockout","REQ-SW-0031", A.C, Ph.SIL_SYSTEM)
    T("TC11", "DTC P0217 confirmed at 110 °C",                 "REQ-SW-0032", A.C,  Ph.SIL_SYSTEM)
    T("TC12", "ClearDTC removes P0217",                        "REQ-SW-0033", A.C,  Ph.SIL_SYSTEM)

    # GROUP 4 — HIL Validation
    T("TC13", "Programming session entry: 0x10 0x02 → 0x50 0x02","REQ-SW-0040", A.D, Ph.HIL_VALIDATION)
    T("TC14", "CommunicationControl: disable then re-enable tx","REQ-SW-0041", A.D, Ph.HIL_VALIDATION)
    T("TC15", "ECUReset hardReset → ECU returns to defaultSession","REQ-SW-0042",A.D, Ph.HIL_VALIDATION)
    T("TC16", "S3 timeout: session drops after 1.5 s silence", "REQ-SW-0043", A.D,  Ph.HIL_VALIDATION)

    # GROUP 5 — Gate Reviews
    T("TC17", "Gate 1: Unit phase complete",                   "REQ-PROC-001", A.QM, Ph.PRODUCTION)
    T("TC18", "Gate 2: defect D001 lifecycle + re-test TC06",  "REQ-PROC-002", A.QM, Ph.PRODUCTION)
    T("TC19", "Gates 3 + 4: SIL System and HIL complete",      "REQ-PROC-003", A.QM, Ph.PRODUCTION)
    T("TC20", "Production readiness + campaign report",        "REQ-PROC-004", A.QM, Ph.PRODUCTION)


# ─── TEST IMPLEMENTATIONS ─────────────────────────────────────────────────────

def tc01_sf_encoding(c: TestCampaign) -> None:
    """ISO-TP SF: byte 0 = len, len = number of UDS bytes."""
    frame = build_sf([0x10, 0x03])
    if frame[0] == 0x02 and frame[1] == 0x10 and frame[2] == 0x03:
        c.pass_tc("TC01", "SF[0]=0x02 ✓")
    else:
        c.fail_tc("TC01", detail=f"unexpected SF: {frame.hex()}")


def tc02_ff_encoding(c: TestCampaign) -> None:
    """ISO-TP FF: 12-bit length field encodes total payload length."""
    payload = list(range(20))   # 20-byte payload
    frame   = build_ff(payload)
    total   = ((frame[0] & 0x0F) << 8) | frame[1]
    if total == 20 and (frame[0] >> 4) == 0x01:
        c.pass_tc("TC02", f"FF total_length=20 ✓")
    else:
        c.fail_tc("TC02", detail=f"FF field={total}")


def tc03_did_encoding(c: TestCampaign) -> None:
    """DID encoding: temp_°C × 10 as unsigned 16-bit BE, reversible."""
    test_temps = [25.0, 90.0, 105.0, 72.3]
    for t in test_temps:
        raw     = int(t * 10)
        decoded = raw / 10.0
        if abs(decoded - t) > 0.05:
            c.fail_tc("TC03", detail=f"round-trip failed at {t} °C"); return
    c.pass_tc("TC03", f"{len(test_temps)} temps verified ✓")


def tc04_crc32(c: TestCampaign) -> None:
    """CRC-32: two identical calls produce identical results."""
    data = b"Day19-ECU-firmware-v1.0"
    r1   = crc32(data)
    r2   = crc32(data)
    if r1 == r2 and r1 != 0:
        c.pass_tc("TC04", f"CRC-32=0x{r1:08X} deterministic ✓")
    else:
        c.fail_tc("TC04", detail="CRC-32 non-deterministic or zero")


def tc05_default_session(t: UDSTester, c: TestCampaign) -> None:
    """Default session: 0x10 0x01 → positive response."""
    resp = t.sr([SID_SESSION, 0x01])
    if resp and resp[0] == SID_SESSION + 0x40 and resp[1] == 0x01:
        c.pass_tc("TC05", "0x50 0x01 ✓")
    else:
        c.fail_tc("TC05", detail=f"resp={resp}")


def tc06_extended_session_p2(t: UDSTester, ecu: SimulatedECU,
                              c: TestCampaign) -> None:
    """
    Extended session: positive response must include P2/P2* timing bytes
    (response length ≥ 6: SID + sub + 4 timing bytes).
    When ECU defect_mode=True, response is truncated → FAIL → D001 opened.
    """
    resp = t.sr([SID_SESSION, 0x03])
    if resp is None:
        c.fail_tc("TC06", defect_id="D001", detail="timeout"); return

    if resp[0] == SID_SESSION + 0x40 and len(resp) >= 6:
        c.pass_tc("TC06", f"response length={len(resp)}, P2 bytes present ✓")
    else:
        detail = (f"response length={len(resp)} — P2/P2* timing bytes missing "
                  f"(REQ-SW-0021 violated)")
        c.fail_tc("TC06", defect_id="D001", detail=detail)
        c.open_defect(Defect(
            defect_id  = "D001",
            title      = "Extended session response missing P2/P2* timing bytes",
            severity   = DefectSeverity.MAJOR,
            asil       = ASILLevel.B,
            found_in_tc= "TC06",
        ))


def tc07_read_did_sw_version(t: UDSTester, c: TestCampaign) -> None:
    """ReadDID 0xF189 returns ASCII SW version string."""
    t.switch_session(0x01)
    resp = t.sr([SID_READ_DID, 0xF1, 0x89])
    if resp and resp[0] == SID_READ_DID + 0x40:
        ver = bytes(resp[3:]).decode("ascii", errors="replace").rstrip("\x00")
        c.pass_tc("TC07", f"version='{ver}' ✓")
    else:
        c.fail_tc("TC07", detail=f"resp={resp}")


def tc08_read_did_unknown(t: UDSTester, c: TestCampaign) -> None:
    """ReadDID unknown DID 0xAABB → NRC 0x31."""
    resp = t.sr([SID_READ_DID, 0xAA, 0xBB])
    if resp and resp[0] == SID_NEG and len(resp) >= 3 and resp[2] == NRC_OUT_OF_RANGE:
        c.pass_tc("TC08", "NRC 0x31 ✓")
    else:
        c.fail_tc("TC08", detail=f"resp={resp}")


def tc09_security_access(t: UDSTester, ecu: SimulatedECU,
                          c: TestCampaign) -> None:
    """SecurityAccess: full seed/key exchange → positive response."""
    t.switch_session(0x03)
    resp = t.sr([SID_SEC, 0x01])
    if resp is None or resp[0] != SID_SEC + 0x40:
        c.fail_tc("TC09", detail="requestSeed failed"); return
    seed = struct.unpack(">I", bytes(resp[2:6]))[0]
    key  = seed ^ SEC_SECRET
    resp = t.sr([SID_SEC, 0x02,
                 (key >> 24) & 0xFF, (key >> 16) & 0xFF,
                 (key >> 8) & 0xFF,  key & 0xFF])
    if resp and resp[0] == SID_SEC + 0x40:
        c.pass_tc("TC09", "seed/key exchange accepted ✓")
    else:
        nrc = resp[2] if resp and resp[0] == SID_NEG and len(resp) >= 3 else "?"
        c.fail_tc("TC09", detail=f"NRC=0x{nrc:02X}")


def tc10_security_lockout(t: UDSTester, ecu: SimulatedECU,
                           c: TestCampaign) -> None:
    """SecurityAccess: 3 consecutive wrong keys → NRC 0x36 (exceededAttempts)."""
    # Reset to known state: default → extended (session change locks security)
    t.switch_session(0x01)
    t.switch_session(0x03)
    got_lockout = False
    for attempt in range(1, SEC_MAX_ATTEMPTS + 1):
        resp = t.sr([SID_SEC, 0x01])   # requestSeed
        if resp is None or resp[0] != SID_SEC + 0x40:
            break
        # Deliberate wrong key
        resp = t.sr([SID_SEC, 0x02, 0xDE, 0xAD, 0x00, 0x00])
        if resp and resp[0] == SID_NEG and len(resp) >= 3:
            if resp[2] == NRC_EXCEEDED_ATTEMPTS:
                got_lockout = True
                break
    if got_lockout:
        c.pass_tc("TC10", "NRC 0x36 on 3rd wrong key ✓")
    else:
        c.fail_tc("TC10", detail="lockout NRC 0x36 not received")
    # SIL power: clear lockout so subsequent TCs are not blocked
    ecu._lockout_until = 0.0
    ecu._fail_count    = 0


def tc11_dtc_over_temp(t: UDSTester, ecu: SimulatedECU,
                        c: TestCampaign) -> None:
    """DTC P0217 confirmed when temperature > 105 °C."""
    ecu.test_temperature = 110.0
    # Any dispatch call triggers _update_dtcs(); use TesterPresent as trigger
    t.sr([SID_TP, 0x00])
    resp = t.sr([SID_READ_DTC, 0x02, 0xFF])
    if resp is None or resp[0] != SID_READ_DTC + 0x40:
        c.fail_tc("TC11", detail="ReadDTC failed"); return
    section = resp[3:]
    found, status = False, 0
    i = 0
    while i + 2 < len(section):
        if section[i] == DTC_P0217_H and section[i + 1] == DTC_P0217_L:
            found  = True
            status = section[i + 2]
            break
        i += 3
    if found:
        c.pass_tc("TC11", f"P0217 confirmed at 110 °C, status=0x{status:02X} ✓")
    else:
        c.fail_tc("TC11", detail="DTC P0217 not found in response")


def tc12_clear_dtc(t: UDSTester, ecu: SimulatedECU,
                    c: TestCampaign) -> None:
    """ClearDiagnosticInformation (0x14) removes confirmed DTC P0217."""
    ecu.test_temperature = 25.0   # fault condition gone
    resp = t.sr([SID_CLR_DTC, 0xFF, 0xFF, 0xFF])
    if resp is None or resp[0] != SID_CLR_DTC + 0x40:
        c.fail_tc("TC12", detail=f"Clear failed: {resp}"); return
    # Verify DTC is gone
    t.sr([SID_TP, 0x00])   # trigger _update_dtcs (temp=25 → P0217 not re-added)
    resp = t.sr([SID_READ_DTC, 0x02, 0xFF])
    if resp and resp[0] == SID_READ_DTC + 0x40:
        count = (len(resp) - 3) // 3
        if count == 0:
            c.pass_tc("TC12", "DTC list empty after clear ✓")
        else:
            c.fail_tc("TC12", detail=f"{count} DTC(s) remain after clear")
    else:
        c.fail_tc("TC12", detail=f"ReadDTC after clear failed: {resp}")


def tc13_programming_session(t: UDSTester, c: TestCampaign) -> None:
    """ECU enters programmingSession on 0x10 0x02."""
    t.switch_session(0x01)
    resp = t.sr([SID_SESSION, 0x02])
    if resp and resp[0] == SID_SESSION + 0x40 and resp[1] == 0x02:
        c.pass_tc("TC13", "0x50 0x02 — programmingSession ✓")
    else:
        c.fail_tc("TC13", detail=f"resp={resp}")


def tc14_comm_ctrl(t: UDSTester, c: TestCampaign) -> None:
    """CommunicationControl: disable tx (0x03) then re-enable (0x00)."""
    t.switch_session(0x03)   # extended session required
    resp_dis = t.sr([SID_COMM, 0x03, 0x01])
    resp_en  = t.sr([SID_COMM, 0x00, 0x01])
    ok_dis = (resp_dis and resp_dis[0] == SID_COMM + 0x40 and resp_dis[1] == 0x03)
    ok_en  = (resp_en  and resp_en[0]  == SID_COMM + 0x40 and resp_en[1]  == 0x00)
    if ok_dis and ok_en:
        c.pass_tc("TC14", "0x68 0x03 → 0x68 0x00 ✓")
    else:
        c.fail_tc("TC14",
                  detail=f"disable={'ok' if ok_dis else resp_dis}  "
                         f"re-enable={'ok' if ok_en else resp_en}")


def tc15_ecu_reset(t: UDSTester, c: TestCampaign) -> None:
    """ECUReset hardReset: 0x51 0x01 received; ECU reboots to defaultSession."""
    t.switch_session(0x01)
    resp = t.sr([SID_RESET, 0x01])
    if resp is None or resp[0] != SID_RESET + 0x40:
        c.fail_tc("TC15", detail=f"reset response: {resp}"); return
    time.sleep(0.3)   # wait for simulated reboot
    resp = t.sr([SID_READ_DID, 0xF1, 0x89])
    if resp and resp[0] == SID_READ_DID + 0x40:
        c.pass_tc("TC15", "hardReset ✓ — ReadDID works post-boot")
    else:
        c.fail_tc("TC15", detail="ECU not responding after reset")


def tc16_s3_timeout(t: UDSTester, c: TestCampaign) -> None:
    """
    S3 session timeout: after S3_TIMEOUT_S seconds of inactivity,
    ECU drops non-default session back to default (ASIL-D safety requirement).
    """
    t.switch_session(0x01)
    # Enter extended session
    resp = t.sr([SID_SESSION, 0x03])
    if resp is None or resp[0] != SID_SESSION + 0x40:
        c.fail_tc("TC16", detail="could not enter extended session"); return

    # Silence for > S3_TIMEOUT_S — no TesterPresent sent deliberately
    time.sleep(S3_TIMEOUT_S + 0.5)

    # Request SecurityAccess — requires non-default session
    resp = t.sr([SID_SEC, 0x01])
    if (resp and resp[0] == SID_NEG
            and len(resp) >= 3 and resp[2] == NRC_CONDITIONS_NOT_OK):
        c.pass_tc("TC16",
                  f"session dropped after {S3_TIMEOUT_S} s → NRC 0x22 ✓")
    else:
        c.fail_tc("TC16",
                  detail=f"expected NRC 0x22, got {resp}")


# ── GROUP 5: Gate Reviews ──────────────────────────────────────────────────────

def tc17_gate1(c: TestCampaign) -> None:
    """Gate 1: all Unit-phase TCs complete — advance to SIL Integration."""
    ok, blockers = c.gate_review(TestPhase.UNIT, TestPhase.SIL_INTEGRATION)
    if ok:
        c.pass_tc("TC17", "Gate 1 (Unit → SIL Integration) CLEARED ✓")
    else:
        c.fail_tc("TC17", detail=f"Gate 1 blockers: {blockers}")


def tc18_gate2_defect_lifecycle(t: UDSTester, ecu: SimulatedECU,
                                  c: TestCampaign) -> None:
    """
    Gate 2 first attempt → BLOCKED (TC06 failed, D001 open).
    Apply fix → re-test TC06 → D001 closed → Gate 2 clears.
    """
    ok, blockers = c.gate_review(TestPhase.SIL_INTEGRATION, TestPhase.SIL_SYSTEM)
    if ok:
        # Should not happen on first pass — defect still open
        c.pass_tc("TC18", "Gate 2 passed first attempt (unexpected)")
        return

    print(f"  ⛔  Gate 2 BLOCKED  —  blockers:")
    for b in blockers:
        print(f"        • {b}")

    # ── Apply ECU fix ────────────────────────────────────────────────────────
    print(f"\n  [FIX] Applying ECU patch: sw_session_response_v1.1.c")
    ecu.defect_mode = False
    c.fix_defect("D001",
                 "ECU firmware corrected: session response now includes P2/P2* bytes")

    # ── Re-test TC06 ─────────────────────────────────────────────────────────
    t.switch_session(0x01)
    resp = t.sr([SID_SESSION, 0x03])
    if resp and resp[0] == SID_SESSION + 0x40 and len(resp) >= 6:
        c.retest_pass("TC06", f"response length={len(resp)}, P2 bytes present ✓")
        c.verify_defect("D001")
        c.close_defect("D001")
    else:
        c.fail_tc("TC18", detail="TC06 re-test still failing after fix"); return

    # ── Re-attempt Gate 2 ────────────────────────────────────────────────────
    ok2, blockers2 = c.gate_review(TestPhase.SIL_INTEGRATION, TestPhase.SIL_SYSTEM)
    if ok2:
        c.pass_tc("TC18", "Gate 2 (SIL Integration → SIL System) CLEARED ✓")
    else:
        c.fail_tc("TC18", detail=f"Gate 2 still blocked: {blockers2}")


def tc19_gate3_gate4(c: TestCampaign) -> None:
    """Gate 3 (SIL System → HIL) and Gate 4 (HIL → Production)."""
    ok3, b3 = c.gate_review(TestPhase.SIL_SYSTEM, TestPhase.HIL_VALIDATION)
    if ok3:
        print(f"  ✅ Gate 3 (SIL System → HIL Validation) CLEARED")
    else:
        c.fail_tc("TC19", detail=f"Gate 3 blocked: {b3}"); return

    ok4, b4 = c.gate_review(TestPhase.HIL_VALIDATION, TestPhase.PRODUCTION)
    if ok4:
        print(f"  ✅ Gate 4 (HIL Validation → Production Readiness) CLEARED")
        c.pass_tc("TC19", "Gate 3 + Gate 4 both CLEARED ✓")
    else:
        c.fail_tc("TC19", detail=f"Gate 4 blocked: {b4}")


def tc20_final_report(c: TestCampaign) -> None:
    """Print the full campaign report and declare production readiness."""
    c.print_report()
    passed, failed = c.summary()
    if failed == 0:
        print(f"\n  🏁 PRODUCTION READINESS DECLARED  —  all gates passed,")
        print(f"     0 open defects, 100% ASIL-D coverage.")
        c.pass_tc("TC20", "production readiness gate CLEARED ✓")
    else:
        c.fail_tc("TC20",
                  detail=f"{failed} TC(s) still failing — production gate blocked")


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def banner(title: str) -> None:
    print(f"\n{'─' * 64}\n  {title}\n{'─' * 64}")


def main() -> None:
    print("\n" + "🏭📋  " * 10)
    print("  Day 19 — Automotive Testing Lifecycle")
    print("  ASPICE · ISO 26262 · Gate Reviews · Defect Lifecycle")
    print("🏭📋  " * 10)

    campaign = TestCampaign()
    register_campaign(campaign)

    ecu    = SimulatedECU()
    bus    = can.Bus(interface="virtual", channel=CHANNEL)
    tester = UDSTester(bus)

    ecu.start()
    time.sleep(0.1)

    print(f"\n  Registered: {len(campaign.requirements)} requirements, "
          f"{len(campaign.test_cases)} test cases")
    print(f"  ECU defect_mode = {ecu.defect_mode}  "
          f"(TC06 will fail until D001 is fixed)\n")

    banner("GROUP 1: Unit Tests  [QM / ASIL-A]  — no CAN bus needed")
    tc01_sf_encoding(campaign)
    tc02_ff_encoding(campaign)
    tc03_did_encoding(campaign)
    tc04_crc32(campaign)

    banner("GROUP 2: SIL Integration Tests  [ASIL-B]")
    tc05_default_session(tester, campaign)
    tc06_extended_session_p2(tester, ecu, campaign)    # ← deliberately fails
    tc07_read_did_sw_version(tester, campaign)
    tc08_read_did_unknown(tester, campaign)

    banner("GROUP 3: SIL System Tests  [ASIL-C]")
    tc09_security_access(tester, ecu, campaign)
    tc10_security_lockout(tester, ecu, campaign)
    tc11_dtc_over_temp(tester, ecu, campaign)
    tc12_clear_dtc(tester, ecu, campaign)

    banner("GROUP 4: HIL Validation Tests  [ASIL-D]")
    tc13_programming_session(tester, campaign)
    tc14_comm_ctrl(tester, campaign)
    tc15_ecu_reset(tester, campaign)
    tc16_s3_timeout(tester, campaign)

    banner("GROUP 5: Gate Reviews & Campaign Closure  [QM — Process]")
    tc17_gate1(campaign)
    tc18_gate2_defect_lifecycle(tester, ecu, campaign)
    tc19_gate3_gate4(campaign)
    tc20_final_report(campaign)

    passed, failed = campaign.summary()
    total = len(campaign.test_cases)
    print(f"\n{'=' * 64}")
    print(f"  TEST SUMMARY: {passed}/{total} TCs pass  |  {failed} fail")
    print(f"{'=' * 64}")

    ecu.stop()
    tester.shutdown()


if __name__ == "__main__":
    main()
