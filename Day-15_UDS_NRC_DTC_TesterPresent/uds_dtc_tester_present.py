"""
Day 15: UDS — Complete NRC Reference, TesterPresent (0x3E),
         ReadDTCInformation (0x19), ClearDiagnosticInformation (0x14)
=====================================================================
Simulates an ECU with a DTC fault store, TesterPresent keepalive,
and comprehensive negative-response validation on a python-can
virtual bus.

No hardware needed.

Install:
    pip install python-can

Run:
    python uds_dtc_tester_present.py
"""

import can
import threading
import time
import struct

# ─── UDS CONSTANTS ───────────────────────────────────────────────────────────

CHANNEL      = "vcan0"
TESTER_TX_ID = 0x7E0
TESTER_RX_ID = 0x7E8

# Service IDs
SID_SESSION   = 0x10
SID_CLEAR_DTC = 0x14
SID_READ_DTC  = 0x19
SID_TESTER_P  = 0x3E
SID_NEG       = 0x7F

# Session types
SESSION_DEFAULT     = 0x01
SESSION_PROGRAMMING = 0x02
SESSION_EXTENDED    = 0x03

# 0x19 ReadDTCInformation sub-functions
RDI_NUM_BY_MASK = 0x01   # reportNumberOfDTCByStatusMask
RDI_BY_MASK     = 0x02   # reportDTCByStatusMask
RDI_SUPPORTED   = 0x0A   # reportSupportedDTC

# DTC status byte bit masks (ISO 14229-1 Table C.1)
DTC_TF     = 0x01   # bit 0: testFailed (currently failing)
DTC_TFTMC  = 0x02   # bit 1: testFailedThisMonitoringCycle
DTC_PDTC   = 0x04   # bit 2: pendingDTC
DTC_CDTC   = 0x08   # bit 3: confirmedDTC
DTC_TNCLSC = 0x10   # bit 4: testNotCompletedSinceLastClear
DTC_TFSLC  = 0x20   # bit 5: testFailedSinceLastClear
DTC_TNCTMC = 0x40   # bit 6: testNotCompletedThisMonitoringCycle
DTC_WIR    = 0x80   # bit 7: warningIndicatorRequested (MIL on)

# NRCs
NRC_SERVICE_NOT_SUPPORTED   = 0x11
NRC_SUBFUNC_NOT_SUPPORTED   = 0x12
NRC_INCORRECT_MSG_LENGTH    = 0x13
NRC_CONDITIONS_NOT_CORRECT  = 0x22
NRC_REQUEST_SEQUENCE_ERROR  = 0x24
NRC_REQUEST_OUT_OF_RANGE    = 0x31

# ECU-level constants
DTC_STATUS_AVAIL_MASK = 0xFF   # all 8 status bits supported
DTC_FORMAT_IDENTIFIER = 0x01   # ISO 14229-1 format


# ─── ISO-TP HELPERS ───────────────────────────────────────────────────────────

def build_single_frame(uds_bytes: list) -> bytes:
    assert 1 <= len(uds_bytes) <= 7
    pci    = len(uds_bytes)
    padded = [pci] + uds_bytes + [0x00] * (7 - len(uds_bytes))
    return bytes(padded)


def build_multi_frame_response(uds_bytes: list) -> list:
    """Build ISO-TP First Frame + Consecutive Frames."""
    total  = len(uds_bytes)
    frames = []
    ff     = [0x10 | ((total >> 8) & 0x0F), total & 0xFF] + uds_bytes[:6]
    frames.append(bytes(ff))
    sn, offset = 1, 6
    while offset < total:
        chunk = uds_bytes[offset: offset + 7]
        cf    = [0x20 | (sn & 0x0F)] + chunk + [0x00] * (7 - len(chunk))
        frames.append(bytes(cf))
        sn     = (sn + 1) & 0x0F
        offset += 7
    return frames


def parse_uds_from_frame(data: bytes):
    """Extract UDS payload from ISO-TP Single Frame. Returns None if not SF."""
    pci = data[0]
    if (pci & 0xF0) != 0x00:
        return None
    length = pci & 0x0F
    return list(data[1: 1 + length])


# ─── DTC RECORD ───────────────────────────────────────────────────────────────

