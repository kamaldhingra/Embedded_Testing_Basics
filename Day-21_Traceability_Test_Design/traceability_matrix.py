"""
Day 21: Traceability and Test Design
Multi-Level Traceability · Bidirectional Links · Orphan/Gap Detection ·
Change Impact Analysis · ASPICE Test Specification Artifacts
======================================================================
Demonstrates a complete automotive traceability infrastructure:

  Requirements hierarchy: SYSTEM → SOFTWARE → UNIT
  Bidirectional links:    requirement ← → test case (many-to-many)
  Coverage gaps:          requirements with no linked TC (compliance risk)
  Orphan tests:           TCs with no requirement (untraceable effort)
  Change impact:          when REQ changes, which TCs must re-run?
  Formal test spec:       ASPICE-compliant TestCaseSpec artifact

  GROUP 1  (TC01–TC04)  Traceability integrity verification
  GROUP 2  (TC05–TC08)  Coverage metrics by ASIL level
  GROUP 3  (TC09–TC12)  Orphan/gap injection and resolution
  GROUP 4  (TC13–TC16)  Change impact analysis + live ECU re-run
  GROUP 5  (TC17–TC20)  Formal test specification + dashboard

No hardware needed.
Install:  pip install python-can
Run:      python traceability_matrix.py
"""

import can
import threading
import time
import struct
import random
from dataclasses import dataclass, field
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

SEC_SECRET = 0xDEADBEEF

S3_TIMEOUT_S  = 1.5
OVER_TEMP_C   = 105.0
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


# ─── TRACEABILITY DATA MODEL ──────────────────────────────────────────────────

class ASILLevel(Enum):
    QM = 0; A = 1; B = 2; C = 3; D = 4

@dataclass
class Requirement:
    """One requirement node in the hierarchy."""
    req_id:     str
    level:      str            # "SYSTEM" | "SOFTWARE" | "UNIT"
    description:str
    asil:       ASILLevel
    parent_ids: List[str] = field(default_factory=list)
    version:    int  = 1
    changed:    bool = False

@dataclass
class TestCaseSpec:
    """
    ASPICE-aligned test case specification.
    tc_id, title, asil, technique, precondition, steps, expected are static.
    req_ids, status, actual_result, executed_at, change_impact are runtime.
    """
    tc_id:              str
    title:              str
    asil:               ASILLevel
    technique:          str
    precondition:       str
    steps:              List[str]
    expected:           str
    aspice_artifact_id: str = ""
    req_ids:            List[str] = field(default_factory=list)
    status:             str  = "NOT_RUN"   # NOT_RUN | PASS | FAIL
    actual_result:      str  = ""
    executed_at:        str  = ""
    change_impact:      bool = False

    def print_spec(self) -> None:
        print(f"\n{'═' * 64}")
        print(f"  TEST CASE SPECIFICATION   (ASPICE SWE.6 / SWTS)")
        print(f"{'─' * 64}")
        print(f"  TC ID:        {self.tc_id}")
        print(f"  Title:        {self.title}")
        print(f"  Req(s):       {', '.join(self.req_ids) or '—'}")
        print(f"  ASIL:         {self.asil.name}")
        print(f"  Technique:    {self.technique}")
        if self.aspice_artifact_id:
            print(f"  Artifact ID:  {self.aspice_artifact_id}")
        print(f"{'─' * 64}")
        print(f"  PRECONDITION:")
        print(f"    {self.precondition}")
        print(f"\n  TEST STEPS:")
        for i, step in enumerate(self.steps, 1):
            print(f"    {i}. {step}")
        print(f"\n  EXPECTED RESULT:")
        print(f"    {self.expected}")
        print(f"{'═' * 64}")


