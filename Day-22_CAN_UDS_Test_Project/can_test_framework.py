"""
Day 22 — CAN + UDS Test Automation Framework
=============================================================================
A complete, production-style embedded test automation project that brings
together every concept from Days 1–21:

  LAYER 1  DBC Parser          can_matrix.dbc  →  SignalDecoder/Encoder
  LAYER 2  CAN Simulator       EngineStatus broadcast @ 10 ms (0x200)
  LAYER 3  ECU Simulator       Sessions, DIDs, DTCs, SecurityAccess
  LAYER 4  UDS Client          ISO-TP, full UDS service set
  LAYER 5  Timing Verifier     Cycle-time + P2 response-time checks
  LAYER 6  Test Runner         Assertions, structured logging, results
  LAYER 7  Report Generator    JSON report  +  self-contained HTML report

  GROUP 1  TC01–TC04  CAN Infrastructure & DBC
  GROUP 2  TC05–TC07  Timing Verification
  GROUP 3  TC08–TC09  ECU Health Checks
  GROUP 4  TC10–TC11  UDS Session Control
  GROUP 5  TC12–TC13  Read Data By Identifier (0x22)
  GROUP 6  TC14–TC15  Negative Response Handling
  GROUP 7  TC16–TC17  DTC Lifecycle
  GROUP 8  TC18       Security Access (Seed / Key)
  GROUP 9  TC19–TC20  Automated Reports (JSON + HTML)

No hardware needed.
Install:  pip install python-can
Run:      python can_test_framework.py
Outputs:  test_run_YYYYMMDD_HHMMSS.log
          test_report_YYYYMMDD_HHMMSS.json
          test_report_YYYYMMDD_HHMMSS.html
"""

import can
import threading
import time
import struct
import random
import json
import logging
import re
import os
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple
from pathlib import Path

# ─── CONSTANTS ────────────────────────────────────────────────────────────────

ENGINE_STATUS_ID = 0x200          # EngineStatus broadcast CAN ID
ENGINE_CYCLE_MS  = 10             # Target broadcast interval (ms)
CHANNEL          = "vcan0"
TESTER_TX_ID     = 0x7E0
TESTER_RX_ID     = 0x7E8

SID_SESSION  = 0x10
SID_RESET    = 0x11
SID_SEC      = 0x27
SID_READ_DID = 0x22
SID_READ_DTC = 0x19
SID_CLR_DTC  = 0x14
SID_TP       = 0x3E
SID_NEG      = 0x7F

NRC_LENGTH_ERROR       = 0x13
NRC_CONDITIONS_NOT_OK  = 0x22
NRC_OUT_OF_RANGE       = 0x31
NRC_INVALID_KEY        = 0x35
NRC_EXCEEDED_ATTEMPTS  = 0x36
NRC_SUBFUNC_NOT_SUPP   = 0x12

SEC_SECRET       = 0xDEADBEEF
S3_TIMEOUT_S     = 2.0
OVER_TEMP_C      = 105.0
DTC_P0217_H      = 0x02
DTC_P0217_L      = 0x17
DTC_CONFIRMED    = 0xAF

P2_MAX_MS        = 50.0           # Maximum UDS P2 response time we assert
DBC_FILE         = Path(__file__).with_name("can_matrix.dbc")

# ─── LAYER 1: DBC DATA MODEL ──────────────────────────────────────────────────

@dataclass
class DBCSignal:
    name:       str
    start_bit:  int
    length:     int
    little_end: bool        # True = Intel / little-endian  (format @1)
    is_signed:  bool        # True = signed
    scale:      float
    offset:     float
    min_val:    float
    max_val:    float
    unit:       str

@dataclass
class DBCMessage:
    can_id:  int
    name:    str
    dlc:     int
    sender:  str
    signals: Dict[str, DBCSignal] = field(default_factory=dict)

    def encode(self, values: Dict[str, float]) -> bytearray:
        """
        Encode a dict of {signal_name: physical_value} into an 8-byte frame.
        Only Intel (little-endian) signals supported.
        """
        data = bytearray(self.dlc)
        int_val = 0
        for sig_name, phys in values.items():
            sig = self.signals[sig_name]
            raw = round((phys - sig.offset) / sig.scale)
            raw = max(0, min(raw, (1 << sig.length) - 1))
            int_val |= (raw & ((1 << sig.length) - 1)) << sig.start_bit
        data[:] = int_val.to_bytes(self.dlc, "little")
        return data

    def decode(self, data: bytes) -> Dict[str, float]:
        """Decode raw bytes → {signal_name: physical_value}."""
        int_val = int.from_bytes(data[: self.dlc], "little")
        result  = {}
        for sig in self.signals.values():
            raw = (int_val >> sig.start_bit) & ((1 << sig.length) - 1)
            if sig.is_signed and raw >= (1 << (sig.length - 1)):
                raw -= 1 << sig.length
            result[sig.name] = raw * sig.scale + sig.offset
        return result


class DBCParser:
    """
    Minimal DBC parser — handles BO_ (messages) and SG_ (signals).
    Supports Intel byte order (format code @1) with unsigned (+) or signed (-).
    """
    _MSG_RE = re.compile(
        r'BO_\s+(\d+)\s+(\w+)\s*:\s*(\d+)\s+(\w+)'
    )
    _SIG_RE = re.compile(
        r'SG_\s+(\w+)\s*:\s*'
        r'(\d+)\|(\d+)@([01])([+-])\s*'
        r'\(([^,]+),([^)]+)\)\s*'
        r'\[([^|]+)\|([^\]]+)\]\s*'
        r'"([^"]*)"'
    )

    def parse(self, content: str) -> Dict[int, DBCMessage]:
        messages: Dict[int, DBCMessage] = {}
        current: Optional[DBCMessage]   = None
        for line in content.splitlines():
            line = line.strip()
            m = self._MSG_RE.match(line)
            if m:
                current = DBCMessage(
                    can_id=int(m.group(1)),
                    name=m.group(2),
                    dlc=int(m.group(3)),
                    sender=m.group(4),
                )
                messages[current.can_id] = current
                continue
            if current:
                s = self._SIG_RE.match(line)
                if s:
                    current.signals[s.group(1)] = DBCSignal(
                        name      =s.group(1),
                        start_bit =int(s.group(2)),
                        length    =int(s.group(3)),
                        little_end=s.group(4) == "1",
                        is_signed =s.group(5) == "-",
                        scale     =float(s.group(6)),
                        offset    =float(s.group(7)),
                        min_val   =float(s.group(8)),
                        max_val   =float(s.group(9)),
                        unit      =s.group(10),
                    )
        return messages

    def parse_file(self, path: Path) -> Dict[int, DBCMessage]:
        return self.parse(path.read_text())


# ─── LAYER 2: ISO-TP HELPERS ──────────────────────────────────────────────────

def _sf(uds: list) -> bytes:
    assert 1 <= len(uds) <= 7
    return bytes([len(uds)] + list(uds) + [0] * (7 - len(uds)))

def _ff(uds: list) -> bytes:
    n = len(uds)
    return bytes([0x10 | ((n >> 8) & 0x0F), n & 0xFF] + list(uds[:6]))