class DTCRecord:
    """
    One Diagnostic Trouble Code with an ISO 14229 status byte.

    code   — 16-bit value: e.g. 0x0300 for P0300
    status — 8-bit bitmask using DTC_* constants above
    """

    def __init__(self, code: int, name: str, status: int):
        self.code       = code
        self.name       = name
        self._status    = status
        self._original  = status   # kept for reset after clear

    @property
    def status(self) -> int:
        return self._status

    @property
    def high_byte(self) -> int:
        return (self.code >> 8) & 0xFF

    @property
    def low_byte(self) -> int:
        return self.code & 0xFF

    def matches_mask(self, mask: int) -> bool:
        """Return True when at least one bit in mask is set in this DTC's status."""
        return (self._status & mask) != 0

    def clear(self) -> None:
        """Simulate what happens to the status byte after ClearDiagnosticInformation."""
        # Confirmed faults don't vanish instantly — they clear CDTC, TF, TFTMC
        # but TFSLC stays until the next monitoring cycle passes clean.
        # For simulation simplicity we zero the entire status.
        self._status = 0x00


# ─── SIMULATED ECU ────────────────────────────────────────────────────────────

class SimulatedECU(threading.Thread):
    """
    UDS ECU with DTC store.
    Handles: 0x10 (session), 0x14 (clear DTC), 0x19 (read DTC), 0x3E (tester present).
    """

    S3_TIMEOUT_S = 5.0

    def __init__(self):
        super().__init__(daemon=True, name="SimECU")
        self.bus          = can.Bus(interface="virtual", channel=CHANNEL)
        self.session      = SESSION_DEFAULT
        self._stop        = threading.Event()
        self._last_diag_t = time.monotonic()

        # ── DTC store — four representative fault codes ───────────────
        #
        #  P0300 (Random Misfire)         — active, confirmed, MIL on
        #    status 0xAF = WIR+TFSLC+CDTC+PDTC+TFTMC+TF
        #
        #  P0128 (Thermostat Stuck Open)  — pending only, not yet confirmed
        #    status 0x24 = TFSLC+PDTC
        #
        #  P0420 (Catalyst Efficiency)    — confirmed, stored, no longer active
        #    status 0x28 = TFSLC+CDTC
        #
        #  U0100 (Lost Comm with ECM)     — active, confirmed, MIL on
        #    status 0xAF = same as P0300
        # ─────────────────────────────────────────────────────────────
        self._dtcs = [
            DTCRecord(0x0300, "P0300 RandomMisfire",
                      DTC_TF | DTC_TFTMC | DTC_PDTC | DTC_CDTC | DTC_TFSLC | DTC_WIR),
            DTCRecord(0x0128, "P0128 ThermostatStuck",
                      DTC_PDTC | DTC_TFSLC),
            DTCRecord(0x0420, "P0420 CatalystEfficiency",
                      DTC_CDTC | DTC_TFSLC),
            DTCRecord(0xC100, "U0100 LostCommECM",
                      DTC_TF | DTC_TFTMC | DTC_PDTC | DTC_CDTC | DTC_TFSLC | DTC_WIR),
        ]

    # ── Public control ────────────────────────────────────────────────

    def stop(self) -> None:
        self._stop.set()
        self.bus.shutdown()

    # ── Response helpers ──────────────────────────────────────────────

    def _send_raw(self, payload: list) -> None:
        if len(payload) <= 7:
            self.bus.send(can.Message(arbitration_id=TESTER_RX_ID,
                                      data=build_single_frame(payload),
                                      is_extended_id=False))
        else:
            for fd in build_multi_frame_response(payload):
                self.bus.send(can.Message(arbitration_id=TESTER_RX_ID,
                                          data=fd, is_extended_id=False))
                time.sleep(0.001)

    def _neg(self, sid: int, nrc: int) -> None:
        self._send_raw([SID_NEG, sid, nrc])

    # ── Service: 0x10 DiagnosticSessionControl ────────────────────────

    def _handle_session(self, sub: int) -> None:
        valid = {SESSION_DEFAULT, SESSION_PROGRAMMING, SESSION_EXTENDED}
        if sub not in valid:
            self._neg(SID_SESSION, NRC_SUBFUNC_NOT_SUPPORTED); return
        if self.session == SESSION_DEFAULT and sub == SESSION_PROGRAMMING:
            self._neg(SID_SESSION, NRC_REQUEST_SEQUENCE_ERROR); return
        self.session = sub
        self._last_diag_t = time.monotonic()
        self._send_raw([SID_SESSION + 0x40, sub, 0x00, 0x19, 0x01, 0xF4])

    # ── Service: 0x3E TesterPresent ───────────────────────────────────

    def _handle_tester_present(self, uds: list) -> None:
        """
        Resets the S3 session-inactivity timer.
        sub=0x00: normal response [0x7E, 0x00]
        sub=0x80: suppressPosRspMsgIndicationBit set — timer resets, NO response sent.
        Any non-0x00 sub-function (after stripping bit 7) → NRC 0x12.
        """
        if len(uds) < 2:
            self._neg(SID_TESTER_P, NRC_INCORRECT_MSG_LENGTH); return

        sub      = uds[1]
        suppress = (sub & 0x80) != 0   # bit 7 = suppressPosRspMsgIndicationBit
        core_sub = sub & 0x7F          # actual sub-function value without suppress bit

        if core_sub != 0x00:
            self._neg(SID_TESTER_P, NRC_SUBFUNC_NOT_SUPPORTED); return

        self._last_diag_t = time.monotonic()   # ← the whole point: reset S3

        if not suppress:
            self._send_raw([SID_TESTER_P + 0x40, core_sub])
        # If suppress=True: ECU processes silently — no positive response sent
        # NRC is still sent if there's an error (but we already returned above)

    # ── Service: 0x19 ReadDTCInformation ─────────────────────────────

    def _handle_read_dtc(self, uds: list) -> None:
        if len(uds) < 2:
            self._neg(SID_READ_DTC, NRC_INCORRECT_MSG_LENGTH); return

        sub = uds[1]

        # ── 0x01 reportNumberOfDTCByStatusMask ───────────────────────
        if sub == RDI_NUM_BY_MASK:
            if len(uds) < 3:
                self._neg(SID_READ_DTC, NRC_INCORRECT_MSG_LENGTH); return
            mask  = uds[2]
            count = sum(1 for d in self._dtcs if d.matches_mask(mask))
            self._send_raw([
                SID_READ_DTC + 0x40, sub,
                DTC_STATUS_AVAIL_MASK,
                DTC_FORMAT_IDENTIFIER,
                (count >> 8) & 0xFF,
                count & 0xFF,
            ])

        # ── 0x02 reportDTCByStatusMask ───────────────────────────────
        elif sub == RDI_BY_MASK:
            if len(uds) < 3:
                self._neg(SID_READ_DTC, NRC_INCORRECT_MSG_LENGTH); return
            mask    = uds[2]
            matched = [d for d in self._dtcs if d.matches_mask(mask)]
            payload = [SID_READ_DTC + 0x40, sub, DTC_STATUS_AVAIL_MASK]
            for d in matched:
                payload += [d.high_byte, d.low_byte, d.status]
            self._send_raw(payload)

        # ── 0x0A reportSupportedDTC ──────────────────────────────────
        elif sub == RDI_SUPPORTED:
            payload = [SID_READ_DTC + 0x40, sub, DTC_STATUS_AVAIL_MASK]
            for d in self._dtcs:
                payload += [d.high_byte, d.low_byte, d.status]
            self._send_raw(payload)

        else:
            self._neg(SID_READ_DTC, NRC_SUBFUNC_NOT_SUPPORTED)

    # ── Service: 0x14 ClearDiagnosticInformation ─────────────────────

    def _handle_clear_dtc(self, uds: list) -> None:
        """
        [0x14, groupOfDTC_H, groupOfDTC_M, groupOfDTC_L]
        0xFFFFFF = clear all; specific 16-bit DTC code in low 2 bytes otherwise.
        Requires extendedDiagnosticSession.
        Response: [0x54]  — just one byte, no sub-function echo.
        """
        if len(uds) != 4:   # SID + exactly 3 group bytes
            self._neg(SID_CLEAR_DTC, NRC_INCORRECT_MSG_LENGTH); return

        if self.session == SESSION_DEFAULT:
            self._neg(SID_CLEAR_DTC, NRC_CONDITIONS_NOT_CORRECT); return

        group    = (uds[1] << 16) | (uds[2] << 8) | uds[3]

        if group == 0xFFFFFF:
            for d in self._dtcs:
                d.clear()
        else:
            dtc_code = group & 0xFFFF
            dtc = next((d for d in self._dtcs if d.code == dtc_code), None)
            if dtc is None:
                self._neg(SID_CLEAR_DTC, NRC_REQUEST_OUT_OF_RANGE); return
            dtc.clear()

        self._send_raw([SID_CLEAR_DTC + 0x40])

    # ── Main loop ─────────────────────────────────────────────────────

    def run(self) -> None:
        while not self._stop.is_set():

            # S3 session-inactivity watchdog
            if (self.session != SESSION_DEFAULT
                    and time.monotonic() - self._last_diag_t > self.S3_TIMEOUT_S):
                self.session = SESSION_DEFAULT

            frame = self.bus.recv(timeout=0.05)
            if frame is None or frame.arbitration_id != TESTER_TX_ID:
                continue

            self._last_diag_t = time.monotonic()
            uds = parse_uds_from_frame(bytes(frame.data))
            if uds is None or len(uds) < 1:
                continue

            sid = uds[0]
            if   sid == SID_SESSION and len(uds) >= 2: self._handle_session(uds[1])
            elif sid == SID_TESTER_P:                  self._handle_tester_present(uds)
            elif sid == SID_READ_DTC:                  self._handle_read_dtc(uds)
            elif sid == SID_CLEAR_DTC:                 self._handle_clear_dtc(uds)
            else:                                      self._neg(sid, NRC_SERVICE_NOT_SUPPORTED)