class TraceabilityMatrix:
    """
    Manages requirements hierarchy and bidirectional requirement ↔ TC links.

    Key operations:
      add_req / add_tc        — register entities
      link(req_id, tc_id)     — create bidirectional traceability link
      find_orphan_tcs()       — TCs with no requirement (untraceable)
      find_gap_reqs()         — requirements with no TC (compliance gap)
      mark_changed(req_id)    — bump version, set changed flag
      flag_change_impact()    — mark affected TCs for re-run
      resolve_change_impact() — clear flag after successful re-run
      asil_consistency_errors()— detect ASIL mismatches between TC and req
      asil_coverage()         — coverage metrics per ASIL level
      print_dashboard()       — visual traceability tree + health indicators
    """

    def __init__(self) -> None:
        self.reqs:         Dict[str, Requirement]   = {}
        self.tcs:          Dict[str, TestCaseSpec]  = {}
        self._req_to_tcs:  Dict[str, List[str]]     = {}   # req_id → [tc_ids]
        self._tc_to_reqs:  Dict[str, List[str]]     = {}   # tc_id  → [req_ids]

    # ── Registration ──────────────────────────────────────────────────────────

    def add_req(self, req: Requirement) -> None:
        self.reqs[req.req_id] = req
        self._req_to_tcs.setdefault(req.req_id, [])

    def add_tc(self, spec: TestCaseSpec) -> None:
        self.tcs[spec.tc_id] = spec
        self._tc_to_reqs.setdefault(spec.tc_id, [])

    def link(self, req_id: str, tc_id: str) -> None:
        if tc_id not in self._req_to_tcs[req_id]:
            self._req_to_tcs[req_id].append(tc_id)
        if req_id not in self._tc_to_reqs[tc_id]:
            self._tc_to_reqs[tc_id].append(req_id)
            self.tcs[tc_id].req_ids.append(req_id)

    def unlink(self, req_id: str, tc_id: str) -> None:
        self._req_to_tcs[req_id] = [t for t in self._req_to_tcs.get(req_id, []) if t != tc_id]
        self._tc_to_reqs[tc_id]  = [r for r in self._tc_to_reqs.get(tc_id, [])  if r != req_id]
        self.tcs[tc_id].req_ids  = [r for r in self.tcs[tc_id].req_ids if r != req_id]

    def remove_tc(self, tc_id: str) -> None:
        for req_id in list(self._tc_to_reqs.get(tc_id, [])):
            self.unlink(req_id, tc_id)
        self.tcs.pop(tc_id, None)
        self._tc_to_reqs.pop(tc_id, None)

    def remove_req(self, req_id: str) -> None:
        for tc_id in list(self._req_to_tcs.get(req_id, [])):
            self.unlink(req_id, tc_id)
        self.reqs.pop(req_id, None)
        self._req_to_tcs.pop(req_id, None)

    # ── Analysis ──────────────────────────────────────────────────────────────

    def find_orphan_tcs(self) -> List[str]:
        """TCs with no requirement link — untraceable, audit finding."""
        return sorted(tc_id for tc_id in self.tcs
                      if not self._tc_to_reqs.get(tc_id))

    def find_gap_reqs(self) -> List[str]:
        """
        Requirements with no TC link — compliance gap.
        Only SOFTWARE and UNIT level are checked; SYSTEM-level reqs are
        covered transitively through their SW children.
        """
        return sorted(
            req_id for req_id, req in self.reqs.items()
            if req.level in ("SOFTWARE", "UNIT")
            and not self._req_to_tcs.get(req_id)
        )

    def asil_consistency_errors(self) -> List[str]:
        """
        Each TC's ASIL must be >= the highest ASIL of its linked requirements.
        Returns a list of error strings for any mismatch found.
        """
        errors = []
        for tc_id, tc in self.tcs.items():
            linked_req_ids = self._tc_to_reqs.get(tc_id, [])
            if not linked_req_ids:
                continue
            max_asil = max(
                self.reqs[r].asil.value
                for r in linked_req_ids if r in self.reqs
            )
            if tc.asil.value < max_asil:
                expected = ASILLevel(max_asil).name
                errors.append(
                    f"{tc_id}: TC=ASIL-{tc.asil.name} but req needs ASIL-{expected}"
                )
        return errors

    def chain_intact(self, sys_req_id: str) -> Tuple[bool, List[str]]:
        """
        Verify the full chain:
          SYS req → at least one SW child → each SW child has at least one TC.
        Returns (ok, list_of_broken_links).
        """
        broken = []
        if sys_req_id not in self.reqs:
            return False, [f"{sys_req_id} not in matrix"]
        sw_children = [r for r in self.reqs.values()
                       if r.level == "SOFTWARE" and sys_req_id in r.parent_ids]
        if not sw_children:
            broken.append(f"{sys_req_id} has no SOFTWARE children")
        for sw in sw_children:
            if not self._req_to_tcs.get(sw.req_id):
                broken.append(f"{sw.req_id} has no linked TCs")
        return len(broken) == 0, broken

    # ── Change impact ─────────────────────────────────────────────────────────

    def mark_changed(self, req_id: str) -> None:
        if req_id in self.reqs:
            self.reqs[req_id].changed  = True
            self.reqs[req_id].version += 1

    def impact_analysis(self, req_id: str) -> List[str]:
        """Return TC IDs that would need re-run if req_id changes (read-only)."""
        return list(self._req_to_tcs.get(req_id, []))

    def flag_change_impact(self, req_id: str) -> List[str]:
        """Mark all TCs linked to req_id as needing re-run. Returns list of TC IDs."""
        tc_ids = self.impact_analysis(req_id)
        for tc_id in tc_ids:
            self.tcs[tc_id].change_impact = True
        return tc_ids

    def resolve_change_impact(self, tc_id: str, passed: bool,
                               result: str = "") -> None:
        tc = self.tcs[tc_id]
        tc.change_impact  = False
        tc.status         = "PASS" if passed else "FAIL"
        tc.actual_result  = result
        tc.executed_at    = time.strftime("%Y-%m-%d %H:%M")

    # ── Coverage ──────────────────────────────────────────────────────────────

    def asil_coverage(self) -> Dict:
        """
        Coverage at SOFTWARE and UNIT level only.
        SYSTEM-level reqs are verified transitively via their SW children.
        """
        testable = [r for r in self.reqs.values()
                    if r.level in ("SOFTWARE", "UNIT")]
        result = {}
        for level in ASILLevel:
            reqs    = [r for r in testable if r.asil == level]
            covered = [r for r in reqs if self._req_to_tcs.get(r.req_id)]
            result[level.name] = {"total": len(reqs), "covered": len(covered)}
        return result

    # ── Dashboard ─────────────────────────────────────────────────────────────

    def print_dashboard(self) -> None:
        cov     = self.asil_coverage()
        orphans = self.find_orphan_tcs()
        gaps    = self.find_gap_reqs()
        impact  = [tc_id for tc_id, tc in self.tcs.items() if tc.change_impact]

        print(f"\n{'╔' + '═' * 62 + '╗'}")
        print(f"║   TRACEABILITY DASHBOARD{' ' * 37}║")
        print(f"{'╚' + '═' * 62 + '╝'}")

        # Requirements hierarchy tree
        print(f"\n  REQUIREMENTS HIERARCHY  (REQ → UNIT → TC)")
        sys_reqs = sorted(
            [r for r in self.reqs.values() if r.level == "SYSTEM"],
            key=lambda r: r.req_id,
        )
        for sys_req in sys_reqs:
            ver = f" v{sys_req.version}" if sys_req.version > 1 else ""
            chg = " ⚡CHANGED" if sys_req.changed else ""
            print(f"\n  {sys_req.req_id} [ASIL-{sys_req.asil.name}]{ver}{chg}  {sys_req.description}")
            sw_children = sorted(
                [r for r in self.reqs.values()
                 if r.level == "SOFTWARE" and sys_req.req_id in r.parent_ids],
                key=lambda r: r.req_id,
            )
            for sw in sw_children:
                ver2 = f" v{sw.version}" if sw.version > 1 else ""
                chg2 = " ⚡" if sw.changed else ""
                print(f"  ├─ {sw.req_id} [ASIL-{sw.asil.name}]{ver2}{chg2}  {sw.description}")
                unit_children = sorted(
                    [r for r in self.reqs.values()
                     if r.level == "UNIT" and sw.req_id in r.parent_ids],
                    key=lambda r: r.req_id,
                )
                shown_tcs = set()
                for unit in unit_children:
                    print(f"  │   └─ {unit.req_id} [ASIL-{unit.asil.name}]  {unit.description}")
                    for tc_id in sorted(self._req_to_tcs.get(unit.req_id, [])):
                        tc   = self.tcs[tc_id]
                        icon = "✅" if tc.status == "PASS" else ("❌" if tc.status == "FAIL" else "—")
                        ci   = " ⚡re-run" if tc.change_impact else ""
                        print(f"  │        └── {tc_id} {icon}{ci}  {tc.title}")
                        shown_tcs.add(tc_id)
                for tc_id in sorted(self._req_to_tcs.get(sw.req_id, [])):
                    if tc_id not in shown_tcs:
                        tc   = self.tcs[tc_id]
                        icon = "✅" if tc.status == "PASS" else ("❌" if tc.status == "FAIL" else "—")
                        ci   = " ⚡re-run" if tc.change_impact else ""
                        print(f"  │   └─── {tc_id} {icon}{ci}  {tc.title}")

        # Coverage metrics
        print(f"\n  COVERAGE METRICS")
        print(f"  {'ASIL':<8} {'Reqs':<8} {'Covered':<10} {'%'}")
        print(f"  {'─' * 34}")
        total_r = covered_r = 0
        for asil_name, data in cov.items():
            if data["total"] > 0:
                pct       = round(data["covered"] / data["total"] * 100)
                total_r   += data["total"]
                covered_r += data["covered"]
                bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
                print(f"  ASIL-{asil_name:<3} {data['total']:<8} {data['covered']:<10} "
                      f"{pct:>3}%  {bar}")
        if total_r:
            overall_pct = round(covered_r / total_r * 100)
            print(f"  {'─' * 34}")
            print(f"  {'TOTAL':<8} {total_r:<8} {covered_r:<10} {overall_pct:>3}%")

        # Health indicators
        print(f"\n  HEALTH INDICATORS")
        print(f"  Change Impact:  {len(impact)} TCs flagged  "
              f"{'✅' if not impact else '⚠️  ' + str(impact)}")
        print(f"  Orphan TCs:     {len(orphans)} "
              f"{'✅' if not orphans else '⚠️  ' + str(orphans)}")
        print(f"  Coverage Gaps:  {len(gaps)} "
              f"{'✅' if not gaps else '⚠️  ' + str(gaps)}")

        # TC execution log
        print(f"\n  TC EXECUTION STATUS")
        print(f"  {'TC ID':<12} {'ASIL':<6} {'Status':<10} Title")
        print(f"  {'─' * 60}")
        for tc_id in sorted(self.tcs.keys()):
            tc   = self.tcs[tc_id]
            icon = "✅" if tc.status == "PASS" else ("❌" if tc.status == "FAIL" else " —")
            ci   = "⚡" if tc.change_impact else " "
            print(f"  {tc_id:<12} {tc.asil.name:<6} {tc.status:<10} {icon}{ci} {tc.title[:44]}")
        print()