def _cf(sn: int, chunk: list) -> bytes:
    return bytes([0x20 | (sn & 0x0F)] + list(chunk) + [0] * (7 - len(chunk)))

def _fc(bs: int = 0, stmin: int = 5) -> bytes:
    return bytes([0x30, bs, stmin, 0, 0, 0, 0, 0])


# ─── LAYER 3: TIMING VERIFIER ─────────────────────────────────────────────────

class TimingVerifier:
    """
    Captures CAN frames from a bus and computes timing metrics.

    capture_cycle_times():  collect N frames, return inter-frame deltas (ms)
    measure_response_time(): send a UDS request, measure time until response
    """

    def __init__(self, bus: can.BusABC, logger: logging.Logger) -> None:
        self.bus    = bus
        self.log    = logger

    def capture_cycle_times(
        self,
        arb_id:  int,
        count:   int   = 10,
        timeout: float = 0.5,
    ) -> List[float]:
        """
        Wait for `count` frames matching arb_id.  Return inter-frame deltas in ms.
        Returns an empty list if fewer than 2 frames arrive within `timeout`.
        """
        # Drain stale buffered frames before real-time measurement
        drain_end = time.monotonic() + 0.040
        while time.monotonic() < drain_end:
            self.bus.recv(timeout=0.003)

        timestamps: List[float] = []
        deadline = time.monotonic() + timeout
        while len(timestamps) < count and time.monotonic() < deadline:
            remaining = max(0.005, deadline - time.monotonic())
            frame = self.bus.recv(timeout=remaining)
            if frame and frame.arbitration_id == arb_id:
                timestamps.append(time.monotonic())
        if len(timestamps) < 2:
            return []
        deltas = [(timestamps[i + 1] - timestamps[i]) * 1000
                  for i in range(len(timestamps) - 1)]
        self.log.debug(
            f"CycleTimes arb_id=0x{arb_id:X}: "
            f"n={len(deltas)} mean={sum(deltas)/len(deltas):.1f}ms "
            f"min={min(deltas):.1f}ms max={max(deltas):.1f}ms"
        )
        return deltas

    def measure_uds_response_ms(
        self,
        uds:       list,
        rx_arb_id: int,
        timeout:   float = 0.2,
    ) -> float:
        """
        Send UDS request on self.bus, measure ms until first response frame.
        Uses timing_bus for TX so uds_bus stays uncontaminated.
        """
        # Drain stale frames first
        drain_end = time.monotonic() + 0.020
        while time.monotonic() < drain_end:
            self.bus.recv(timeout=0.002)
        self.bus.send(can.Message(
            arbitration_id=TESTER_TX_ID,
            data=_sf(uds),
            is_extended_id=False,
        ))
        t0       = time.monotonic()
        deadline = t0 + timeout
        while time.monotonic() < deadline:
            remaining = max(0.001, deadline - time.monotonic())
            frame = self.bus.recv(timeout=remaining)
            if frame and frame.arbitration_id == rx_arb_id:
                elapsed = (time.monotonic() - t0) * 1000
                self.log.debug(f"UDS response latency: {elapsed:.2f} ms")
                return elapsed
        return float("inf")


# ─── LAYER 4: TEST RESULT & RUNNER ────────────────────────────────────────────

@dataclass
class TestResult:
    tc_id:       str
    group:       str
    title:       str
    status:      str          # "PASS" | "FAIL" | "ERROR"
    duration_ms: float
    timestamp:   str
    detail:      str = ""