# ─── TESTER CLIENT ────────────────────────────────────────────────────────────

class UDSTester:
    """UDS tester with TesterPresent, ReadDTC, ClearDTC, and NRC test cases."""

    RESPONSE_TIMEOUT_S = 2.0

    def __init__(self):
        self.bus    = can.Bus(interface="virtual", channel=CHANNEL)
        self.passed = []
        self.failed = []

    def shutdown(self) -> None:
        self.bus.shutdown()

    # ── Transport ─────────────────────────────────────────────────────

    def _send(self, uds_bytes: list) -> None:
        self.bus.send(can.Message(
            arbitration_id=TESTER_TX_ID,
            data=build_single_frame(uds_bytes),
            is_extended_id=False
        ))

    def _recv(self, timeout: float = None):
        """
        Collect a UDS response, reassembling multi-frame automatically.
        Handles 0x78 RCRRP (response pending) by extending the deadline.
        """
        deadline          = time.monotonic() + (timeout or self.RESPONSE_TIMEOUT_S)
        collected_payload = []
        total_expected    = 0

        while time.monotonic() < deadline:
            frame = self.bus.recv(timeout=max(0.01, deadline - time.monotonic()))
            if frame is None or frame.arbitration_id != TESTER_RX_ID:
                continue

            first_byte = frame.data[0]
            pci_type   = (first_byte & 0xF0) >> 4

            if pci_type == 0x0:
                # Single Frame
                length = first_byte & 0x0F
                uds    = list(frame.data[1: 1 + length])
                if len(uds) == 3 and uds[0] == SID_NEG and uds[2] == 0x78:
                    print("    ⏳ RCRRP 0x78 — extending wait...")
                    deadline += 5.0
                    continue
                return uds

            elif pci_type == 0x1:
                # First Frame — extract total UDS payload length
                total_expected    = ((first_byte & 0x0F) << 8) | frame.data[1]
                collected_payload = list(frame.data[2:])   # first 6 payload bytes
                # Send Flow Control (Continue To Send, block size=0, separation=0)
                self.bus.send(can.Message(
                    arbitration_id=TESTER_TX_ID,
                    data=bytes([0x30, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]),
                    is_extended_id=False
                ))

            elif pci_type == 0x2:
                # Consecutive Frame
                collected_payload += list(frame.data[1:])
                if len(collected_payload) >= total_expected:
                    return collected_payload[:total_expected]

        return collected_payload if collected_payload else None

    # ── Assertion helpers ──────────────────────────────────────────────

    def _pass(self, name: str, detail: str = "") -> None:
        tag = f"  ✅ PASS  {name}" + (f"  [{detail}]" if detail else "")
        print(tag)
        self.passed.append(tag)

    def _fail(self, name: str, detail: str = "") -> None:
        tag = f"  ❌ FAIL  {name}" + (f"  [{detail}]" if detail else "")
        print(tag)
        self.failed.append(tag)

    def _assert_positive(self, name: str, resp, expected_sid: int) -> bool:
        if resp is None:
            self._fail(name, "no response (timeout)"); return False
        if resp[0] == SID_NEG:
            nrc = resp[2] if len(resp) >= 3 else "?"
            self._fail(name, f"NegResp NRC=0x{nrc:02X}"); return False
        if resp[0] != expected_sid + 0x40:
            self._fail(name, f"wrong SID 0x{resp[0]:02X}"); return False
        self._pass(name, f"SID=0x{resp[0]:02X}"); return True

    def _assert_negative(self, name: str, resp, expected_nrc: int) -> bool:
        if resp is None:
            self._fail(name, "no response (timeout)"); return False
        if resp[0] != SID_NEG:
            self._fail(name, f"expected NegResp got 0x{resp[0]:02X}"); return False
        actual = resp[2] if len(resp) >= 3 else 0
        if actual != expected_nrc:
            self._fail(name, f"expected 0x{expected_nrc:02X} got 0x{actual:02X}"); return False
        self._pass(name, f"NRC=0x{actual:02X}"); return True

    def _switch_session(self, session_type: int) -> None:
        self._send([SID_SESSION, session_type])
        self._recv()

    # ── Helper: parse DTC list from 0x59 0x02/0x0A response ─────────

    @staticmethod
    def _parse_dtc_list(resp: list) -> list:
        """
        Parse the DTC records from a 0x59 0x02 or 0x59 0x0A response.
        Returns list of (code: int, status: int) tuples.
        """
        # resp[0]=0x59, resp[1]=sub, resp[2]=availMask, then 3-byte DTC records
        if resp is None or len(resp) < 3:
            return []
        dtcs = []
        i    = 3
        while i + 2 < len(resp):
            code   = (resp[i] << 8) | resp[i + 1]
            status = resp[i + 2]
            dtcs.append((code, status))
            i += 3
        return dtcs

    @staticmethod
    def _parse_dtc_count(resp: list) -> int:
        """Parse the count from a 0x59 0x01 response."""
        if resp is None or len(resp) < 6:
            return -1
        return (resp[4] << 8) | resp[5]

    # ─────────────────────────────────────────────────────────────────
    # TEST CASES
    # ─────────────────────────────────────────────────────────────────

    # GROUP 1: TesterPresent (0x3E) ───────────────────────────────────

    def tc01_tester_present_extended(self) -> None:
        """TC01: TesterPresent sub=0x00 in extended session → [0x7E, 0x00]."""
        self._switch_session(SESSION_EXTENDED)
        self._send([SID_TESTER_P, 0x00])
        resp = self._recv()
        if not self._assert_positive("TC01 TesterPresent extended session",
                                      resp, SID_TESTER_P): return
        if len(resp) >= 2 and resp[1] == 0x00:
            self._pass("TC01 sub-function echoed as 0x00", "✓")
        else:
            self._fail("TC01 sub-function echo", f"got 0x{resp[1]:02X}" if resp else "?")

    def tc02_tester_present_default(self) -> None:
        """TC02: TesterPresent is allowed from ANY session, including default."""
        self._switch_session(SESSION_DEFAULT)
        self._send([SID_TESTER_P, 0x00])
        resp = self._recv()
        self._assert_positive("TC02 TesterPresent in default session (allowed everywhere)",
                               resp, SID_TESTER_P)

    def tc03_tester_present_suppress(self) -> None:
        """TC03: Sub=0x80 (suppressPosRspMsgIndicationBit) — ECU resets timer but sends NO response."""
        self._switch_session(SESSION_EXTENDED)
        self._send([SID_TESTER_P, 0x80])
        # Expect timeout — no positive response should arrive
        resp = self._recv(timeout=0.5)
        if resp is None:
            self._pass("TC03 TesterPresent suppress → no response (correct)", "✓")
        elif resp[0] == SID_NEG:
            nrc = resp[2] if len(resp) >= 3 else "?"
            self._fail("TC03 suppress → got NegResp", f"NRC=0x{nrc:02X}")
        else:
            self._fail("TC03 suppress → unexpected positive response", f"0x{resp[0]:02X}")

    def tc04_tester_present_unknown_subfunc(self) -> None:
        """TC04: Unknown sub-function for TesterPresent → NRC 0x12."""
        self._switch_session(SESSION_EXTENDED)
        self._send([SID_TESTER_P, 0x01])   # 0x01 is not a valid sub-function
        resp = self._recv()
        self._assert_negative("TC04 TesterPresent unknown sub → NRC 0x12",
                              resp, NRC_SUBFUNC_NOT_SUPPORTED)

    # GROUP 2: ReadDTCInformation (0x19) — Count by Mask ─────────────

    def tc05_dtc_count_all(self) -> None:
        """TC05: reportNumberOfDTCByStatusMask 0xFF → 4 DTCs."""
        self._switch_session(SESSION_DEFAULT)
        self._send([SID_READ_DTC, RDI_NUM_BY_MASK, 0xFF])
        resp = self._recv()
        if not self._assert_positive("TC05 0x19 0x01 mask=0xFF",
                                      resp, SID_READ_DTC): return
        count = self._parse_dtc_count(resp)
        if count == 4:
            self._pass("TC05 DTC count = 4", "✓")
        else:
            self._fail("TC05 DTC count", f"expected 4 got {count}")

    def tc06_dtc_count_confirmed(self) -> None:
        """TC06: mask=0x08 (confirmedDTC) → 3 DTCs (P0300, P0420, U0100)."""
        self._send([SID_READ_DTC, RDI_NUM_BY_MASK, DTC_CDTC])
        resp = self._recv()
        if not self._assert_positive("TC06 0x19 0x01 mask=0x08 (confirmed)",
                                      resp, SID_READ_DTC): return
        count = self._parse_dtc_count(resp)
        if count == 3:
            self._pass("TC06 Confirmed DTC count = 3", "✓")
        else:
            self._fail("TC06 Confirmed DTC count", f"expected 3 got {count}")

    def tc07_dtc_count_currently_failing(self) -> None:
        """TC07: mask=0x01 (testFailed / currently active) → 2 DTCs."""
        self._send([SID_READ_DTC, RDI_NUM_BY_MASK, DTC_TF])
        resp = self._recv()
        if not self._assert_positive("TC07 0x19 0x01 mask=0x01 (active)",
                                      resp, SID_READ_DTC): return
        count = self._parse_dtc_count(resp)
        if count == 2:
            self._pass("TC07 Active DTC count = 2 (P0300, U0100)", "✓")
        else:
            self._fail("TC07 Active DTC count", f"expected 2 got {count}")

    # GROUP 3: ReadDTCInformation (0x19) — By Mask ───────────────────

    def tc08_read_all_dtcs(self) -> None:
        """TC08: reportDTCByStatusMask 0xFF → all 4 DTCs with correct status bytes."""
        self._send([SID_READ_DTC, RDI_BY_MASK, 0xFF])
        resp = self._recv()
        if not self._assert_positive("TC08 0x19 0x02 mask=0xFF",
                                      resp, SID_READ_DTC): return
        dtcs = self._parse_dtc_list(resp)
        if len(dtcs) == 4:
            self._pass("TC08 All 4 DTCs returned", "✓")
        else:
            self._fail("TC08 DTC count", f"expected 4 got {len(dtcs)}")
        # Verify P0300 status
        p0300 = next((d for d in dtcs if d[0] == 0x0300), None)
        if p0300:
            expected = DTC_TF|DTC_TFTMC|DTC_PDTC|DTC_CDTC|DTC_TFSLC|DTC_WIR
            if p0300[1] == expected:
                self._pass("TC08 P0300 status byte correct", f"0x{p0300[1]:02X} ✓")
            else:
                self._fail("TC08 P0300 status byte",
                           f"expected 0x{expected:02X} got 0x{p0300[1]:02X}")
        else:
            self._fail("TC08 P0300 not in response")

    def tc09_read_active_dtcs(self) -> None:
        """TC09: mask=0x01 (testFailed) → P0300 and U0100 only."""
        self._send([SID_READ_DTC, RDI_BY_MASK, DTC_TF])
        resp = self._recv()
        if not self._assert_positive("TC09 0x19 0x02 mask=0x01 (active)",
                                      resp, SID_READ_DTC): return
        dtcs  = self._parse_dtc_list(resp)
        codes = {d[0] for d in dtcs}
        if codes == {0x0300, 0xC100}:
            self._pass("TC09 Active DTCs = {P0300, U0100}", "✓")
        else:
            names = [f"0x{c:04X}" for c in sorted(codes)]
            self._fail("TC09 Active DTCs", f"got {names}")

    def tc10_read_mil_on_dtcs(self) -> None:
        """TC10: mask=0x80 (warningIndicatorRequested / MIL on) → P0300 and U0100."""
        self._send([SID_READ_DTC, RDI_BY_MASK, DTC_WIR])
        resp = self._recv()
        if not self._assert_positive("TC10 0x19 0x02 mask=0x80 (MIL on)",
                                      resp, SID_READ_DTC): return
        dtcs  = self._parse_dtc_list(resp)
        codes = {d[0] for d in dtcs}
        if codes == {0x0300, 0xC100}:
            self._pass("TC10 MIL-on DTCs = {P0300, U0100}", "✓")
        else:
            self._fail("TC10 MIL-on DTCs", f"got {[f'0x{c:04X}' for c in sorted(codes)]}")

    def tc11_read_pending_dtcs(self) -> None:
        """TC11: mask=0x04 (pendingDTC) → P0300, P0128, U0100 (3 DTCs)."""
        self._send([SID_READ_DTC, RDI_BY_MASK, DTC_PDTC])
        resp = self._recv()
        if not self._assert_positive("TC11 0x19 0x02 mask=0x04 (pending)",
                                      resp, SID_READ_DTC): return
        dtcs = self._parse_dtc_list(resp)
        if len(dtcs) == 3:
            self._pass("TC11 Pending DTC count = 3 (P0300, P0128, U0100)", "✓")
        else:
            self._fail("TC11 Pending DTC count", f"expected 3 got {len(dtcs)}")

    def tc12_read_supported_dtcs(self) -> None:
        """TC12: reportSupportedDTC (0x0A) → all 4 DTCs regardless of status."""
        self._send([SID_READ_DTC, RDI_SUPPORTED])
        resp = self._recv()
        if not self._assert_positive("TC12 0x19 0x0A reportSupportedDTC",
                                      resp, SID_READ_DTC): return
        dtcs = self._parse_dtc_list(resp)
        if len(dtcs) == 4:
            self._pass("TC12 Supported DTC count = 4", "✓")
        else:
            self._fail("TC12 Supported DTC count", f"expected 4 got {len(dtcs)}")

    def tc13_read_dtc_unknown_subfunc(self) -> None:
        """TC13: Unknown 0x19 sub-function → NRC 0x12."""
        self._send([SID_READ_DTC, 0x99])
        resp = self._recv()
        self._assert_negative("TC13 0x19 unknown sub → NRC 0x12",
                              resp, NRC_SUBFUNC_NOT_SUPPORTED)

    # GROUP 4: ClearDiagnosticInformation (0x14) ──────────────────────

    def tc14_clear_in_default_session(self) -> None:
        """TC14: ClearDiagnosticInformation in default session → NRC 0x22."""
        self._switch_session(SESSION_DEFAULT)
        self._send([SID_CLEAR_DTC, 0xFF, 0xFF, 0xFF])
        resp = self._recv()
        self._assert_negative("TC14 Clear DTCs in default → NRC 0x22",
                              resp, NRC_CONDITIONS_NOT_CORRECT)

    def tc15_clear_all_dtcs(self) -> None:
        """TC15: Clear all DTCs in extended session → [0x54] (1-byte positive)."""
        self._switch_session(SESSION_EXTENDED)
        self._send([SID_CLEAR_DTC, 0xFF, 0xFF, 0xFF])
        resp = self._recv()
        if resp is None:
            self._fail("TC15 Clear all DTCs", "no response"); return
        if resp[0] == SID_CLEAR_DTC + 0x40:   # 0x54
            self._pass("TC15 Clear all DTCs → 0x54", "✓")
        else:
            nrc = resp[2] if resp[0] == SID_NEG and len(resp) >= 3 else "?"
            self._fail("TC15 Clear all DTCs", f"NRC=0x{nrc}" if resp[0] == SID_NEG
                       else f"got 0x{resp[0]:02X}")

    def tc16_verify_dtcs_cleared(self) -> None:
        """TC16: After TC15 clear, count of DTCs must be 0."""
        # Still in extended session from TC15
        self._send([SID_READ_DTC, RDI_NUM_BY_MASK, 0xFF])
        resp = self._recv()
        if not self._assert_positive("TC16 Read DTC count after clear",
                                      resp, SID_READ_DTC): return
        count = self._parse_dtc_count(resp)
        if count == 0:
            self._pass("TC16 DTC count = 0 after clear", "✓")
        else:
            self._fail("TC16 DTC count after clear", f"expected 0 got {count}")

    def tc17_clear_wrong_length(self) -> None:
        """TC17: 0x14 with wrong payload length → NRC 0x13."""
        self._switch_session(SESSION_EXTENDED)
        # Send only 2 bytes after SID instead of 3
        self._send([SID_CLEAR_DTC, 0xFF, 0xFF])
        resp = self._recv()
        self._assert_negative("TC17 ClearDTC wrong length → NRC 0x13",
                              resp, NRC_INCORRECT_MSG_LENGTH)

    # GROUP 5: Comprehensive NRC Validation ───────────────────────────

    def tc18_malformed_read_dtc_no_subfunc(self) -> None:
        """TC18: 0x19 with no sub-function byte → NRC 0x13 incorrectMessageLength."""
        self._send([SID_READ_DTC])
        resp = self._recv()
        self._assert_negative("TC18 0x19 missing sub-function → NRC 0x13",
                              resp, NRC_INCORRECT_MSG_LENGTH)

    def tc19_unknown_service_nrc11(self) -> None:
        """TC19: Completely unknown SID → NRC 0x11 serviceNotSupported."""
        self._send([0x99, 0x00])
        resp = self._recv()
        if resp is None:
            self._fail("TC19 Unknown SID", "no response"); return
        if resp[0] == SID_NEG and len(resp) >= 3:
            if resp[2] == NRC_SERVICE_NOT_SUPPORTED:
                self._pass("TC19 Unknown SID → NRC 0x11", f"NRC=0x{resp[2]:02X} ✓")
            else:
                self._fail("TC19 Unknown SID NRC", f"expected 0x11 got 0x{resp[2]:02X}")
        else:
            self._fail("TC19 Unknown SID", f"expected NegResp got 0x{resp[0]:02X}")

    def tc20_verify_nrc_format(self) -> None:
        """TC20: NRC response is exactly [0x7F, original_SID, NRC_byte] — 3 bytes."""
        self._send([SID_READ_DTC, 0xAA])   # unknown sub → NRC 0x12
        resp = self._recv()
        if resp is None:
            self._fail("TC20 NRC format", "no response"); return
        if resp[0] == 0x7F and len(resp) == 3:
            if resp[1] == SID_READ_DTC:
                self._pass("TC20 NRC format: [0x7F, SID_echoed, NRC] ✓",
                           f"[0x{resp[0]:02X}, 0x{resp[1]:02X}, 0x{resp[2]:02X}]")
            else:
                self._fail("TC20 NRC SID echo",
                           f"expected 0x{SID_READ_DTC:02X} got 0x{resp[1]:02X}")
        else:
            self._fail("TC20 NRC format",
                       f"expected 3-byte NegResp, got len={len(resp)} resp={resp}")

    # ── Summary ───────────────────────────────────────────────────────

    def print_summary(self) -> None:
        total = len(self.passed) + len(self.failed)
        print(f"\n{'='*64}")
        print(f"  TEST SUMMARY: {len(self.passed)}/{total} passed, "
              f"{len(self.failed)} failed")
        print(f"{'='*64}")
        if self.failed:
            print("\n  Failed:")
            for f in self.failed:
                print(f"    {f.strip()}")


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def banner(title: str) -> None:
    print(f"\n{'─'*64}\n  {title}\n{'─'*64}")