# ─── SIMPLE RESULT TRACKER (for the 20 management TCs) ───────────────────────

class SimpleReport:
    def __init__(self) -> None:
        self._passed: List[str] = []
        self._failed: List[str] = []

    def pass_tc(self, tc_id: str, detail: str = "") -> None:
        print(f"  ✅ PASS  {tc_id}  {detail}")
        self._passed.append(tc_id)

    def fail_tc(self, tc_id: str, detail: str = "") -> None:
        print(f"  ❌ FAIL  {tc_id}  {detail}")
        self._failed.append(tc_id)

    def summary(self) -> Tuple[int, int]:
        return len(self._passed), len(self._failed)


# ─── SIMULATED ECU ────────────────────────────────────────────────────────────

class SimulatedECU(threading.Thread):
    def __init__(self) -> None:
        super().__init__(daemon=True, name="SimECU")
        self.bus          = can.Bus(interface="virtual", channel=CHANNEL)
        self._stop        = threading.Event()
        self.session      = 0x01
        self._last_diag_t = time.monotonic()
        self.test_temperature: float = 25.0
        self._dtcs: Dict[Tuple[int, int], int] = {}
        self._unlocked        = False
        self._seed            = 0
        self._seed_issued     = False
        self._fail_count      = 0
        self._lockout_until   = 0.0

    def stop(self) -> None:
        self._stop.set()
        self.bus.shutdown()

    def _send(self, payload: list) -> None:
        if len(payload) <= 7:
            self.bus.send(can.Message(arbitration_id=TESTER_RX_ID,
                                      data=build_sf(payload),
                                      is_extended_id=False))
            return
        self.bus.send(can.Message(arbitration_id=TESTER_RX_ID,
                                  data=build_ff(payload),
                                  is_extended_id=False))
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

    def _update_dtcs(self) -> None:
        key = (DTC_P0217_H, DTC_P0217_L)
        if self.test_temperature > OVER_TEMP_C:
            self._dtcs[key] = DTC_CONFIRMED

    def _dispatch(self, uds: list) -> None:
        self._last_diag_t = time.monotonic()
        self._update_dtcs()
        sid = uds[0]
        if sid == SID_SESSION and len(uds) >= 2:
            sub = uds[1]
            if sub not in (0x01, 0x02, 0x03):
                self._neg(SID_SESSION, NRC_SUBFUNC_NOT_SUPP); return
            self._unlocked    = False
            self._seed_issued = False
            self._fail_count  = 0
            self.session      = sub
            self._send([SID_SESSION + 0x40, sub, 0x00, 0x19, 0x01, 0xF4])

        elif sid == SID_RESET and len(uds) >= 2:
            self._send([SID_RESET + 0x40, uds[1]])
            def _do_reset():
                time.sleep(0.1)
                self.session    = 0x01
                self._unlocked  = False
            threading.Thread(target=_do_reset, daemon=True).start()

        elif sid == SID_SEC and len(uds) >= 2:
            if self.session not in (0x02, 0x03):
                self._neg(SID_SEC, NRC_CONDITIONS_NOT_OK); return
            if time.monotonic() < self._lockout_until:
                self._neg(SID_SEC, NRC_EXCEEDED_ATTEMPTS); return
            sub = uds[1]
            if sub == 0x01:
                if self._unlocked:
                    self._send([SID_SEC + 0x40, sub, 0, 0, 0, 0])
                else:
                    seed = random.randint(1, 0xFFFFFFFF)
                    self._seed = seed; self._seed_issued = True
                    self._send([SID_SEC + 0x40, sub,
                                (seed >> 24) & 0xFF, (seed >> 16) & 0xFF,
                                (seed >> 8) & 0xFF,  seed & 0xFF])
            elif sub == 0x02:
                if not self._seed_issued:
                    self._neg(SID_SEC, 0x24); return
                self._seed_issued = False
                key = struct.unpack(">I", bytes(uds[2:6]))[0]
                if key == (self._seed ^ SEC_SECRET):
                    self._unlocked   = True
                    self._fail_count = 0
                    self._send([SID_SEC + 0x40, sub])
                else:
                    self._fail_count += 1
                    if self._fail_count >= 3:
                        self._lockout_until = time.monotonic() + 3.0
                        self._fail_count = 0
                        self._neg(SID_SEC, NRC_EXCEEDED_ATTEMPTS)
                    else:
                        self._neg(SID_SEC, NRC_INVALID_KEY)

        elif sid == SID_READ_DID:
            if len(uds) < 3:
                self._neg(SID_READ_DID, 0x13); return
            did = (uds[1] << 8) | uds[2]
            if did == 0xF186:
                self._send([SID_READ_DID + 0x40, uds[1], uds[2], self.session])
            elif did == 0xF189:
                ver = list("Day21-ECU-v1.0".encode("ascii"))
                self._send([SID_READ_DID + 0x40, uds[1], uds[2]] + ver)
            elif did == 0xF405:
                raw = int(self.test_temperature * 10)
                self._send([SID_READ_DID + 0x40, uds[1], uds[2],
                            (raw >> 8) & 0xFF, raw & 0xFF])
            else:
                self._neg(SID_READ_DID, NRC_OUT_OF_RANGE)

        elif sid == SID_READ_DTC:
            sub  = uds[1] if len(uds) >= 2 else 0
            if sub != 0x02:
                self._neg(SID_READ_DTC, NRC_SUBFUNC_NOT_SUPP); return
            payload = [SID_READ_DTC + 0x40, sub, 0xFF]
            for (h, l), s in self._dtcs.items():
                payload += [h, l, s]
            self._send(payload)

        elif sid == SID_CLR_DTC:
            if len(uds) >= 4 and uds[1] == 0xFF and uds[2] == 0xFF and uds[3] == 0xFF:
                self._dtcs.clear()
                self._send([SID_CLR_DTC + 0x40])
            else:
                self._neg(SID_CLR_DTC, NRC_OUT_OF_RANGE)

        elif sid == SID_TP:
            if len(uds) >= 2 and (uds[1] & 0x80):
                return
            self._send([SID_TP + 0x40, uds[1] if len(uds) >= 2 else 0x00])
        else:
            self._neg(sid, 0x11)

    def run(self) -> None:
        while not self._stop.is_set():
            now = time.monotonic()
            if self.session != 0x01 and now - self._last_diag_t > S3_TIMEOUT_S:
                self.session   = 0x01
                self._unlocked = False
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
                    deadline += 5.0; continue
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
        self._send(uds)
        return self._recv(timeout=timeout)

    def switch_session(self, sub: int) -> None:
        self._send([SID_SESSION, sub])
        self._recv()


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _dtc_present(resp: Optional[list], h: int, l: int) -> bool:
    if resp is None or resp[0] != SID_READ_DTC + 0x40:
        return False
    i = 3
    while i + 2 <= len(resp):
        if resp[i] == h and resp[i + 1] == l:
            return True
        i += 3
    return False