class TestRunner:
    """
    Runs test functions, collects TestResult objects, generates reports.

    Usage:
        runner = TestRunner("My Project", "1.0.0")
        runner.run_tc("TC01", "Group 1", "Check something", lambda: assert 1 == 1)
        runner.generate_json()   # → test_report_YYYYMMDD.json
        runner.generate_html()   # → test_report_YYYYMMDD.html
    """

    def __init__(self, project: str, version: str = "1.0.0") -> None:
        self.project = project
        self.version = version
        self.results: List[TestResult] = []
        self.run_id  = time.strftime("%Y%m%d_%H%M%S")
        self.start_t = time.monotonic()
        self.log     = self._setup_logger()
        self.log_path = f"test_run_{self.run_id}.log"

    def _setup_logger(self) -> logging.Logger:
        log = logging.getLogger("CanTest")
        log.setLevel(logging.DEBUG)
        run_id = time.strftime("%Y%m%d_%H%M%S")
        # File handler — DEBUG (every UDS byte, every CAN frame timing)
        fh = logging.FileHandler(f"test_run_{run_id}.log")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(
            "%(asctime)s.%(msecs)03d  [%(levelname)-7s]  %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        log.addHandler(fh)
        return log

    def run_tc(
        self,
        tc_id: str,
        group: str,
        title: str,
        fn,
    ) -> TestResult:
        self.log.info(f"START  {tc_id}  {title}")
        start = time.monotonic()
        try:
            fn()
            duration_ms = (time.monotonic() - start) * 1000
            result = TestResult(tc_id, group, title, "PASS",
                                duration_ms, time.strftime("%Y-%m-%dT%H:%M:%S"))
            print(f"  ✅ PASS  {tc_id}  {title}")
        except AssertionError as exc:
            duration_ms = (time.monotonic() - start) * 1000
            detail = str(exc)
            result = TestResult(tc_id, group, title, "FAIL",
                                duration_ms, time.strftime("%Y-%m-%dT%H:%M:%S"),
                                detail)
            print(f"  ❌ FAIL  {tc_id}  {title}")
            if detail:
                print(f"        ↳ {detail[:90]}")
            self.log.warning(f"FAIL   {tc_id}: {detail}")
        except Exception as exc:
            duration_ms = (time.monotonic() - start) * 1000
            detail = f"{type(exc).__name__}: {exc}"
            result = TestResult(tc_id, group, title, "ERROR",
                                duration_ms, time.strftime("%Y-%m-%dT%H:%M:%S"),
                                detail)
            print(f"  💥 ERROR {tc_id}  {title}")
            print(f"        ↳ {detail[:90]}")
            self.log.error(f"ERROR  {tc_id}: {detail}")
        self.results.append(result)
        self.log.info(
            f"END    {tc_id}  {result.status}  ({result.duration_ms:.1f} ms)"
        )
        return result

    def generate_json(self) -> str:
        passed   = sum(1 for r in self.results if r.status == "PASS")
        failed   = sum(1 for r in self.results if r.status != "PASS")
        duration = time.monotonic() - self.start_t
        data = {
            "run_id":            self.run_id,
            "project":           self.project,
            "framework_version": self.version,
            "timestamp":         time.strftime("%Y-%m-%dT%H:%M:%S"),
            "total":             len(self.results),
            "passed":            passed,
            "failed":            failed,
            "pass_rate":         f"{round(passed / max(len(self.results), 1) * 100)}%",
            "duration_s":        round(duration, 2),
            "test_cases": [
                {
                    "tc_id":       r.tc_id,
                    "group":       r.group,
                    "title":       r.title,
                    "status":      r.status,
                    "duration_ms": round(r.duration_ms, 2),
                    "timestamp":   r.timestamp,
                    "detail":      r.detail,
                }
                for r in self.results
            ],
        }
        path = f"test_report_{self.run_id}.json"
        with open(path, "w") as fh:
            json.dump(data, fh, indent=2)
        self.log.info(f"JSON report written → {path}")
        return path

    def generate_html(self) -> str:
        passed   = sum(1 for r in self.results if r.status == "PASS")
        failed   = sum(1 for r in self.results if r.status != "PASS")
        duration = time.monotonic() - self.start_t
        pass_pct = round(passed / max(len(self.results), 1) * 100)

        rows = "".join(
            f"<tr style='background:{'#d4edda' if r.status=='PASS' else '#f8d7da'}'>"
            f"<td>{r.tc_id}</td>"
            f"<td>{r.group}</td>"
            f"<td>{r.title}</td>"
            f"<td><b>{'✅ PASS' if r.status=='PASS' else '❌ '+r.status}</b></td>"
            f"<td>{round(r.duration_ms,1)} ms</td>"
            f"<td>{r.timestamp}</td>"
            f"<td style='font-size:12px;color:#555'>{(r.detail or '—')[:80]}</td>"
            f"</tr>"
            for r in self.results
        )

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>{self.project} — Test Report</title>
  <style>
    body{{font-family:Arial,sans-serif;margin:40px;background:#f4f4f4}}
    h1{{color:#2c3e50;margin-bottom:4px}}
    .meta{{color:#888;font-size:12px;margin-bottom:24px}}
    .cards{{display:flex;gap:16px;margin-bottom:28px;flex-wrap:wrap}}
    .card{{background:#fff;padding:18px 24px;border-radius:8px;
           box-shadow:0 2px 6px rgba(0,0,0,.1);min-width:100px;text-align:center}}
    .label{{font-size:11px;color:#777;text-transform:uppercase;letter-spacing:.5px}}
    .val{{font-size:30px;font-weight:700;margin-top:4px}}
    .green{{color:#27ae60}} .red{{color:#e74c3c}} .grey{{color:#555}}
    table{{border-collapse:collapse;width:100%;background:#fff;
           border-radius:8px;overflow:hidden;box-shadow:0 2px 6px rgba(0,0,0,.1)}}
    th{{background:#2c3e50;color:#fff;padding:11px 14px;text-align:left;font-size:13px}}
    td{{padding:9px 14px;border-bottom:1px solid #eee;font-size:13px}}
  </style>
</head>
<body>
  <h1>🚗 {self.project}</h1>
  <p class="meta">Run ID: {self.run_id} &nbsp;|&nbsp;
     {time.strftime("%Y-%m-%d %H:%M:%S")} &nbsp;|&nbsp;
     Framework v{self.version}</p>
  <div class="cards">
    <div class="card"><div class="label">Total</div>
      <div class="val grey">{len(self.results)}</div></div>
    <div class="card"><div class="label">Passed</div>
      <div class="val green">{passed}</div></div>
    <div class="card"><div class="label">Failed</div>
      <div class="val {'red' if failed else 'green'}">{failed}</div></div>
    <div class="card"><div class="label">Pass Rate</div>
      <div class="val {'green' if pass_pct==100 else 'red'}">{pass_pct}%</div></div>
    <div class="card"><div class="label">Duration</div>
      <div class="val grey">{round(duration,1)} s</div></div>
  </div>
  <table>
    <thead><tr>
      <th>TC ID</th><th>Group</th><th>Title</th>
      <th>Status</th><th>Duration</th><th>Timestamp</th><th>Detail</th>
    </tr></thead>
    <tbody>{rows}</tbody>
  </table>
</body>
</html>
"""
        path = f"test_report_{self.run_id}.html"
        with open(path, "w") as fh:
            fh.write(html)
        self.log.info(f"HTML report written → {path}")
        return path

    def print_summary(self) -> None:
        passed = sum(1 for r in self.results if r.status == "PASS")
        failed = sum(1 for r in self.results if r.status != "PASS")
        total  = len(self.results)
        print(f"\n{'=' * 66}")
        print(f"  TEST SUMMARY: {passed}/{total} pass  |  {failed} fail  "
              f"|  {round((time.monotonic() - self.start_t), 1)}s")
        print(f"{'=' * 66}")


# ─── LAYER 5: SIMULATED ECU ───────────────────────────────────────────────────

class SimulatedECU(threading.Thread):
    """
    Simulates an automotive ECU on a virtual CAN bus.

    - Broadcasts EngineStatus (0x200) every ENGINE_CYCLE_MS milliseconds
    - Handles UDS services: SessionControl, Reset, SecurityAccess,
      ReadDID (0xF186, 0xF189, 0xF405), ReadDTC, ClearDTC, TesterPresent
    - Implements S3 session watchdog and SecurityAccess lockout
    """

    def __init__(self) -> None:
        super().__init__(daemon=True, name="SimECU")
        self.bus              = can.Bus(interface="virtual", channel=CHANNEL)
        self._stop            = threading.Event()
        self.session:  int    = 0x01
        self._last_diag_t     = time.monotonic()
        self.test_temperature: float = 25.0
        self.test_rpm:         float = 3000.0
        self.test_speed:       float = 60.0
        self._dtcs:            Dict  = {}
        self._unlocked         = False
        self._seed             = 0
        self._seed_issued      = False
        self._fail_count       = 0
        self._lockout_until    = 0.0
        # DBC for building EngineStatus frames
        self._dbc: Dict[int, DBCMessage] = DBCParser().parse_file(DBC_FILE)

    def stop(self) -> None:
        self._stop.set()
        self.bus.shutdown()

    # ── Private helpers ────────────────────────────────────────────────────────

    def _send(self, payload: list) -> None:
        if len(payload) <= 7:
            self.bus.send(can.Message(
                arbitration_id=TESTER_RX_ID,
                data=_sf(payload),
                is_extended_id=False,
            ))
            return
        self.bus.send(can.Message(
            arbitration_id=TESTER_RX_ID,
            data=_ff(payload),
            is_extended_id=False,
        ))
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            f = self.bus.recv(timeout=0.05)
            if f and f.arbitration_id == TESTER_TX_ID:
                break
        sn, offset = 1, 6
        while offset < len(payload):
            chunk = payload[offset: offset + 7]
            self.bus.send(can.Message(
                arbitration_id=TESTER_RX_ID,
                data=_cf(sn, chunk),
                is_extended_id=False,
            ))
            sn     = (sn + 1) & 0x0F
            offset += 7
            time.sleep(0.005)

    def _neg(self, sid: int, nrc: int) -> None:
        self._send([SID_NEG, sid, nrc])

    def _broadcast_engine_status(self) -> None:
        if ENGINE_STATUS_ID not in self._dbc:
            return
        msg = self._dbc[ENGINE_STATUS_ID]
        data = msg.encode({
            "EngineRPM":   self.test_rpm,
            "CoolantTemp": self.test_temperature,
            "EngineLoad":  50.0,
            "ThrottlePos": 20.0,
            "VehicleSpeed": self.test_speed,
        })
        self.bus.send(can.Message(
            arbitration_id=ENGINE_STATUS_ID,
            data=bytes(data),
            is_extended_id=False,
        ))

    def _update_dtcs(self) -> None:
        key = (DTC_P0217_H, DTC_P0217_L)
        if self.test_temperature > OVER_TEMP_C:
            self._dtcs[key] = DTC_CONFIRMED

    # ── UDS dispatch ───────────────────────────────────────────────────────────

    def _dispatch(self, uds: list) -> None:
        self._last_diag_t = time.monotonic()
        self._update_dtcs()
        sid = uds[0]

        if sid == SID_SESSION:
            if len(uds) < 2:
                self._neg(sid, NRC_LENGTH_ERROR); return
            sub = uds[1]
            if sub not in (0x01, 0x02, 0x03):
                self._neg(sid, NRC_SUBFUNC_NOT_SUPP); return
            self._unlocked    = False
            self._seed_issued = False
            self._fail_count  = 0
            self.session      = sub
            # P2=25ms (0x0019), P2*=5000ms (0x01F4)
            self._send([SID_SESSION + 0x40, sub, 0x00, 0x19, 0x01, 0xF4])

        elif sid == SID_RESET:
            if len(uds) < 2:
                self._neg(sid, NRC_LENGTH_ERROR); return
            self._send([SID_RESET + 0x40, uds[1]])
            def _do_reset():
                time.sleep(0.1)
                self.session   = 0x01
                self._unlocked = False
            threading.Thread(target=_do_reset, daemon=True).start()

        elif sid == SID_SEC:
            if len(uds) < 2:
                self._neg(sid, NRC_LENGTH_ERROR); return
            if self.session not in (0x02, 0x03):
                self._neg(sid, NRC_CONDITIONS_NOT_OK); return
            if time.monotonic() < self._lockout_until:
                self._neg(sid, NRC_EXCEEDED_ATTEMPTS); return
            sub = uds[1]
            if sub == 0x01:
                if self._unlocked:
                    self._send([SID_SEC + 0x40, sub, 0, 0, 0, 0])
                else:
                    seed = random.randint(1, 0xFFFFFFFF)
                    self._seed = seed; self._seed_issued = True
                    self._send([SID_SEC + 0x40, sub,
                                (seed >> 24) & 0xFF, (seed >> 16) & 0xFF,
                                (seed >>  8) & 0xFF,  seed & 0xFF])
            elif sub == 0x02:
                if not self._seed_issued:
                    self._neg(sid, 0x24); return
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
                        self._neg(sid, NRC_EXCEEDED_ATTEMPTS)
                    else:
                        self._neg(sid, NRC_INVALID_KEY)

        elif sid == SID_READ_DID:
            if len(uds) < 3:
                self._neg(sid, NRC_LENGTH_ERROR); return
            did = (uds[1] << 8) | uds[2]
            if did == 0xF186:
                self._send([SID_READ_DID + 0x40, uds[1], uds[2], self.session])
            elif did == 0xF189:
                ver = list("Day22-ECU-v1.0".encode("ascii"))
                self._send([SID_READ_DID + 0x40, uds[1], uds[2]] + ver)
            elif did == 0xF405:
                raw = int(self.test_temperature * 10)
                self._send([SID_READ_DID + 0x40, uds[1], uds[2],
                            (raw >> 8) & 0xFF, raw & 0xFF])
            else:
                self._neg(sid, NRC_OUT_OF_RANGE)

        elif sid == SID_READ_DTC:
            sub = uds[1] if len(uds) >= 2 else 0
            if sub != 0x02:
                self._neg(sid, NRC_SUBFUNC_NOT_SUPP); return
            payload = [SID_READ_DTC + 0x40, sub, 0xFF]
            for (h, l), s in self._dtcs.items():
                payload += [h, l, s]
            self._send(payload)

        elif sid == SID_CLR_DTC:
            if len(uds) >= 4 and uds[1] == 0xFF and uds[2] == 0xFF and uds[3] == 0xFF:
                self._dtcs.clear()
                self._send([SID_CLR_DTC + 0x40])
            else:
                self._neg(sid, NRC_OUT_OF_RANGE)

        elif sid == SID_TP:
            if len(uds) >= 2 and (uds[1] & 0x80):
                return
            self._send([SID_TP + 0x40, uds[1] if len(uds) >= 2 else 0x00])

        else:
            self._neg(sid, 0x11)

    def run(self) -> None:
        last_broadcast = time.monotonic()
        while not self._stop.is_set():
            now = time.monotonic()
            # Periodic EngineStatus broadcast
            if now - last_broadcast >= ENGINE_CYCLE_MS / 1000.0:
                self._broadcast_engine_status()
                last_broadcast = now
            # S3 session watchdog
            if self.session != 0x01 and now - self._last_diag_t > S3_TIMEOUT_S:
                self.session   = 0x01
                self._unlocked = False
            # UDS frame handler
            frame = self.bus.recv(timeout=0.005)
            if frame is None or frame.arbitration_id != TESTER_TX_ID:
                continue
            data     = bytes(frame.data)
            pci_type = (data[0] >> 4) & 0x0F
            if pci_type == 0:
                length = data[0] & 0x0F
                uds    = list(data[1: 1 + length])
                if uds:
                    self._dispatch(uds)


# ─── LAYER 6: UDS TESTER ──────────────────────────────────────────────────────

class UDSTester:
    TIMEOUT_S = 3.0
    STMIN_MS  = 5

    def __init__(self, bus: can.BusABC, logger: logging.Logger) -> None:
        self.bus = bus
        self.log = logger

    def shutdown(self) -> None:
        self.bus.shutdown()

    def _send(self, uds: list) -> None:
        hex_str = " ".join(f"{b:02X}" for b in uds)
        self.log.debug(f"UDS TX  [{hex_str}]")
        if len(uds) <= 7:
            self.bus.send(can.Message(
                arbitration_id=TESTER_TX_ID,
                data=_sf(uds),
                is_extended_id=False,
            ))
            return
        self.bus.send(can.Message(
            arbitration_id=TESTER_TX_ID,
            data=_ff(uds),
            is_extended_id=False,
        ))
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            f = self.bus.recv(timeout=0.05)
            if f and f.arbitration_id == TESTER_RX_ID:
                break
        sn, offset = 1, 6
        while offset < len(uds):
            chunk = uds[offset: offset + 7]
            self.bus.send(can.Message(
                arbitration_id=TESTER_TX_ID,
                data=_cf(sn, chunk),
                is_extended_id=False,
            ))
            sn     = (sn + 1) & 0x0F
            offset += 7
            time.sleep(self.STMIN_MS / 1000.0)

    def _recv(self, timeout: float = None) -> Optional[list]:
        deadline  = time.monotonic() + (timeout or self.TIMEOUT_S)
        collected = []
        total_exp = 0
        while time.monotonic() < deadline:
            remaining = max(0.001, deadline - time.monotonic())
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
                if uds:
                    hex_str = " ".join(f"{b:02X}" for b in uds)
                    self.log.debug(f"UDS RX  [{hex_str}]")
                return uds
            elif pci_type == 1:
                total_exp = ((fb & 0x0F) << 8) | frame.data[1]
                collected = list(frame.data[2:])
                self.bus.send(can.Message(
                    arbitration_id=TESTER_TX_ID,
                    data=_fc(0, self.STMIN_MS),
                    is_extended_id=False,
                ))
            elif pci_type == 2:
                collected += list(frame.data[1:])
                if len(collected) >= total_exp:
                    result = collected[:total_exp]
                    self.log.debug(
                        f"UDS RX  [multi-frame  {len(result)} bytes]"
                    )
                    return result
        return collected[:total_exp] if collected else None

    def sr(self, uds: list, timeout: float = None) -> Optional[list]:
        """Send + receive (request / response)."""
        self._send(uds)
        return self._recv(timeout=timeout)

    def switch_session(self, sub: int) -> Optional[list]:
        return self.sr([SID_SESSION, sub])


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

def _clean_dtc(ecu: SimulatedECU, t: UDSTester) -> None:
    ecu.test_temperature = 25.0
    t.sr([SID_CLR_DTC, 0xFF, 0xFF, 0xFF])
    time.sleep(0.02)


# ─── TEST CASES ───────────────────────────────────────────────────────────────
#
#  Each function receives the shared fixtures and is passed as a lambda to
#  runner.run_tc().  Failures raise AssertionError with descriptive messages.

def _drain_bus(bus: can.BusABC, window_s: float = 0.050) -> None:
    """Discard all frames currently queued in the bus receive buffer."""
    deadline = time.monotonic() + window_s
    while time.monotonic() < deadline:
        bus.recv(timeout=0.003)


def _banner(title: str) -> None:
    print(f"\n{'─' * 66}\n  {title}\n{'─' * 66}")


# ── GROUP 1: CAN Infrastructure & DBC (TC01–TC04) ────────────────────────────

def tc01_can_frame_construction(dbc: Dict[int, DBCMessage], log: logging.Logger) -> None:
    """Build an EngineStatus frame manually; verify arb ID and DLC."""
    msg = dbc[ENGINE_STATUS_ID]
    assert msg.can_id == ENGINE_STATUS_ID, \
        f"Expected CAN ID 0x{ENGINE_STATUS_ID:X}, got 0x{msg.can_id:X}"
    assert msg.dlc == 8, f"Expected DLC=8, got {msg.dlc}"
    assert "EngineRPM" in msg.signals, "EngineRPM signal not found in DBC"
    assert "CoolantTemp" in msg.signals, "CoolantTemp signal not found in DBC"
    assert "VehicleSpeed" in msg.signals, "VehicleSpeed signal not found in DBC"
    frame = can.Message(arbitration_id=msg.can_id,
                        data=bytearray(msg.dlc),
                        is_extended_id=False)
    assert frame.arbitration_id == 0x200
    log.debug(f"EngineStatus: ID=0x{msg.can_id:X}  DLC={msg.dlc}  "
              f"signals={list(msg.signals.keys())}")


def tc02_dbc_signal_encoding(dbc: Dict[int, DBCMessage], log: logging.Logger) -> None:
    """
    Encode: RPM=3000, CoolantTemp=90°C, EngineLoad=75%, ThrottlePos=25%, Speed=80.
    Expected bytes: [0xE0, 0x2E, 0x82, 0xBF, 0x40, 0x50, 0x00, 0x00]
    """
    msg  = dbc[ENGINE_STATUS_ID]
    data = msg.encode({
        "EngineRPM":   3000.0,
        "CoolantTemp": 90.0,
        "EngineLoad":  75.0,
        "ThrottlePos": 25.0,
        "VehicleSpeed": 80.0,
    })
    # RPM raw = 3000/0.25 = 12000 = 0x2EE0 → bytes [0xE0, 0x2E]
    assert data[0] == 0xE0, f"RPM byte[0]: expected 0xE0, got 0x{data[0]:02X}"
    assert data[1] == 0x2E, f"RPM byte[1]: expected 0x2E, got 0x{data[1]:02X}"
    # CoolantTemp raw = 90+40 = 130 = 0x82
    assert data[2] == 0x82, f"Temp byte[2]: expected 0x82, got 0x{data[2]:02X}"
    log.debug(f"Encoded: {data.hex().upper()}")


def tc03_dbc_signal_decoding(dbc: Dict[int, DBCMessage], log: logging.Logger) -> None:
    """
    Decode the known byte sequence back to signal values and verify with tolerance.
    """
    msg         = dbc[ENGINE_STATUS_ID]
    raw_bytes   = bytes([0xE0, 0x2E, 0x82, 0xBF, 0x40, 0x50, 0x00, 0x00])
    decoded     = msg.decode(raw_bytes)

    # RPM: 0x2EE0 = 12000 raw → 12000 * 0.25 = 3000.0
    assert abs(decoded["EngineRPM"] - 3000.0) < 1.0, \
        f"RPM: expected ~3000.0, got {decoded['EngineRPM']}"
    # CoolantTemp: 0x82 = 130 raw → 130*1 - 40 = 90.0
    assert abs(decoded["CoolantTemp"] - 90.0) < 0.1, \
        f"Temp: expected 90.0, got {decoded['CoolantTemp']}"
    # VehicleSpeed: 0x50 = 80 raw → 80 km/h
    assert abs(decoded["VehicleSpeed"] - 80.0) < 0.1, \
        f"Speed: expected 80.0, got {decoded['VehicleSpeed']}"
    log.debug(f"Decoded: {decoded}")


def tc04_round_trip(dbc: Dict[int, DBCMessage], ecu: SimulatedECU,
                    timing_bus: can.BusABC, log: logging.Logger) -> None:
    """
    Set ECU broadcast values, capture the next EngineStatus frame from the bus,
    decode it with DBC, and verify the values match.
    """
    ecu.test_rpm         = 4000.0
    ecu.test_speed       = 120.0
    ecu.test_temperature = 70.0
    time.sleep(0.030)   # wait for ECU to broadcast new values (3 × 10 ms)

    # Drain ALL buffered frames (may contain old values from earlier broadcasts)
    drain_end = time.monotonic() + 0.040
    while time.monotonic() < drain_end:
        timing_bus.recv(timeout=0.003)

    # Capture the very next fresh broadcast
    deadline = time.monotonic() + 0.05
    frame    = None
    while time.monotonic() < deadline:
        f = timing_bus.recv(timeout=0.005)
        if f and f.arbitration_id == ENGINE_STATUS_ID:
            frame = f
            break

    assert frame is not None, "No EngineStatus frame received from ECU broadcast"
    msg     = dbc[ENGINE_STATUS_ID]
    decoded = msg.decode(bytes(frame.data))
    assert abs(decoded["EngineRPM"]   - 4000.0) < 1.0,  \
        f"Round-trip RPM: expected ~4000, got {decoded['EngineRPM']}"
    assert abs(decoded["VehicleSpeed"] - 120.0) < 0.5,  \
        f"Round-trip Speed: expected 120, got {decoded['VehicleSpeed']}"
    log.debug(f"Round-trip decoded: RPM={decoded['EngineRPM']}  "
              f"Speed={decoded['VehicleSpeed']}  Temp={decoded['CoolantTemp']}")


# ── GROUP 2: Timing Verification (TC05–TC07) ──────────────────────────────────

def tc05_cycle_time(tv: TimingVerifier, log: logging.Logger) -> None:
    """
    Capture 10 EngineStatus broadcasts; verify mean cycle time is in [5, 20] ms.
    Tolerance is generous to account for OS scheduler jitter on macOS/VM.
    """
    deltas = tv.capture_cycle_times(
        arb_id=ENGINE_STATUS_ID, count=10, timeout=0.5
    )
    assert len(deltas) >= 5, \
        f"Too few frames captured: {len(deltas)} (need ≥5)"
    mean_ms = sum(deltas) / len(deltas)
    assert 5.0 <= mean_ms <= 20.0, \
        f"Cycle time {mean_ms:.1f} ms outside [5, 20] ms window"
    log.info(f"EngineStatus cycle time: mean={mean_ms:.1f}ms  "
             f"min={min(deltas):.1f}ms  max={max(deltas):.1f}ms")


def tc06_p2_response_time(tv: TimingVerifier, log: logging.Logger) -> None:
    """
    Send a ReadDID request via timing_bus and verify the UDS response arrives
    within P2_MAX_MS.  Using timing_bus (not uds_bus) keeps uds_bus clean.
    """
    elapsed_ms = tv.measure_uds_response_ms(
        uds=[SID_READ_DID, 0xF1, 0x86],
        rx_arb_id=TESTER_RX_ID,
        timeout=P2_MAX_MS / 1000.0 + 0.05,
    )
    assert elapsed_ms < P2_MAX_MS, \
        f"P2 response {elapsed_ms:.1f} ms exceeded P2_MAX={P2_MAX_MS} ms"
    log.info(f"P2 response latency: {elapsed_ms:.2f} ms  (limit={P2_MAX_MS} ms)")


def tc07_session_timing_bytes(t: UDSTester, log: logging.Logger) -> None:
    """
    Extended session response must include:
      bytes[2:4] = P2  = 0x0019 (25 ms)
      bytes[4:6] = P2* = 0x01F4 (500 × 10 ms = 5000 ms)
    """
    t.switch_session(0x01)          # ensure default first
    resp = t.sr([SID_SESSION, 0x03])
    assert resp is not None, "No response to DiagnosticSessionControl 0x03"
    assert resp[0] == SID_SESSION + 0x40, \
        f"Expected 0x{SID_SESSION+0x40:02X}, got 0x{resp[0]:02X}"
    assert len(resp) >= 6, f"Response too short: {len(resp)} bytes"
    p2     = (resp[2] << 8) | resp[3]
    p2star = (resp[4] << 8) | resp[5]
    assert p2 == 0x0019, f"P2 timing: expected 0x0019 (25 ms), got 0x{p2:04X}"
    assert p2star == 0x01F4, f"P2* timing: expected 0x01F4, got 0x{p2star:04X}"
    log.info(f"Session timing: P2={p2} ms  P2*={p2star * 10} ms")


# ── GROUP 3: ECU Health (TC08–TC09) ───────────────────────────────────────────

def tc08_ecu_reachability(t: UDSTester, log: logging.Logger) -> None:
    """ECU must acknowledge TesterPresent within timeout."""
    resp = t.sr([SID_TP, 0x00])
    assert resp is not None, "No response to TesterPresent (ECU unreachable)"
    assert resp[0] == SID_TP + 0x40, \
        f"Expected 0x{SID_TP+0x40:02X}, got 0x{resp[0]:02X}"
    log.info("ECU reachable: TesterPresent acknowledged")


def tc09_ecu_reset_to_default(t: UDSTester, ecu: SimulatedECU,
                               log: logging.Logger) -> None:
    """
    Enter extended session → send ECUReset → wait 200ms → verify session=0x01.
    """
    t.switch_session(0x03)
    resp = t.sr([SID_RESET, 0x01])
    assert resp is not None, "No response to ECUReset"
    assert resp[0] == SID_RESET + 0x40, \
        f"Expected 0x{SID_RESET+0x40:02X}, got 0x{resp[0]:02X}"
    time.sleep(0.2)
    resp2 = t.sr([SID_READ_DID, 0xF1, 0x86])
    assert resp2 is not None and len(resp2) >= 4, \
        "Could not read 0xF186 after reset"
    session_byte = resp2[3]
    assert session_byte == 0x01, \
        f"Expected defaultSession (0x01) after reset, got 0x{session_byte:02X}"
    log.info(f"ECUReset verified: session={session_byte:#04x} (defaultSession)")


# ── GROUP 4: UDS Session Control (TC10–TC11) ──────────────────────────────────

def tc10_programming_session(t: UDSTester, log: logging.Logger) -> None:
    """Enter programmingSession (0x02) and verify positive response."""
    t.switch_session(0x01)
    resp = t.sr([SID_SESSION, 0x02])
    assert resp is not None, "No response to programmingSession"
    assert resp[0] == SID_SESSION + 0x40, \
        f"Expected 0x{SID_SESSION+0x40:02X}, got 0x{resp[0]:02X}"
    assert resp[1] == 0x02, f"SubFunction echo: expected 0x02, got 0x{resp[1]:02X}"
    log.info("programmingSession (0x02) entered successfully")
    t.switch_session(0x01)  # back to default


def tc11_session_restriction_nrc(t: UDSTester, log: logging.Logger) -> None:
    """
    SecurityAccess in defaultSession must return NRC 0x22 (conditionsNotCorrect).
    """
    t.switch_session(0x01)
    resp = t.sr([SID_SEC, 0x01])
    assert resp is not None, "No response to SecurityAccess in default session"
    assert resp[0] == SID_NEG, \
        f"Expected NRC 0x7F, got 0x{resp[0]:02X} (service was not rejected)"
    assert resp[1] == SID_SEC, \
        f"NRC service echo: expected 0x{SID_SEC:02X}, got 0x{resp[1]:02X}"
    assert resp[2] == NRC_CONDITIONS_NOT_OK, \
        f"Expected NRC 0x22, got 0x{resp[2]:02X}"
    log.info("Session restriction: SecurityAccess in default → NRC 0x22 ✓")


# ── GROUP 5: Read Data By Identifier (TC12–TC13) ──────────────────────────────

def tc12_read_sw_version_did(t: UDSTester, log: logging.Logger) -> None:
    """
    ReadDID 0xF189 (SW version) must return valid printable ASCII data.
    """
    resp = t.sr([SID_READ_DID, 0xF1, 0x89])
    assert resp is not None, "No response to ReadDID 0xF189"
    assert resp[0] == SID_READ_DID + 0x40, \
        f"Expected 0x62, got 0x{resp[0]:02X}"
    assert len(resp) >= 4, "Response too short"
    version_bytes = bytes(resp[3:])
    try:
        version_str = version_bytes.decode("ascii").rstrip("\x00")
    except UnicodeDecodeError:
        raise AssertionError(
            f"DID 0xF189 data is not valid ASCII: {version_bytes.hex()}"
        )
    assert all(32 <= b <= 126 for b in version_bytes if b != 0), \
        f"Non-printable characters in SW version: {version_bytes.hex()}"
    log.info(f"SW Version (0xF189): '{version_str}'")


def tc13_read_coolant_temp_did(t: UDSTester, ecu: SimulatedECU,
                                log: logging.Logger) -> None:
    """
    ReadDID 0xF405 must return coolant temperature matching ecu.test_temperature.
    """
    ecu.test_temperature = 25.0
    time.sleep(0.02)
    resp = t.sr([SID_READ_DID, 0xF4, 0x05])
    assert resp is not None, "No response to ReadDID 0xF405"
    assert resp[0] == SID_READ_DID + 0x40, \
        f"Expected 0x62, got 0x{resp[0]:02X}"
    assert len(resp) >= 5, f"Response too short: {len(resp)} bytes"
    temp_raw = (resp[3] << 8) | resp[4]
    temp_c   = temp_raw / 10.0
    assert abs(temp_c - 25.0) < 0.2, \
        f"CoolantTemp: expected 25.0°C, got {temp_c}°C"
    log.info(f"CoolantTemp DID 0xF405: {temp_c}°C (raw=0x{temp_raw:04X})")


# ── GROUP 6: Negative Responses (TC14–TC15) ───────────────────────────────────

def tc14_unknown_did_nrc31(t: UDSTester, log: logging.Logger) -> None:
    """ReadDID with unsupported DID (0xAABB) must return NRC 0x31."""
    resp = t.sr([SID_READ_DID, 0xAA, 0xBB])
    assert resp is not None, "No response to unknown DID"
    assert resp[0] == SID_NEG, \
        f"Expected NRC 0x7F, got 0x{resp[0]:02X}"
    assert resp[2] == NRC_OUT_OF_RANGE, \
        f"Expected NRC 0x31 (requestOutOfRange), got 0x{resp[2]:02X}"
    log.info("Unknown DID 0xAABB → NRC 0x31 ✓")


def tc15_short_request_nrc13(t: UDSTester, log: logging.Logger) -> None:
    """
    ReadDID with only 1 byte of DID (missing second byte) must return NRC 0x13
    (incorrectMessageLengthOrInvalidFormat).
    """
    resp = t.sr([SID_READ_DID, 0xF1])      # only 1 DID byte instead of 2
    assert resp is not None, "No response to malformed ReadDID"
    assert resp[0] == SID_NEG, \
        f"Expected NRC 0x7F, got 0x{resp[0]:02X}"
    assert resp[2] == NRC_LENGTH_ERROR, \
        f"Expected NRC 0x13 (incorrectMessageLength), got 0x{resp[2]:02X}"
    log.info("Short request [22 F1] → NRC 0x13 ✓")


# ── GROUP 7: DTC Lifecycle (TC16–TC17) ────────────────────────────────────────

def tc16_dtc_set_by_fault(t: UDSTester, ecu: SimulatedECU,
                           log: logging.Logger) -> None:
    """
    Set test_temperature > OVER_TEMP_C → ReadDTC should confirm DTC P0217 (0x0217).
    """
    _clean_dtc(ecu, t)
    ecu.test_temperature = 110.0
    t.sr([SID_TP, 0x00])              # trigger _update_dtcs()
    resp = t.sr([SID_READ_DTC, 0x02, 0xFF])
    assert _dtc_present(resp, DTC_P0217_H, DTC_P0217_L), \
        f"DTC P0217 not found after over-temp injection. resp={resp}"
    log.info("DTC P0217 confirmed after temp=110°C injection ✓")


def tc17_dtc_clear(t: UDSTester, ecu: SimulatedECU,
                   log: logging.Logger) -> None:
    """After ClearDTC at safe temp, DTC P0217 must be absent."""
    _clean_dtc(ecu, t)           # sets temp=25, sends ClearDTC
    resp = t.sr([SID_READ_DTC, 0x02, 0xFF])
    assert not _dtc_present(resp, DTC_P0217_H, DTC_P0217_L), \
        "DTC P0217 still present after ClearDTC"
    log.info("DTC P0217 cleared successfully ✓")


# ── GROUP 8: Security Access (TC18) ───────────────────────────────────────────

def tc18_security_access_unlock(t: UDSTester, log: logging.Logger) -> None:
    """
    Full seed/key SecurityAccess in extendedDiagSession:
      1. Enter extended session
      2. RequestSeed (0x27 0x01) → receive 4-byte seed
      3. Compute key = seed XOR SEC_SECRET
      4. SendKey (0x27 0x02 + key) → positive response
    """
    t.switch_session(0x01)
    t.switch_session(0x03)

    # Request seed
    resp_seed = t.sr([SID_SEC, 0x01])
    assert resp_seed is not None, "No response to SecurityAccess requestSeed"
    assert resp_seed[0] == SID_SEC + 0x40, \
        f"Expected 0x67, got 0x{resp_seed[0]:02X}"
    assert len(resp_seed) >= 6, f"Seed response too short: {len(resp_seed)} bytes"

    seed = struct.unpack(">I", bytes(resp_seed[2:6]))[0]
    key  = seed ^ SEC_SECRET

    # Send key
    resp_key = t.sr([
        SID_SEC, 0x02,
        (key >> 24) & 0xFF, (key >> 16) & 0xFF,
        (key >>  8) & 0xFF,  key & 0xFF,
    ])
    assert resp_key is not None, "No response to SecurityAccess sendKey"
    assert resp_key[0] == SID_SEC + 0x40, \
        f"Expected 0x67, got 0x{resp_key[0]:02X} (key rejected?)"
    log.info(f"SecurityAccess unlocked: seed=0x{seed:08X}  key=0x{key:08X}")
    t.switch_session(0x01)


# ── GROUP 9: Automated Reports (TC19–TC20) ────────────────────────────────────

def tc19_json_report(runner: TestRunner, log: logging.Logger) -> None:
    """
    Generate JSON report with current results (TC01–TC18).
    Verify: file exists, is valid JSON, contains all expected fields.
    """
    path = runner.generate_json()
    assert os.path.exists(path), f"JSON report not created: {path}"
    with open(path) as fh:
        data = json.load(fh)
    assert "run_id"    in data, "JSON missing 'run_id'"
    assert "total"     in data, "JSON missing 'total'"
    assert "passed"    in data, "JSON missing 'passed'"
    assert "failed"    in data, "JSON missing 'failed'"
    assert "pass_rate" in data, "JSON missing 'pass_rate'"
    assert "test_cases" in data and isinstance(data["test_cases"], list), \
        "JSON missing 'test_cases' list"
    assert len(data["test_cases"]) >= 18, \
        f"Expected ≥18 TCs in JSON, got {len(data['test_cases'])}"
    log.info(f"JSON report: {path}  ({data['total']} TCs, {data['pass_rate']} pass)")


def tc20_html_report(runner: TestRunner, log: logging.Logger) -> None:
    """
    Generate HTML report with current results (TC01–TC19).
    Verify: file exists, contains expected structural elements.
    """
    path = runner.generate_html()
    assert os.path.exists(path), f"HTML report not created: {path}"
    content = Path(path).read_text()
    assert "<!DOCTYPE html>" in content, "HTML missing DOCTYPE"
    assert "PASS" in content,  "HTML missing any PASS result"
    assert "TC01" in content,  "HTML missing TC01"
    assert "TC18" in content,  "HTML missing TC18"
    assert "pass_rate" not in content.lower() or "pass" in content.lower(), \
        "HTML pass rate indicator missing"
    log.info(f"HTML report: {path}  ({len(content)} bytes)")


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main() -> None:
    print("\n" + "🚗🔧  " * 11)
    print("  Day 22 — CAN + UDS Test Automation Framework")
    print("  DBC Decoder · ECU Simulator · UDS Client · Timing · Reports")
    print("🚗🔧  " * 11)

    # ── Set up shared fixtures ─────────────────────────────────────────────────
    runner      = TestRunner("CAN/UDS Test Automation", version="1.0.0")
    log         = runner.log
    dbc         = DBCParser().parse_file(DBC_FILE)

    ecu         = SimulatedECU()
    uds_bus     = can.Bus(interface="virtual", channel=CHANNEL)
    timing_bus  = can.Bus(interface="virtual", channel=CHANNEL)
    t           = UDSTester(uds_bus, log)
    tv          = TimingVerifier(timing_bus, log)

    ecu.start()
    time.sleep(0.05)      # let ECU spin up

    log.info(f"Framework started: project='{runner.project}'  run_id={runner.run_id}")
    log.info(f"DBC loaded: {len(dbc)} messages  "
             f"({sum(len(m.signals) for m in dbc.values())} signals)")

    # ── GROUP 1: CAN Infrastructure & DBC ─────────────────────────────────────
    _banner("GROUP 1: CAN Infrastructure & DBC Signal Codec")

    runner.run_tc("TC01", "CAN Infrastructure",
                  "DBC parsed: EngineStatus has correct ID/DLC/signals",
                  lambda: tc01_can_frame_construction(dbc, log))

    runner.run_tc("TC02", "CAN Infrastructure",
                  "Signal encoding: RPM=3000 → bytes [E0 2E ...] correct",
                  lambda: tc02_dbc_signal_encoding(dbc, log))

    runner.run_tc("TC03", "CAN Infrastructure",
                  "Signal decoding: raw bytes → RPM=3000, Temp=90°C, Speed=80",
                  lambda: tc03_dbc_signal_decoding(dbc, log))

    runner.run_tc("TC04", "CAN Infrastructure",
                  "Round-trip: set ECU values → broadcast → capture → decode → verify",
                  lambda: tc04_round_trip(dbc, ecu, timing_bus, log))

    # ── GROUP 2: Timing Verification ──────────────────────────────────────────
    _banner("GROUP 2: Timing Verification")

    runner.run_tc("TC05", "Timing",
                  "EngineStatus cycle time: mean in [5, 20] ms",
                  lambda: tc05_cycle_time(tv, log))

    runner.run_tc("TC06", "Timing",
                  f"UDS P2 response time < {P2_MAX_MS} ms",
                  lambda: tc06_p2_response_time(tv, log))

    runner.run_tc("TC07", "Timing",
                  "Extended session P2=25ms, P2*=5000ms timing bytes",
                  lambda: tc07_session_timing_bytes(t, log))

    # GROUP 2 timing tests used timing_bus to TX; those responses are still
    # buffered on uds_bus.  Drain before any test that uses the UDS tester.
    _drain_bus(uds_bus)

    # ── GROUP 3: ECU Health ────────────────────────────────────────────────────
    _banner("GROUP 3: ECU Health Checks")

    runner.run_tc("TC08", "ECU Health",
                  "TesterPresent acknowledged (ECU reachable)",
                  lambda: tc08_ecu_reachability(t, log))

    runner.run_tc("TC09", "ECU Health",
                  "ECUReset returns to defaultSession (0x01)",
                  lambda: tc09_ecu_reset_to_default(t, ecu, log))

    # ── GROUP 4: UDS Session Control ──────────────────────────────────────────
    _banner("GROUP 4: UDS Session Control")

    runner.run_tc("TC10", "Session Control",
                  "programmingSession (0x10 0x02) → positive response 0x50 0x02",
                  lambda: tc10_programming_session(t, log))

    runner.run_tc("TC11", "Session Control",
                  "SecurityAccess in defaultSession → NRC 0x22 (conditionsNotCorrect)",
                  lambda: tc11_session_restriction_nrc(t, log))

    # ── GROUP 5: Read Data By Identifier ──────────────────────────────────────
    _banner("GROUP 5: Read Data By Identifier (0x22)")

    runner.run_tc("TC12", "ReadDID",
                  "DID 0xF189 SW version returns printable ASCII",
                  lambda: tc12_read_sw_version_did(t, log))

    runner.run_tc("TC13", "ReadDID",
                  "DID 0xF405 CoolantTemp returns 25.0°C",
                  lambda: tc13_read_coolant_temp_did(t, ecu, log))

    # ── GROUP 6: Negative Responses ───────────────────────────────────────────
    _banner("GROUP 6: Negative Response Handling")

    runner.run_tc("TC14", "Negative Responses",
                  "Unknown DID 0xAABB → NRC 0x31 (requestOutOfRange)",
                  lambda: tc14_unknown_did_nrc31(t, log))

    runner.run_tc("TC15", "Negative Responses",
                  "Malformed ReadDID (1-byte DID) → NRC 0x13 (incorrectMessageLength)",
                  lambda: tc15_short_request_nrc13(t, log))

    # ── GROUP 7: DTC Lifecycle ─────────────────────────────────────────────────
    _banner("GROUP 7: DTC Lifecycle (P0217)")

    runner.run_tc("TC16", "DTC Lifecycle",
                  "Inject over-temp fault → DTC P0217 confirmed (0xAF)",
                  lambda: tc16_dtc_set_by_fault(t, ecu, log))

    runner.run_tc("TC17", "DTC Lifecycle",
                  "ClearDTC at safe temp → DTC P0217 absent",
                  lambda: tc17_dtc_clear(t, ecu, log))

    # ── GROUP 8: Security Access ───────────────────────────────────────────────
    _banner("GROUP 8: Security Access (Seed / Key)")

    runner.run_tc("TC18", "Security Access",
                  "Full Seed/Key unlock in extendedDiagSession",
                  lambda: tc18_security_access_unlock(t, log))

    # ── GROUP 9: Report Generation ─────────────────────────────────────────────
    _banner("GROUP 9: Automated Report Generation")

    runner.run_tc("TC19", "Reports",
                  "JSON report: file created, valid JSON, all required fields",
                  lambda: tc19_json_report(runner, log))

    runner.run_tc("TC20", "Reports",
                  "HTML report: file created, contains TC01–TC18 results",
                  lambda: tc20_html_report(runner, log))

    # ── Final complete reports (all 20 TCs) ────────────────────────────────────
    json_path = runner.generate_json()
    html_path = runner.generate_html()

    runner.print_summary()

    passed = sum(1 for r in runner.results if r.status == "PASS")
    total  = len(runner.results)
    print(f"\n  📄 Log:   test_run_{runner.run_id}.log")
    print(f"  📊 JSON:  {json_path}")
    print(f"  🌐 HTML:  {html_path}")
    print(f"\n  {'🎉 ALL TESTS PASS' if passed == total else '⚠️  FAILURES DETECTED'}"
          f"  ({passed}/{total})\n")

    ecu.stop()
    t.shutdown()
    timing_bus.shutdown()


if __name__ == "__main__":
    main()