def main() -> None:
    print("\n" + "🩺🔔  " * 10)
    print("  Day 15 — UDS NRC Reference + TesterPresent (0x3E) +")
    print("           ReadDTCInformation (0x19) + ClearDTCs (0x14)")
    print("🩺🔔  " * 10)

    ecu    = SimulatedECU()
    tester = UDSTester()
    ecu.start()
    time.sleep(0.1)

    banner("GROUP 1: TesterPresent (0x3E)")
    tester.tc01_tester_present_extended()
    tester.tc02_tester_present_default()
    tester.tc03_tester_present_suppress()
    tester.tc04_tester_present_unknown_subfunc()

    banner("GROUP 2: ReadDTCInformation (0x19) — Count by Mask")
    tester.tc05_dtc_count_all()
    tester.tc06_dtc_count_confirmed()
    tester.tc07_dtc_count_currently_failing()

    banner("GROUP 3: ReadDTCInformation (0x19) — DTCs by Mask")
    tester.tc08_read_all_dtcs()
    tester.tc09_read_active_dtcs()
    tester.tc10_read_mil_on_dtcs()
    tester.tc11_read_pending_dtcs()
    tester.tc12_read_supported_dtcs()
    tester.tc13_read_dtc_unknown_subfunc()

    banner("GROUP 4: ClearDiagnosticInformation (0x14)")
    tester.tc14_clear_in_default_session()
    tester.tc15_clear_all_dtcs()
    tester.tc16_verify_dtcs_cleared()
    tester.tc17_clear_wrong_length()

    banner("GROUP 5: Comprehensive NRC Validation")
    tester.tc18_malformed_read_dtc_no_subfunc()
    tester.tc19_unknown_service_nrc11()
    tester.tc20_verify_nrc_format()

    tester.print_summary()
    ecu.stop()
    tester.shutdown()


if __name__ == "__main__":
    main()