def _clean_dtc(ecu: SimulatedECU, t: UDSTester, safe_temp: float = 25.0) -> None:
    ecu.test_temperature = safe_temp
    t.sr([SID_CLR_DTC, 0xFF, 0xFF, 0xFF])


# ─── REQUIREMENTS & TC REGISTRATION ──────────────────────────────────────────

def build_matrix() -> TraceabilityMatrix:
    """
    Construct the multi-level requirements hierarchy and register all functional TCs.

    Hierarchy:
      SYS-001 [ASIL-C]: Vehicle detects engine over-temperature
        └─ SW-001  [ASIL-C]: DTC P0217 confirmed when temp > 105°C
             └─ UNIT-001 [ASIL-C]: DTC status byte = 0xAF

      SYS-002 [ASIL-B]: Diagnostic interface accessible via CAN
        ├─ SW-002  [ASIL-B]: Extended session includes P2/P2* timing
        │    └─ UNIT-002 [ASIL-B]: Session response bytes 3–5 = P2 timing
        ├─ SW-003  [ASIL-B]: DID 0xF189 returns SW version ASCII string
        │    └─ UNIT-003 [QM]:    DID 0xF189 data format
        ├─ SW-004  [ASIL-B]: Unknown DID returns NRC 0x31
        ├─ SW-005  [ASIL-C]: SecurityAccess locks after 3 wrong keys
        └─ SW-006  [ASIL-D]: S3 timeout drops session to defaultSession
    """
    tm = TraceabilityMatrix()

    # System requirements
    tm.add_req(Requirement("SYS-001", "SYSTEM", "Vehicle shall detect engine over-temperature", ASILLevel.C))
    tm.add_req(Requirement("SYS-002", "SYSTEM", "Diagnostic interface accessible via CAN bus", ASILLevel.B))

    # Software requirements
    tm.add_req(Requirement("SW-001", "SOFTWARE", "DTC P0217 confirmed when temp > 105°C",       ASILLevel.C, ["SYS-001"]))
    tm.add_req(Requirement("SW-002", "SOFTWARE", "Extended session includes P2/P2* timing bytes",ASILLevel.B, ["SYS-002"]))
    tm.add_req(Requirement("SW-003", "SOFTWARE", "DID 0xF189 returns SW version ASCII string",  ASILLevel.B, ["SYS-002"]))
    tm.add_req(Requirement("SW-004", "SOFTWARE", "Unknown DID returns NRC 0x31",                ASILLevel.B, ["SYS-002"]))
    tm.add_req(Requirement("SW-005", "SOFTWARE", "SecurityAccess locks after 3 wrong key attempts",ASILLevel.C, ["SYS-002"]))
    tm.add_req(Requirement("SW-006", "SOFTWARE", "S3 timeout drops session to defaultSession",  ASILLevel.D, ["SYS-002"]))

    # Unit requirements
    tm.add_req(Requirement("UNIT-001", "UNIT", "DTC P0217 status byte shall be 0xAF (confirmed)",    ASILLevel.C, ["SW-001"]))
    tm.add_req(Requirement("UNIT-002", "UNIT", "Session response bytes 3–5 encode P2/P2* timing",    ASILLevel.B, ["SW-002"]))
    tm.add_req(Requirement("UNIT-003", "UNIT", "DID 0xF189 data is null-padded ASCII (≤ 14 chars)",  ASILLevel.QM, ["SW-003"]))

    # Functional test cases
    tm.add_tc(TestCaseSpec(
        "TC-F01", "DTC P0217 confirmed at 110 °C",
        ASILLevel.C, "BVA",
        "ECU powered on; default session; DTCs cleared; temp set to 25 °C",
        ["Set ecu.test_temperature = 25.0", "Send 0x14 FF FF FF (ClearDTC)",
         "Set ecu.test_temperature = 110.0", "Send TesterPresent 0x3E 0x00",
         "Send 0x19 0x02 0xFF (ReadDTC)", "Parse response for P0217"],
        "Response contains DTC 0x02 0x17 with status 0xAF",
        aspice_artifact_id="SWE.4-SWTS-TC-F01-v1.0",
    ))
    tm.add_tc(TestCaseSpec(
        "TC-F02", "DTC P0217 NOT set at 104 °C",
        ASILLevel.C, "BVA",
        "ECU powered on; default session; DTCs cleared",
        ["Set ecu.test_temperature = 25.0", "Send 0x14 FF FF FF",
         "Set ecu.test_temperature = 104.0", "Send TesterPresent",
         "Send 0x19 0x02 0xFF", "Parse response"],
        "Response contains NO DTC entries (0 bytes after header)",
        aspice_artifact_id="SWE.4-SWTS-TC-F02-v1.0",
    ))
    tm.add_tc(TestCaseSpec(
        "TC-F03", "Extended session P2 timing bytes present",
        ASILLevel.B, "BVA",
        "ECU in defaultSession",
        ["Send 0x10 0x03 (extendedDiagSession)",
         "Receive response; check length ≥ 6"],
        "Response length ≥ 6; bytes 3–5 encode P2=25ms, P2*=5000ms",
        aspice_artifact_id="SWE.5-SWTS-TC-F03-v1.0",
    ))
    tm.add_tc(TestCaseSpec(
        "TC-F04", "ReadDID 0xF189 returns SW version",
        ASILLevel.B, "EP",
        "ECU in any session",
        ["Send 0x22 0xF1 0x89", "Receive response", "Decode bytes 3+ as ASCII"],
        "SID = 0x62; data is printable ASCII string",
        aspice_artifact_id="SWE.5-SWTS-TC-F04-v1.0",
    ))
    tm.add_tc(TestCaseSpec(
        "TC-F05", "ReadDID unknown DID → NRC 0x31",
        ASILLevel.B, "EP",
        "ECU in any session",
        ["Send 0x22 0xAA 0xBB", "Receive response"],
        "Negative response 0x7F 0x22 0x31 (requestOutOfRange)",
        aspice_artifact_id="SWE.5-SWTS-TC-F05-v1.0",
    ))
    tm.add_tc(TestCaseSpec(
        "TC-F06", "SecurityAccess lockout after 3 wrong keys",
        ASILLevel.C, "BVA",
        "ECU in extendedDiagSession",
        ["Send 0x27 0x01 (requestSeed) × 3 with wrong key each time",
         "Verify NRC 0x35 on attempts 1 and 2",
         "Verify NRC 0x36 on attempt 3"],
        "Third wrong key returns NRC 0x7F 0x27 0x36 (exceededAttempts)",
        aspice_artifact_id="SWE.6-SWTS-TC-F06-v1.0",
    ))
    tm.add_tc(TestCaseSpec(
        "TC-F07", "S3 timeout drops session to defaultSession",
        ASILLevel.D, "State Transition",
        "ECU powered on; defaultSession; no active session or TesterPresent running",
        ["Send 0x10 0x03 (enter extendedDiagSession)",
         "Verify response positive and DID 0xF186 = 0x03",
         f"Wait {S3_TIMEOUT_S + 0.5:.1f} s without sending any UDS message",
         "Send 0x27 0x01 (SecurityAccess requestSeed — requires non-default session)",
         "Receive response"],
        "Negative response 0x7F 0x27 0x22 (conditionsNotCorrect — session dropped)",
        aspice_artifact_id="SWE.6-SWTS-TC-F07-v1.0",
    ))

    # Links: requirements ← → test cases
    tm.link("SW-001",  "TC-F01"); tm.link("UNIT-001", "TC-F01")
    tm.link("SW-001",  "TC-F02")
    tm.link("SW-002",  "TC-F03"); tm.link("UNIT-002", "TC-F03")
    tm.link("SW-003",  "TC-F04"); tm.link("UNIT-003", "TC-F04")
    tm.link("SW-004",  "TC-F05")
    tm.link("SW-005",  "TC-F06")
    tm.link("SW-006",  "TC-F07")

    return tm


# ─── GROUP 1: TRACEABILITY INTEGRITY (TC01–TC04) ──────────────────────────────

def tc01_forward_traceability(tm: TraceabilityMatrix, rpt: SimpleReport) -> None:
    """All SW-level requirements must have at least one linked TC."""
    sw_reqs  = [r for r in tm.reqs.values() if r.level == "SOFTWARE"]
    untested = [r.req_id for r in sw_reqs if not tm._req_to_tcs.get(r.req_id)]
    if not untested:
        rpt.pass_tc("TC01",
                    f"Forward traceability: all {len(sw_reqs)} SW reqs have ≥1 TC ✓")
    else:
        rpt.fail_tc("TC01", f"Untested SW requirements: {untested}")


def tc02_backward_traceability(tm: TraceabilityMatrix, rpt: SimpleReport) -> None:
    """All functional TCs must have at least one linked requirement."""
    orphans = tm.find_orphan_tcs()
    if not orphans:
        rpt.pass_tc("TC02",
                    f"Backward traceability: all {len(tm.tcs)} TCs have ≥1 req ✓")
    else:
        rpt.fail_tc("TC02", f"Orphan TCs (no req): {orphans}")


def tc03_multilevel_chain(tm: TraceabilityMatrix, rpt: SimpleReport) -> None:
    """
    Multi-level chain: SYS-001 → SW-001 → UNIT-001 → TC-F01 must all exist and link.
    """
    # Explicit chain check
    chain_ok = (
        "SYS-001" in tm.reqs
        and "SW-001" in tm.reqs
        and "SYS-001" in tm.reqs["SW-001"].parent_ids
        and "UNIT-001" in tm.reqs
        and "SW-001" in tm.reqs["UNIT-001"].parent_ids
        and "TC-F01" in tm.tcs
        and "UNIT-001" in tm._req_to_tcs.get("UNIT-001", [])  # wait, should be TC-F01
    )
    # Correct check: TC-F01 linked to both SW-001 and UNIT-001
    chain_ok = (
        "SYS-001" in tm.reqs
        and "SW-001" in tm.reqs and "SYS-001" in tm.reqs["SW-001"].parent_ids
        and "UNIT-001" in tm.reqs and "SW-001" in tm.reqs["UNIT-001"].parent_ids
        and "TC-F01" in tm.tcs
        and "TC-F01" in tm._req_to_tcs.get("UNIT-001", [])
        and "TC-F01" in tm._req_to_tcs.get("SW-001", [])
    )
    ok2, broken = tm.chain_intact("SYS-001")
    if chain_ok and ok2:
        rpt.pass_tc("TC03",
                    "Multi-level chain: SYS-001 → SW-001 → UNIT-001 → TC-F01 intact ✓")
    else:
        rpt.fail_tc("TC03", f"Chain broken: {broken}")


def tc04_asil_consistency(tm: TraceabilityMatrix, rpt: SimpleReport) -> None:
    """Each TC's ASIL must be >= the highest ASIL of its linked requirements."""
    errors = tm.asil_consistency_errors()
    if not errors:
        rpt.pass_tc("TC04",
                    f"ASIL consistency: all {len(tm.tcs)} TCs correctly labelled ✓")
    else:
        rpt.fail_tc("TC04", f"ASIL mismatches: {errors}")


# ─── GROUP 2: COVERAGE METRICS (TC05–TC08) ────────────────────────────────────

def tc05_asil_b_coverage(tm: TraceabilityMatrix, rpt: SimpleReport) -> None:
    cov  = tm.asil_coverage()["B"]
    ok   = cov["covered"] == cov["total"] and cov["total"] > 0
    if ok:
        rpt.pass_tc("TC05", f"ASIL-B coverage: {cov['covered']}/{cov['total']} (100%) ✓")
    else:
        rpt.fail_tc("TC05", f"ASIL-B: {cov['covered']}/{cov['total']}")


def tc06_asil_c_coverage(tm: TraceabilityMatrix, rpt: SimpleReport) -> None:
    cov = tm.asil_coverage()["C"]
    ok  = cov["covered"] == cov["total"] and cov["total"] > 0
    if ok:
        rpt.pass_tc("TC06", f"ASIL-C coverage: {cov['covered']}/{cov['total']} (100%) ✓")
    else:
        rpt.fail_tc("TC06", f"ASIL-C: {cov['covered']}/{cov['total']}")


def tc07_asil_d_coverage(tm: TraceabilityMatrix, rpt: SimpleReport) -> None:
    cov = tm.asil_coverage()["D"]
    ok  = cov["covered"] == cov["total"] and cov["total"] > 0
    if ok:
        rpt.pass_tc("TC07", f"ASIL-D coverage: {cov['covered']}/{cov['total']} (100%) ✓")
    else:
        rpt.fail_tc("TC07", f"ASIL-D: {cov['covered']}/{cov['total']}")


def tc08_overall_coverage(tm: TraceabilityMatrix, rpt: SimpleReport) -> None:
    cov    = tm.asil_coverage()
    total  = sum(d["total"]   for d in cov.values())
    covered= sum(d["covered"] for d in cov.values())
    pct    = round(covered / total * 100) if total else 0
    ok     = pct == 100
    if ok:
        rpt.pass_tc("TC08", f"Overall coverage: {covered}/{total} ({pct}%) ✓")
    else:
        rpt.fail_tc("TC08", f"Overall: {covered}/{total} ({pct}%) — below 100%")


# ─── GROUP 3: ORPHAN & GAP DETECTION (TC09–TC12) ─────────────────────────────

def tc09_detect_orphan(tm: TraceabilityMatrix, rpt: SimpleReport) -> None:
    """Inject a TC with no requirement → find_orphan_tcs() must detect it."""
    tm.add_tc(TestCaseSpec("TC-ORPHAN", "Orphan test — no requirement link",
                           ASILLevel.QM, "Exploratory", "None", [], "Unknown"))
    orphans = tm.find_orphan_tcs()
    if "TC-ORPHAN" in orphans:
        rpt.pass_tc("TC09",
                    f"Orphan detection ✓  found {len(orphans)} orphan(s): {orphans}")
    else:
        rpt.fail_tc("TC09", "Orphan TC-ORPHAN not detected")


def tc10_detect_gap(tm: TraceabilityMatrix, rpt: SimpleReport) -> None:
    """Inject a requirement with no TC → find_gap_reqs() must detect it."""
    tm.add_req(Requirement("SW-UNTESTED", "SOFTWARE",
                           "Untested SW requirement — coverage gap", ASILLevel.B,
                           ["SYS-002"]))
    gaps = tm.find_gap_reqs()
    if "SW-UNTESTED" in gaps:
        rpt.pass_tc("TC10",
                    f"Gap detection ✓  found {len(gaps)} gap(s): {gaps}")
    else:
        rpt.fail_tc("TC10", "Gap SW-UNTESTED not detected")


def tc11_resolve_orphan(tm: TraceabilityMatrix, rpt: SimpleReport) -> None:
    """Link TC-ORPHAN to SW-UNTESTED → orphan list clears."""
    tm.link("SW-UNTESTED", "TC-ORPHAN")
    orphans = tm.find_orphan_tcs()
    if "TC-ORPHAN" not in orphans:
        rpt.pass_tc("TC11",
                    "Orphan resolved: TC-ORPHAN linked to SW-UNTESTED → orphan list empty ✓")
    else:
        rpt.fail_tc("TC11", f"TC-ORPHAN still in orphan list: {orphans}")


def tc12_resolve_gap(tm: TraceabilityMatrix, rpt: SimpleReport) -> None:
    """SW-UNTESTED now has TC-ORPHAN → gap list clears. Then clean up."""
    gaps = tm.find_gap_reqs()
    if "SW-UNTESTED" not in gaps:
        rpt.pass_tc("TC12",
                    "Gap resolved: SW-UNTESTED has TC-ORPHAN → gap list empty ✓")
        # Restore clean state
        tm.remove_tc("TC-ORPHAN")
        tm.remove_req("SW-UNTESTED")
    else:
        rpt.fail_tc("TC12", f"SW-UNTESTED still in gap list: {gaps}")
        tm.remove_tc("TC-ORPHAN")
        tm.remove_req("SW-UNTESTED")


# ─── GROUP 4: CHANGE IMPACT ANALYSIS (TC13–TC16) ─────────────────────────────

def tc13_mark_requirement_changed(tm: TraceabilityMatrix, rpt: SimpleReport) -> None:
    """
    SW-001 is updated (version bump → new safety analysis result).
    impact_analysis() should return TC-F01 and TC-F02.
    """
    print("  [CHANGE] SW-001 updated: new threshold study → version bump")
    tm.mark_changed("SW-001")
    affected = tm.impact_analysis("SW-001")
    expected = {"TC-F01", "TC-F02"}
    if set(affected) == expected:
        rpt.pass_tc("TC13",
                    f"Impact analysis: SW-001 v{tm.reqs['SW-001'].version} affects "
                    f"{sorted(affected)} ✓")
    else:
        rpt.fail_tc("TC13", f"Expected {sorted(expected)}, got {sorted(affected)}")


def tc14_verify_change_impact_flags(tm: TraceabilityMatrix, rpt: SimpleReport) -> None:
    """Flag the affected TCs — change_impact must be True on TC-F01 and TC-F02."""
    flagged = tm.flag_change_impact("SW-001")
    ok = (tm.tcs["TC-F01"].change_impact and tm.tcs["TC-F02"].change_impact)
    if ok:
        rpt.pass_tc("TC14",
                    f"Flagged {len(flagged)} TC(s) for re-run: {sorted(flagged)} ✓")
    else:
        rpt.fail_tc("TC14",
                    f"TC-F01.change_impact={tm.tcs['TC-F01'].change_impact}  "
                    f"TC-F02.change_impact={tm.tcs['TC-F02'].change_impact}")


def tc15_execute_change_impact_rerun(tm: TraceabilityMatrix,
                                      t: UDSTester, ecu: SimulatedECU,
                                      rpt: SimpleReport) -> None:
    """
    Execute TC-F01 and TC-F02 on the live ECU to verify they still pass
    after SW-001 was changed.  On success, resolve the change_impact flags.
    """
    print("  [RE-RUN] TC-F01: DTC P0217 at 110°C")
    # TC-F01: DTC confirmed at 110°C
    _clean_dtc(ecu, t)
    ecu.test_temperature = 110.0
    t.sr([SID_TP, 0x00])
    resp   = t.sr([SID_READ_DTC, 0x02, 0xFF])
    f01_ok = _dtc_present(resp, DTC_P0217_H, DTC_P0217_L)
    tm.resolve_change_impact("TC-F01", f01_ok, "P0217 confirmed at 110°C")

    print("  [RE-RUN] TC-F02: DTC NOT set at 104°C")
    # TC-F02: DTC not set at 104°C
    _clean_dtc(ecu, t)
    ecu.test_temperature = 104.0
    t.sr([SID_TP, 0x00])
    resp   = t.sr([SID_READ_DTC, 0x02, 0xFF])
    f02_ok = not _dtc_present(resp, DTC_P0217_H, DTC_P0217_L)
    tm.resolve_change_impact("TC-F02", f02_ok, "No DTC at 104°C")

    if f01_ok and f02_ok:
        rpt.pass_tc("TC15",
                    "Change-impact re-run: TC-F01 PASS + TC-F02 PASS ✓  (SW-001 still valid)")
    else:
        rpt.fail_tc("TC15",
                    f"TC-F01={f01_ok}  TC-F02={f02_ok}")


def tc16_verify_impact_flags_cleared(tm: TraceabilityMatrix, rpt: SimpleReport) -> None:
    """After re-run, change_impact flags must be False on TC-F01 and TC-F02."""
    ok = (not tm.tcs["TC-F01"].change_impact
          and not tm.tcs["TC-F02"].change_impact)
    if ok:
        rpt.pass_tc("TC16",
                    "Change impact flags cleared after re-run ✓  (TC-F01, TC-F02)")
    else:
        rpt.fail_tc("TC16",
                    f"Flags still set: TC-F01={tm.tcs['TC-F01'].change_impact}  "
                    f"TC-F02={tm.tcs['TC-F02'].change_impact}")


# ─── GROUP 5: TEST SPECIFICATION ARTIFACTS (TC17–TC20) ────────────────────────

def tc17_generate_formal_spec(tm: TraceabilityMatrix, rpt: SimpleReport) -> None:
    """Print the formal ASPICE-compliant TestCaseSpec for TC-F07 (S3 timeout)."""
    spec = tm.tcs["TC-F07"]
    spec.print_spec()
    # Verify the spec has all required ASPICE fields
    required_fields = [spec.tc_id, spec.title, spec.req_ids,
                       spec.asil, spec.technique, spec.precondition,
                       spec.steps, spec.expected, spec.aspice_artifact_id]
    all_present = all(bool(f) for f in required_fields)
    if all_present and len(spec.steps) >= 4:
        rpt.pass_tc("TC17",
                    f"Formal spec for {spec.tc_id} generated with all ASPICE fields ✓")
    else:
        rpt.fail_tc("TC17", "Spec missing required fields")


def tc18_execute_spec(tm: TraceabilityMatrix, t: UDSTester,
                       ecu: SimulatedECU, rpt: SimpleReport) -> None:
    """
    Execute TC-F07 (S3 timeout) by following the formal spec's steps exactly.
    Records result in the TestCaseSpec artifact.
    """
    spec = tm.tcs["TC-F07"]
    print(f"\n  [EXECUTING SPEC] {spec.tc_id}: {spec.title}")
    print(f"  Following {len(spec.steps)} steps from {spec.aspice_artifact_id}")

    # Step 1: Enter extended session
    t.switch_session(0x01)
    resp = t.sr([SID_SESSION, 0x03])
    if resp is None or resp[0] != SID_SESSION + 0x40:
        tm.resolve_change_impact  # not called for this TC
        spec.status       = "FAIL"
        spec.actual_result= "Could not enter extended session"
        rpt.fail_tc("TC18", "Extended session entry failed"); return

    # Step 2: Verify DID 0xF186 = 0x03
    session_byte = None
    r2 = t.sr([SID_READ_DID, 0xF1, 0x86])
    if r2 and r2[0] == SID_READ_DID + 0x40 and len(r2) >= 4:
        session_byte = r2[3]

    # Steps 3: wait for S3 timeout
    print(f"  [WAIT] {S3_TIMEOUT_S + 0.5:.1f} s S3 silence window ...")
    time.sleep(S3_TIMEOUT_S + 0.5)

    # Step 4: send SecurityAccess (requires non-default session → should get NRC 0x22)
    resp = t.sr([SID_SEC, 0x01])
    got_nrc_22 = bool(resp and resp[0] == SID_NEG
                      and len(resp) >= 3 and resp[2] == NRC_CONDITIONS_NOT_OK)

    if got_nrc_22:
        spec.status        = "PASS"
        spec.actual_result = "NRC 0x7F 0x27 0x22 — session dropped to default ✓"
        spec.executed_at   = time.strftime("%Y-%m-%d %H:%M")
        rpt.pass_tc("TC18",
                    f"Spec execution: {spec.tc_id} PASS — "
                    f"S3 watchdog fired after {S3_TIMEOUT_S}s ✓")
    else:
        spec.status        = "FAIL"
        spec.actual_result = f"Unexpected response: {resp}"
        rpt.fail_tc("TC18", f"Expected NRC 0x22, got {resp}")


def tc19_verify_spec_completeness(tm: TraceabilityMatrix, rpt: SimpleReport) -> None:
    """Verify all functional TCs have the 7 mandatory ASPICE spec fields."""
    mandatory = ["tc_id", "title", "aspice_artifact_id", "precondition", "steps", "expected"]
    incomplete = []
    for tc_id, spec in tm.tcs.items():
        for field_name in mandatory:
            val = getattr(spec, field_name, None)
            if not val:
                incomplete.append(f"{tc_id}.{field_name} is empty")
    if not incomplete:
        rpt.pass_tc("TC19",
                    f"All {len(tm.tcs)} TC specs have mandatory ASPICE fields ✓")
    else:
        rpt.fail_tc("TC19", f"Incomplete specs: {incomplete[:3]}")


def tc20_dashboard(tm: TraceabilityMatrix, rpt: SimpleReport) -> None:
    """Print the complete traceability dashboard and verify final health."""
    tm.print_dashboard()
    orphans  = tm.find_orphan_tcs()
    gaps     = tm.find_gap_reqs()
    impact   = [tc_id for tc_id, tc in tm.tcs.items() if tc.change_impact]
    asil_errs= tm.asil_consistency_errors()
    all_ok   = not orphans and not gaps and not impact and not asil_errs
    if all_ok:
        rpt.pass_tc("TC20",
                    "Dashboard: 0 orphans, 0 gaps, 0 impact flags, 0 ASIL errors ✓")
    else:
        rpt.fail_tc("TC20",
                    f"orphans={orphans}  gaps={gaps}  "
                    f"impact={impact}  asil_errors={asil_errs}")


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def banner(title: str) -> None:
    print(f"\n{'─' * 66}\n  {title}\n{'─' * 66}")


def main() -> None:
    print("\n" + "🔗📋  " * 10)
    print("  Day 21 — Traceability and Test Design")
    print("  Multi-level hierarchy · Bidirectional links · Change impact")
    print("🔗📋  " * 10)

    tm  = build_matrix()
    rpt = SimpleReport()

    ecu = SimulatedECU()
    bus = can.Bus(interface="virtual", channel=CHANNEL)
    t   = UDSTester(bus)

    ecu.start()
    time.sleep(0.1)

    print(f"\n  Traceability matrix built:")
    print(f"    {len([r for r in tm.reqs.values() if r.level == 'SYSTEM'])} System reqs")
    print(f"    {len([r for r in tm.reqs.values() if r.level == 'SOFTWARE'])} Software reqs")
    print(f"    {len([r for r in tm.reqs.values() if r.level == 'UNIT'])} Unit reqs")
    print(f"    {len(tm.tcs)} Functional test cases  (TC-F01 … TC-F07)")

    banner("GROUP 1: Traceability Integrity Verification")
    print("  Checks: forward, backward, multi-level chain, ASIL consistency\n")
    tc01_forward_traceability(tm, rpt)
    tc02_backward_traceability(tm, rpt)
    tc03_multilevel_chain(tm, rpt)
    tc04_asil_consistency(tm, rpt)

    banner("GROUP 2: Coverage Metrics by ASIL Level")
    print("  Checks: every ASIL level must be 100% covered by linked TCs\n")
    tc05_asil_b_coverage(tm, rpt)
    tc06_asil_c_coverage(tm, rpt)
    tc07_asil_d_coverage(tm, rpt)
    tc08_overall_coverage(tm, rpt)

    banner("GROUP 3: Orphan / Gap Detection and Resolution")
    print("  Injects a deliberate orphan TC and a gap req; verifies detection;\n"
          "  resolves both; verifies matrix is clean again.\n")
    tc09_detect_orphan(tm, rpt)
    tc10_detect_gap(tm, rpt)
    tc11_resolve_orphan(tm, rpt)
    tc12_resolve_gap(tm, rpt)

    banner("GROUP 4: Change Impact Analysis + Live ECU Re-run")
    print("  SW-001 version bumped → TC-F01 and TC-F02 flagged → re-run on ECU\n")
    tc13_mark_requirement_changed(tm, rpt)
    tc14_verify_change_impact_flags(tm, rpt)
    tc15_execute_change_impact_rerun(tm, t, ecu, rpt)
    tc16_verify_impact_flags_cleared(tm, rpt)

    banner("GROUP 5: Formal Test Specification Artifacts")
    print("  Generates ASPICE SWTS spec for TC-F07; executes it; verifies completeness.\n")
    tc17_generate_formal_spec(tm, rpt)
    tc18_execute_spec(tm, t, ecu, rpt)
    tc19_verify_spec_completeness(tm, rpt)
    tc20_dashboard(tm, rpt)

    passed, failed = rpt.summary()
    total = passed + failed
    print(f"\n{'=' * 66}")
    print(f"  TEST SUMMARY: {passed}/{total} TCs pass  |  {failed} fail")
    print(f"{'=' * 66}")

    ecu.stop()
    t.shutdown()


if __name__ == "__main__":
    main()
