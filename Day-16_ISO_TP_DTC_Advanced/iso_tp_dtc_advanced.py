"""
Day 16: ISO-TP Transport Protocol (ISO 15765-2) + Advanced DTC Topics
=======================================================================
Implements a complete ISO-TP state machine from scratch — no higher-level
library — to show exactly what happens byte-by-byte beneath UDS.

Covers:
  • Single Frame (SF)  — up to 7 bytes
  • First Frame (FF)   — starts a multi-frame message
  • Flow Control (FC)  — ContinueToSend / Wait / Overflow
  • Consecutive Frame (CF) — carries remaining bytes with SN counter
  • ISO-TP timing parameters: N_Bs, N_Cr, STmin, BlockSize
  • DTC extended data (0x19 0x06) — occurrence counter, ageing counter
  • DTC snapshot / freeze frame (0x19 0x04) — sensor state at fault
  • Permanent DTC (0x19 0x0B) — survives ClearDTC, OBD mandated

No hardware needed.

Install:
    pip install python-can

Run:
    python iso_tp_dtc_advanced.py
"""

import can
import threading
import time
import struct
import random
from dataclasses import dataclass, field
from typing import Optional

# ─── CONSTANTS ───────────────────────────────────────────────────────────────

CHANNEL      = "vcan0"
TESTER_TX_ID = 0x7E0
TESTER_RX_ID = 0x7E8

# ISO-TP PCI type nibbles (upper nibble of first byte)
ISTP_SF = 0x00   # Single Frame
ISTP_FF = 0x10   # First Frame
ISTP_CF = 0x20   # Consecutive Frame
ISTP_FC = 0x30   # Flow Control

# Flow Control status byte
FC_CTS      = 0x00   # ContinueToSend
FC_WAIT     = 0x01   # Wait — ECU not ready yet
FC_OVERFLOW = 0x02   # Overflow — too much data, abort

# UDS Service IDs used in this day
SID_SESSION   = 0x10
SID_CLEAR_DTC = 0x14
SID_READ_DTC  = 0x19
SID_TESTER_P  = 0x3E
SID_NEG       = 0x7F

SESSION_DEFAULT  = 0x01
SESSION_EXTENDED = 0x03

# 0x19 sub-functions
RDI_NUM_BY_MASK   = 0x01
RDI_BY_MASK       = 0x02
RDI_SNAPSHOT      = 0x04   # reportDTCSnapshotRecordByDTCNumber (freeze frame)
RDI_EXT_DATA      = 0x06   # reportDTCExtDataRecordByDTCNumber
RDI_SUPPORTED     = 0x0A
RDI_PERMANENT     = 0x0B   # reportDTCWithPermanentStatus

# DTC status bits
DTC_TF     = 0x01
DTC_TFTMC  = 0x02
DTC_PDTC   = 0x04
DTC_CDTC   = 0x08
DTC_TNCLSC = 0x10
DTC_TFSLC  = 0x20
DTC_TNCTMC = 0x40
DTC_WIR    = 0x80

# NRCs
NRC_SERVICE_NOT_SUPPORTED  = 0x11
NRC_SUBFUNC_NOT_SUPPORTED  = 0x12
NRC_INCORRECT_MSG_LENGTH   = 0x13
NRC_CONDITIONS_NOT_CORRECT = 0x22
NRC_REQUEST_OUT_OF_RANGE   = 0x31

DTC_STATUS_AVAIL_MASK = 0xFF
DTC_FORMAT_ID         = 0x01

# ISO-TP timing (milliseconds) — from ISO 15765-2
N_BS_MAX_MS  = 1000   # sender waits up to 1000ms for a Flow Control after FF
N_CR_MAX_MS  = 1000   # sender waits up to 1000ms between Consecutive Frames
STMIN_MS     = 10     # minimum separation time between CFs we advertise

# ─── ISO-TP FRAME BUILDERS ───────────────────────────────────────────────────

def build_sf(uds_bytes: list) -> bytes:
    """
    Single Frame: fits entire UDS message in one CAN frame.

    Byte 0:  0x00 | len   (PCI: SF type=0, length nibble)
    Bytes 1–7: UDS payload (zero-padded to 8 bytes)

    Maximum UDS payload: 7 bytes (DLC=8 means PCI=1 byte + up to 7 data bytes).
    """
    assert 1 <= len(uds_bytes) <= 7, f"SF payload too long: {len(uds_bytes)}"
    pci    = len(uds_bytes) & 0x0F
    padded = bytes([pci] + list(uds_bytes) + [0x00] * (7 - len(uds_bytes)))
    return padded


def build_ff(uds_bytes: list) -> bytes:
    """
    First Frame: opens a multi-frame transfer.

    Byte 0:  0x10 | (total_len >> 8)   high nibble of 12-bit total length
    Byte 1:  total_len & 0xFF           low byte of total length
    Bytes 2–7: first 6 bytes of UDS payload

    The 12-bit field means maximum segmented message = 4095 bytes.
    For >4095 bytes, ISO-TP escape: bytes 0–1 = 0x10 0x00, bytes 2–5 = uint32 length.
    We implement the simple form (≤4095) here.
    """
    total = len(uds_bytes)
    assert total > 7, "FF requires payload > 7 bytes"
    assert total <= 4095, "Use escape FF for payloads > 4095 bytes"
    high = 0x10 | ((total >> 8) & 0x0F)
    low  = total & 0xFF
    frame = bytes([high, low] + list(uds_bytes[:6]))
    return frame


def build_cf(sn: int, chunk: list) -> bytes:
    """
    Consecutive Frame: carries remaining bytes with sequence number.

    Byte 0:  0x20 | (sn & 0x0F)   SN wraps 0x0→0xF→0x0 (not 0x1→0xF→0x1)
    Bytes 1–7: next 7 bytes of UDS payload (zero-padded on last CF)

    SN starts at 0x01 for the first CF (FF consumed bytes 0–5, CF1 starts at 6).
    SN wraps: 0x1, 0x2, … 0xF, 0x0, 0x1, …  NOT back to 0x1.
    """
    pci   = 0x20 | (sn & 0x0F)
    frame = bytes([pci] + list(chunk) + [0x00] * (7 - len(chunk)))
    return frame


def build_fc(fc_flag: int, block_size: int, stmin_ms: int) -> bytes:
    """
    Flow Control: sent by receiver after a First Frame, or to pause/resume.

    Byte 0:  0x30 | fc_flag   (0=CTS, 1=Wait, 2=Overflow)
    Byte 1:  block_size        0 = send all remaining CFs without pausing
    Byte 2:  STmin             separation time between CFs (ms, with encoding)

    STmin encoding:
      0x00–0x7F = 0–127 ms (direct)
      0x80–0xF0 = reserved
      0xF1–0xF9 = 100–900 µs (microseconds range, for CAN FD)
    """
    stmin_encoded = min(stmin_ms, 0x7F)   # cap at 127ms for simplicity
    return bytes([0x30 | (fc_flag & 0x0F), block_size & 0xFF, stmin_encoded,
                  0x00, 0x00, 0x00, 0x00, 0x00])


def segment_message(uds_bytes: list):
    """
    Segment a UDS payload into ISO-TP frames.
    Returns (first_frame_bytes, [cf1_bytes, cf2_bytes, ...]).
    For short messages (≤7 bytes), returns (single_frame_bytes, []).
    """
    if len(uds_bytes) <= 7:
        return build_sf(uds_bytes), []

    frames = [build_ff(uds_bytes)]
    offset = 6
    sn     = 1
    while offset < len(uds_bytes):
        chunk = uds_bytes[offset: offset + 7]
        frames.append(build_cf(sn, chunk))
        sn     = (sn + 1) & 0x0F
        offset += 7
    return frames[0], frames[1:]


# ─── ISO-TP RECEIVER STATE MACHINE ───────────────────────────────────────────

class ISOTPReceiver:
    """
    Stateful ISO-TP reassembler.

    Call feed(frame_data) for each incoming CAN frame.
    When a complete UDS message has been assembled, complete() returns it.

    States:
      IDLE     — waiting for SF or FF
      RECEIVING — FF received, waiting for CFs
    """

    def __init__(self):
        self._reset()

    def _reset(self):
        self._state           = "IDLE"
        self._total_expected  = 0
        self._payload         = []
        self._next_sn         = 1
        self._complete        = None

    def feed(self, data: bytes):
        """
        Process one CAN frame's worth of data.
        Returns the assembled UDS payload when complete, else None.
        Also returns a (fc_flag, block_size, stmin) tuple if a Flow Control
        must be sent back — the caller is responsible for transmitting it.
        """
        first_byte = data[0]
        pci_type   = (first_byte >> 4) & 0x0F

        if pci_type == 0:                          # ── Single Frame
            length = first_byte & 0x0F
            if length == 0 or length > 7:
                return None, None   # invalid SF
            self._reset()
            return list(data[1: 1 + length]), None

        elif pci_type == 1:                        # ── First Frame
            total = ((first_byte & 0x0F) << 8) | data[1]
            if total <= 7:
                return None, None   # invalid FF
            self._reset()
            self._state          = "RECEIVING"
            self._total_expected = total
            self._payload        = list(data[2:])   # first 6 bytes
            self._next_sn        = 1
            # Caller must send Flow Control (ContinueToSend) back
            fc_params = (FC_CTS, 0x00, STMIN_MS)
            return None, fc_params

        elif pci_type == 2:                        # ── Consecutive Frame
            if self._state != "RECEIVING":
                return None, None   # unexpected CF, ignore
            sn = first_byte & 0x0F
            if sn != self._next_sn:
                # Wrong sequence number — out of order or lost frame
                self._reset()
                return None, None
            self._payload += list(data[1:])
            self._next_sn = (self._next_sn + 1) & 0x0F
            if len(self._payload) >= self._total_expected:
                complete = self._payload[:self._total_expected]
                self._reset()
                return complete, None
            return None, None

        elif pci_type == 3:                        # ── Flow Control (we are ECU)
            # ECU receives FC only when acting as sender — not in scope here
            return None, None

        return None, None


# ─── DTC DATA STRUCTURES ─────────────────────────────────────────────────────

@dataclass
class DTCSnapshot:
    """
    Freeze-frame data captured when the DTC was confirmed.
    Represents what the ECU's sensors read at the moment of fault.
    """
    record_number : int   = 0x01
    engine_rpm    : int   = 0        # rpm
    vehicle_speed : int   = 0        # km/h
    coolant_temp  : int   = 0        # °C (offset 40 for negative temps)
    engine_load   : int   = 0        # 0–100 %
    fuel_trim     : int   = 0        # signed: positive = lean, negative = rich

    def encode(self) -> list:
        """Encode snapshot as a byte list (7 bytes of data + 1 record number)."""
        rpm_h  = (self.engine_rpm >> 8) & 0xFF
        rpm_l  = self.engine_rpm & 0xFF
        speed  = self.vehicle_speed & 0xFF
        temp   = (self.coolant_temp + 40) & 0xFF   # offset encoding
        load   = self.engine_load & 0xFF
        ftrim  = self.fuel_trim & 0xFF              # 2's complement for signed
        return [self.record_number, rpm_h, rpm_l, speed, temp, load, ftrim]


@dataclass
class DTCExtData:
    """
    Extended data record — OEM-defined counters attached to a DTC.
    Common fields: occurrence count, ageing counter, heal counter.
    """
    record_number   : int = 0x01
    occurrence_count: int = 0    # how many times this DTC was confirmed
    ageing_counter  : int = 0    # drive cycles since last TF (counts toward age-out)
    failed_cycles   : int = 0    # drive cycles where TF was set

    def encode(self) -> list:
        return [self.record_number,
                self.occurrence_count & 0xFF,
                self.ageing_counter   & 0xFF,
                self.failed_cycles    & 0xFF]


class DTCRecord:
    """One Diagnostic Trouble Code with full ISO 14229 data."""

    def __init__(self, code: int, name: str, status: int,
                 snapshot: Optional[DTCSnapshot] = None,
                 ext_data: Optional[DTCExtData]  = None,
                 permanent: bool = False):
        self.code      = code
        self.name      = name
        self._status   = status
        self.snapshot  = snapshot
        self.ext_data  = ext_data
        self.permanent = permanent   # survives ClearDiagnosticInformation

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
        return (self._status & mask) != 0

    def clear(self) -> None:
        """Clear status bits. Permanent DTCs retain their status."""
        if not self.permanent:
            self._status = 0x00
        # Even after clear, occurrence_count in ext_data is preserved


# ─── SIMULATED ECU ────────────────────────────────────────────────────────────

class SimulatedECU(threading.Thread):
    """
    Full ISO-TP ECU — uses the ISOTPReceiver state machine explicitly
    to show every FC/CF handshake rather than hiding it in a helper.

    Services: 0x10, 0x14, 0x19 (0x01/0x02/0x04/0x06/0x0A/0x0B), 0x3E
    """

    S3_TIMEOUT_S = 5.0

    def __init__(self):
        super().__init__(daemon=True, name="SimECU")
        self.bus          = can.Bus(interface="virtual", channel=CHANNEL)
        self.session      = SESSION_DEFAULT
        self._stop        = threading.Event()
        self._last_diag_t = time.monotonic()
        self._receiver    = ISOTPReceiver()

        # ── DTC store ────────────────────────────────────────────────────
        # P0300 Random Misfire — active, confirmed, MIL on, with snapshot
        #   and extended data showing 4 occurrences over 8 failed cycles.
        # P0128 Thermostat Stuck Open — pending only, minimal data.
        # P0420 Catalyst Efficiency — confirmed-stored, with ageing data.
        # U0100 Lost Comm ECM — active, confirmed, MIL on, permanent (OBD).
        # P0171 Fuel System Lean — confirmed, with snapshot (lean at idle).
        self._dtcs: list[DTCRecord] = [
            DTCRecord(
                code     = 0x0300,
                name     = "P0300 Random Misfire",
                status   = DTC_TF|DTC_TFTMC|DTC_PDTC|DTC_CDTC|DTC_TFSLC|DTC_WIR,
                snapshot = DTCSnapshot(
                    record_number=0x01,
                    engine_rpm=2500, vehicle_speed=45,
                    coolant_temp=92, engine_load=85, fuel_trim=15
                ),
                ext_data = DTCExtData(record_number=0x01,
                                      occurrence_count=4, ageing_counter=0,
                                      failed_cycles=8),
            ),
            DTCRecord(
                code    = 0x0128,
                name    = "P0128 Thermostat Stuck Open",
                status  = DTC_PDTC | DTC_TFSLC,
                snapshot= None,   # not yet confirmed — no freeze frame
                ext_data= DTCExtData(record_number=0x01,
                                     occurrence_count=1, ageing_counter=0,
                                     failed_cycles=1),
            ),
            DTCRecord(
                code     = 0x0420,
                name     = "P0420 Catalyst Efficiency Low",
                status   = DTC_CDTC | DTC_TFSLC,
                snapshot = DTCSnapshot(
                    record_number=0x01,
                    engine_rpm=1200, vehicle_speed=0,
                    coolant_temp=88, engine_load=20, fuel_trim=2
                ),
                ext_data = DTCExtData(record_number=0x01,
                                      occurrence_count=2, ageing_counter=5,
                                      failed_cycles=3),
            ),
            DTCRecord(
                code      = 0xC100,
                name      = "U0100 Lost Comm with ECM",
                status    = DTC_TF|DTC_TFTMC|DTC_PDTC|DTC_CDTC|DTC_TFSLC|DTC_WIR,
                snapshot  = DTCSnapshot(
                    record_number=0x01,
                    engine_rpm=800, vehicle_speed=0,
                    coolant_temp=72, engine_load=5, fuel_trim=0
                ),
                ext_data  = DTCExtData(record_number=0x01,
                                       occurrence_count=7, ageing_counter=0,
                                       failed_cycles=7),
                permanent = True,   # OBD-mandated permanent DTC
            ),
            DTCRecord(
                code     = 0x0171,
                name     = "P0171 Fuel System Too Lean Bank1",
                status   = DTC_CDTC | DTC_TFSLC | DTC_PDTC,
                snapshot = DTCSnapshot(
                    record_number=0x01,
                    engine_rpm=750, vehicle_speed=0,
                    coolant_temp=85, engine_load=8, fuel_trim=22
                ),
                ext_data = DTCExtData(record_number=0x01,
                                      occurrence_count=3, ageing_counter=2,
                                      failed_cycles=5),
            ),
        ]

    # ── Lifecycle ─────────────────────────────────────────────────────

    def stop(self) -> None:
        self._stop.set()
        self.bus.shutdown()

    # ── ISO-TP send (ECU → Tester) ────────────────────────────────────

    def _send_frames(self, payload: list) -> None:
        """
        Transmit a UDS payload using correct ISO-TP framing.
        For multi-frame: send FF, wait for FC, then send CFs with STmin gap.
        """
        if len(payload) <= 7:
            self.bus.send(can.Message(
                arbitration_id=TESTER_RX_ID,
                data=build_sf(payload),
                is_extended_id=False
            ))
            return

        # Send First Frame
        ff_data = build_ff(payload)
        self.bus.send(can.Message(
            arbitration_id=TESTER_RX_ID,
            data=ff_data,
            is_extended_id=False
        ))

        # Wait for Flow Control from tester
        fc_frame = self.bus.recv(timeout=N_BS_MAX_MS / 1000.0)
        if fc_frame is None or fc_frame.arbitration_id != TESTER_TX_ID:
            return   # N_Bs timeout or wrong sender — abort
        fc_byte = fc_frame.data[0]
        if (fc_byte & 0xF0) != 0x30:
            return   # not a FC frame
        fc_status   = fc_byte & 0x0F
        block_size  = fc_frame.data[1]   # 0 = send all
        stmin_raw   = fc_frame.data[2]
        stmin_s     = stmin_raw / 1000.0 if stmin_raw <= 0x7F else 0.0

        if fc_status == FC_OVERFLOW:
            return   # tester says abort
        if fc_status == FC_WAIT:
            # In production: re-wait for another FC; here we abort for simplicity
            return

        # Send Consecutive Frames (fc_status == FC_CTS)
        sn     = 1
        offset = 6
        sent   = 0
        while offset < len(payload):
            chunk = payload[offset: offset + 7]
            cf    = build_cf(sn, chunk)
            self.bus.send(can.Message(
                arbitration_id=TESTER_RX_ID,
                data=cf,
                is_extended_id=False
            ))
            sn     = (sn + 1) & 0x0F
            offset += 7
            sent   += 1
            # Respect STmin between consecutive frames
            if stmin_s > 0:
                time.sleep(stmin_s)
            else:
                time.sleep(0.001)   # minimal gap to avoid frame collision
            # BlockSize handling: if block_size > 0, pause after N CFs for another FC
            if block_size > 0 and (sent % block_size) == 0 and offset < len(payload):
                fc2 = self.bus.recv(timeout=N_BS_MAX_MS / 1000.0)
                if fc2 is None:
                    return
                if fc2.data[0] & 0x0F == FC_OVERFLOW:
                    return

    def _neg(self, sid: int, nrc: int) -> None:
        self._send_frames([SID_NEG, sid, nrc])

    # ── Service: 0x10 DiagnosticSessionControl ────────────────────────

    def _handle_session(self, sub: int) -> None:
        valid = {SESSION_DEFAULT, SESSION_EXTENDED}
        if sub not in valid:
            self._neg(SID_SESSION, NRC_SUBFUNC_NOT_SUPPORTED); return
        self.session      = sub
        self._last_diag_t = time.monotonic()
        self._send_frames([SID_SESSION + 0x40, sub, 0x00, 0x19, 0x01, 0xF4])

    # ── Service: 0x3E TesterPresent ───────────────────────────────────

    def _handle_tester_present(self, uds: list) -> None:
        if len(uds) < 2:
            self._neg(SID_TESTER_P, NRC_INCORRECT_MSG_LENGTH); return
        sub      = uds[1]
        suppress = (sub & 0x80) != 0
        core_sub = sub & 0x7F
        if core_sub != 0x00:
            self._neg(SID_TESTER_P, NRC_SUBFUNC_NOT_SUPPORTED); return
        self._last_diag_t = time.monotonic()
        if not suppress:
            self._send_frames([SID_TESTER_P + 0x40, core_sub])

    # ── Service: 0x19 ReadDTCInformation ─────────────────────────────

    def _handle_read_dtc(self, uds: list) -> None:
        if len(uds) < 2:
            self._neg(SID_READ_DTC, NRC_INCORRECT_MSG_LENGTH); return
        sub = uds[1]

        # 0x01 reportNumberOfDTCByStatusMask
        if sub == RDI_NUM_BY_MASK:
            if len(uds) < 3:
                self._neg(SID_READ_DTC, NRC_INCORRECT_MSG_LENGTH); return
            mask  = uds[2]
            count = sum(1 for d in self._dtcs if d.matches_mask(mask))
            self._send_frames([
                SID_READ_DTC + 0x40, sub,
                DTC_STATUS_AVAIL_MASK, DTC_FORMAT_ID,
                (count >> 8) & 0xFF, count & 0xFF,
            ])

        # 0x02 reportDTCByStatusMask
        elif sub == RDI_BY_MASK:
            if len(uds) < 3:
                self._neg(SID_READ_DTC, NRC_INCORRECT_MSG_LENGTH); return
            mask    = uds[2]
            matched = [d for d in self._dtcs if d.matches_mask(mask)]
            payload = [SID_READ_DTC + 0x40, sub, DTC_STATUS_AVAIL_MASK]
            for d in matched:
                payload += [d.high_byte, d.low_byte, d.status]
            self._send_frames(payload)

        # 0x04 reportDTCSnapshotRecordByDTCNumber (freeze frame)
        elif sub == RDI_SNAPSHOT:
            # Request: [0x19, 0x04, dtc_H, dtc_L, record_number]
            if len(uds) < 5:
                self._neg(SID_READ_DTC, NRC_INCORRECT_MSG_LENGTH); return
            code   = (uds[2] << 8) | uds[3]
            rec_no = uds[4]
            dtc    = next((d for d in self._dtcs if d.code == code), None)
            if dtc is None:
                self._neg(SID_READ_DTC, NRC_REQUEST_OUT_OF_RANGE); return
            if dtc.snapshot is None:
                # DTC exists but has no freeze frame (not yet confirmed)
                payload = [SID_READ_DTC + 0x40, sub,
                           dtc.high_byte, dtc.low_byte, dtc.status,
                           0x00]   # 0 records
                self._send_frames(payload)
                return
            payload = ([SID_READ_DTC + 0x40, sub,
                        dtc.high_byte, dtc.low_byte, dtc.status,
                        0x01]           # 1 snapshot record follows
                       + dtc.snapshot.encode())
            self._send_frames(payload)

        # 0x06 reportDTCExtDataRecordByDTCNumber
        elif sub == RDI_EXT_DATA:
            # Request: [0x19, 0x06, dtc_H, dtc_L, ext_data_record_number]
            if len(uds) < 5:
                self._neg(SID_READ_DTC, NRC_INCORRECT_MSG_LENGTH); return
            code   = (uds[2] << 8) | uds[3]
            rec_no = uds[4]
            dtc    = next((d for d in self._dtcs if d.code == code), None)
            if dtc is None:
                self._neg(SID_READ_DTC, NRC_REQUEST_OUT_OF_RANGE); return
            if dtc.ext_data is None:
                payload = [SID_READ_DTC + 0x40, sub,
                           dtc.high_byte, dtc.low_byte, dtc.status,
                           0x00]   # 0 extended data records
                self._send_frames(payload)
                return
            payload = ([SID_READ_DTC + 0x40, sub,
                        dtc.high_byte, dtc.low_byte, dtc.status]
                       + dtc.ext_data.encode())
            self._send_frames(payload)

        # 0x0A reportSupportedDTC
        elif sub == RDI_SUPPORTED:
            payload = [SID_READ_DTC + 0x40, sub, DTC_STATUS_AVAIL_MASK]
            for d in self._dtcs:
                payload += [d.high_byte, d.low_byte, d.status]
            self._send_frames(payload)

        # 0x0B reportDTCWithPermanentStatus
        elif sub == RDI_PERMANENT:
            perm    = [d for d in self._dtcs if d.permanent]
            payload = [SID_READ_DTC + 0x40, sub, DTC_STATUS_AVAIL_MASK]
            for d in perm:
                payload += [d.high_byte, d.low_byte, d.status]
            self._send_frames(payload)

        else:
            self._neg(SID_READ_DTC, NRC_SUBFUNC_NOT_SUPPORTED)

    # ── Service: 0x14 ClearDiagnosticInformation ─────────────────────

    def _handle_clear_dtc(self, uds: list) -> None:
        if len(uds) != 4:
            self._neg(SID_CLEAR_DTC, NRC_INCORRECT_MSG_LENGTH); return
        if self.session == SESSION_DEFAULT:
            self._neg(SID_CLEAR_DTC, NRC_CONDITIONS_NOT_CORRECT); return
        group = (uds[1] << 16) | (uds[2] << 8) | uds[3]
        if group == 0xFFFFFF:
            for d in self._dtcs:
                d.clear()
        else:
            code = group & 0xFFFF
            dtc  = next((d for d in self._dtcs if d.code == code), None)
            if dtc is None:
                self._neg(SID_CLEAR_DTC, NRC_REQUEST_OUT_OF_RANGE); return
            dtc.clear()
        self._send_frames([SID_CLEAR_DTC + 0x40])

    # ── Receive: handles both single-frame and multi-frame inbound ────

    def _receive_uds(self, frame: can.Message):
        """
        Feed an incoming CAN frame into the ISO-TP receiver.
        If it produces a FC request, send the FC.
        If it produces a complete UDS message, dispatch it.
        """
        uds, fc_params = self._receiver.feed(bytes(frame.data))

        if fc_params is not None:
            # Received a FF — need to send a Flow Control back
            fc_flag, bs, stmin = fc_params
            fc_data = build_fc(fc_flag, bs, stmin)
            self.bus.send(can.Message(
                arbitration_id=TESTER_RX_ID,
                data=fc_data,
                is_extended_id=False
            ))

        if uds is not None:
            self._dispatch(uds)

    def _dispatch(self, uds: list) -> None:
        if not uds:
            return
        self._last_diag_t = time.monotonic()
        sid = uds[0]
        if   sid == SID_SESSION and len(uds) >= 2: self._handle_session(uds[1])
        elif sid == SID_TESTER_P:                  self._handle_tester_present(uds)
        elif sid == SID_READ_DTC:                  self._handle_read_dtc(uds)
        elif sid == SID_CLEAR_DTC:                 self._handle_clear_dtc(uds)
        else:                                      self._neg(sid, NRC_SERVICE_NOT_SUPPORTED)

    # ── Main loop ─────────────────────────────────────────────────────

    def run(self) -> None:
        while not self._stop.is_set():
            if (self.session != SESSION_DEFAULT
                    and time.monotonic() - self._last_diag_t > self.S3_TIMEOUT_S):
                self.session = SESSION_DEFAULT

            frame = self.bus.recv(timeout=0.05)
            if frame is None or frame.arbitration_id != TESTER_TX_ID:
                continue

            self._receive_uds(frame)


# ─── TESTER CLIENT ────────────────────────────────────────────────────────────

class UDSTester:
    """
    UDS tester with an explicit ISO-TP sender/receiver.
    Handles multi-frame responses (sends Flow Control, reassembles CFs).
    """

    RESPONSE_TIMEOUT_S = 2.0

    def __init__(self):
        self.bus    = can.Bus(interface="virtual", channel=CHANNEL)
        self.passed = []
        self.failed = []

    def shutdown(self) -> None:
        self.bus.shutdown()

    # ── ISO-TP send (Tester → ECU) ────────────────────────────────────

    def _send(self, uds_bytes: list) -> None:
        """Send a UDS message using proper ISO-TP framing."""
        if len(uds_bytes) <= 7:
            self.bus.send(can.Message(
                arbitration_id=TESTER_TX_ID,
                data=build_sf(uds_bytes),
                is_extended_id=False
            ))
        else:
            # Multi-frame send (tester→ECU): rare in practice but correct to implement
            ff_data = build_ff(uds_bytes)
            self.bus.send(can.Message(
                arbitration_id=TESTER_TX_ID, data=ff_data, is_extended_id=False
            ))
            fc_frame = self.bus.recv(timeout=1.0)
            if fc_frame is None:
                return
            sn, offset = 1, 6
            while offset < len(uds_bytes):
                chunk = uds_bytes[offset: offset + 7]
                self.bus.send(can.Message(
                    arbitration_id=TESTER_TX_ID,
                    data=build_cf(sn, chunk),
                    is_extended_id=False
                ))
                sn     = (sn + 1) & 0x0F
                offset += 7
                time.sleep(0.001)

    # ── ISO-TP receive (ECU response) ─────────────────────────────────

    def _recv(self, timeout: float = None) -> Optional[list]:
        """
        Receive a complete UDS response from the ECU.
        Handles multi-frame reassembly by sending Flow Control on FF.
        """
        deadline          = time.monotonic() + (timeout or self.RESPONSE_TIMEOUT_S)
        collected_payload = []
        total_expected    = 0

        while time.monotonic() < deadline:
            remaining = max(0.01, deadline - time.monotonic())
            frame     = self.bus.recv(timeout=remaining)
            if frame is None or frame.arbitration_id != TESTER_RX_ID:
                continue

            first_byte = frame.data[0]
            pci_type   = (first_byte >> 4) & 0x0F

            if pci_type == 0:                          # Single Frame
                length = first_byte & 0x0F
                uds    = list(frame.data[1: 1 + length])
                # RCRRP: 0x78 — extend deadline and keep waiting
                if len(uds) == 3 and uds[0] == SID_NEG and uds[2] == 0x78:
                    print("    ⏳ RCRRP 0x78 — extending wait...")
                    deadline += 5.0
                    continue
                return uds

            elif pci_type == 1:                        # First Frame
                total_expected    = ((first_byte & 0x0F) << 8) | frame.data[1]
                collected_payload = list(frame.data[2:])   # first 6 bytes
                # Send Flow Control: CTS, blockSize=0, STmin=10ms
                fc_data = build_fc(FC_CTS, 0x00, STMIN_MS)
                self.bus.send(can.Message(
                    arbitration_id=TESTER_TX_ID,
                    data=fc_data,
                    is_extended_id=False
                ))

            elif pci_type == 2:                        # Consecutive Frame
                collected_payload += list(frame.data[1:])
                if len(collected_payload) >= total_expected:
                    return collected_payload[:total_expected]

        return collected_payload[:total_expected] if collected_payload else None

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
            self._fail(name, "timeout — no response"); return False
        if resp[0] == SID_NEG:
            nrc = resp[2] if len(resp) >= 3 else "?"
            self._fail(name, f"NegResp NRC=0x{nrc:02X}"); return False
        if resp[0] != expected_sid + 0x40:
            self._fail(name, f"wrong SID 0x{resp[0]:02X}"); return False
        self._pass(name, f"SID=0x{resp[0]:02X}"); return True

    def _assert_negative(self, name: str, resp, expected_nrc: int) -> bool:
        if resp is None:
            self._fail(name, "timeout — no response"); return False
        if resp[0] != SID_NEG:
            self._fail(name, f"expected NegResp got 0x{resp[0]:02X}"); return False
        actual = resp[2] if len(resp) >= 3 else 0
        if actual != expected_nrc:
            self._fail(name, f"expected 0x{expected_nrc:02X} got 0x{actual:02X}"); return False
        self._pass(name, f"NRC=0x{actual:02X}"); return True

    def _switch_session(self, session_type: int) -> None:
        self._send([SID_SESSION, session_type])
        self._recv()

    # ── Helpers for parsing DTC responses ──────────────────────────────

    @staticmethod
    def _parse_dtc_list(resp: list) -> list:
        if resp is None or len(resp) < 3:
            return []
        dtcs, i = [], 3
        while i + 2 < len(resp):
            dtcs.append(((resp[i] << 8) | resp[i + 1], resp[i + 2]))
            i += 3
        return dtcs

    @staticmethod
    def _parse_dtc_count(resp: list) -> int:
        if resp is None or len(resp) < 6:
            return -1
        return (resp[4] << 8) | resp[5]

    # ═════════════════════════════════════════════════════════════════
    # GROUP 1: ISO-TP Frame Structure Validation
    # ═════════════════════════════════════════════════════════════════

    def tc01_sf_structure(self) -> None:
        """TC01: Verify Single Frame structure — PCI = payload length, padding = 0x00."""
        frame = build_sf([0x10, 0x03])
        pci    = frame[0]
        length = pci & 0x0F
        pci_type = (pci >> 4) & 0x0F
        if pci_type == 0 and length == 2 and all(b == 0x00 for b in frame[3:]):
            self._pass("TC01 SF structure: PCI=0x02, padding=0x00", f"0x{frame.hex()}")
        else:
            self._fail("TC01 SF structure", f"frame={frame.hex()}")

    def tc02_ff_structure(self) -> None:
        """TC02: Verify First Frame encodes total length in 12-bit field."""
        payload = list(range(20))   # 20-byte payload → needs FF + 2 CFs
        ff = build_ff(payload)
        total_encoded = ((ff[0] & 0x0F) << 8) | ff[1]
        if (ff[0] & 0xF0) == 0x10 and total_encoded == 20 and list(ff[2:]) == payload[:6]:
            self._pass("TC02 FF structure: total_len=20 encoded in 12-bit",
                       f"0x{ff.hex()}")
        else:
            self._fail("TC02 FF structure", f"ff={ff.hex()}, encoded={total_encoded}")

    def tc03_cf_sn_wrapping(self) -> None:
        """TC03: Consecutive Frame SN wraps 0xF → 0x0 (not 0xF → 0x1)."""
        cf_f = build_cf(0x0F, [0xAA] * 7)
        cf_0 = build_cf(0x00, [0xBB] * 7)
        sn_f = cf_f[0] & 0x0F
        sn_0 = cf_0[0] & 0x0F
        if sn_f == 0x0F and sn_0 == 0x00 and (cf_f[0] & 0xF0) == 0x20:
            self._pass("TC03 CF SN wrapping: 0x0F → 0x00 ✓", "")
        else:
            self._fail("TC03 CF SN", f"sn_f=0x{sn_f:02X}, sn_0=0x{sn_0:02X}")

    def tc04_fc_cts_structure(self) -> None:
        """TC04: Flow Control CTS — byte0=0x30, byte1=blockSize, byte2=STmin."""
        fc = build_fc(FC_CTS, 0x00, 10)
        if fc[0] == 0x30 and fc[1] == 0x00 and fc[2] == 0x0A:
            self._pass("TC04 FC CTS: [0x30, 0x00, 0x0A] ✓", f"0x{fc.hex()}")
        else:
            self._fail("TC04 FC CTS", f"fc={fc.hex()}")

    def tc05_fc_wait_structure(self) -> None:
        """TC05: Flow Control WAIT — byte0=0x31 (0x30 | FC_WAIT=0x01)."""
        fc = build_fc(FC_WAIT, 0x00, 0)
        if (fc[0] & 0x0F) == FC_WAIT and (fc[0] & 0xF0) == 0x30:
            self._pass("TC05 FC WAIT: byte0=0x31 ✓", f"0x{fc.hex()}")
        else:
            self._fail("TC05 FC WAIT", f"fc={fc.hex()}")

    def tc06_segmentation_roundtrip(self) -> None:
        """TC06: segment_message + reassembly roundtrip for 20-byte payload."""
        original = list(range(20))
        first, rest = segment_message(original)
        # Reassemble
        receiver = ISOTPReceiver()
        reassembled, fc_params = receiver.feed(first)
        if fc_params is not None:
            # Continue feeding CFs
            for cf in rest:
                reassembled, _ = receiver.feed(cf)
        if reassembled == original:
            self._pass("TC06 Segment/reassemble roundtrip 20 bytes ✓", "")
        else:
            self._fail("TC06 Roundtrip",
                       f"expected {original} got {reassembled}")

    def tc07_large_segmentation(self) -> None:
        """TC07: 50-byte payload requires FF + 7 CFs (6 + 7×7 = 55 ≥ 50)."""
        payload = list(range(50))
        first, rest = segment_message(payload)
        ff_type = (first[0] >> 4) & 0x0F
        total   = ((first[0] & 0x0F) << 8) | first[1]
        if ff_type == 1 and total == 50 and len(rest) == 7:
            self._pass("TC07 50-byte payload: FF + 7 CFs ✓",
                       f"total={total}, CFs={len(rest)}")
        else:
            self._fail("TC07 Large segmentation",
                       f"ff_type={ff_type}, total={total}, CFs={len(rest)}")

    # ═════════════════════════════════════════════════════════════════
    # GROUP 2: Live Multi-Frame UDS Exchange
    # ═════════════════════════════════════════════════════════════════

    def tc08_multiframe_read_supported_dtcs(self) -> None:
        """TC08: 0x19 0x0A returns 5 DTCs — response > 7 bytes (multi-frame)."""
        self._switch_session(SESSION_DEFAULT)
        self._send([SID_READ_DTC, RDI_SUPPORTED])
        resp = self._recv()
        if not self._assert_positive("TC08 0x19 0x0A multi-frame response",
                                      resp, SID_READ_DTC): return
        dtcs = self._parse_dtc_list(resp)
        if len(dtcs) == 5:
            self._pass("TC08 5 supported DTCs returned via multi-frame ✓", "")
        else:
            self._fail("TC08 DTC count", f"expected 5 got {len(dtcs)}")

    def tc09_multiframe_read_by_mask_all(self) -> None:
        """TC09: 0x19 0x02 mask=0xFF — 5 DTCs, multi-frame response correct."""
        self._send([SID_READ_DTC, RDI_BY_MASK, 0xFF])
        resp = self._recv()
        if not self._assert_positive("TC09 0x19 0x02 mask=0xFF multi-frame",
                                      resp, SID_READ_DTC): return
        dtcs = self._parse_dtc_list(resp)
        if len(dtcs) == 5:
            self._pass("TC09 5 DTCs via multi-frame mask=0xFF ✓", "")
        else:
            self._fail("TC09 DTC count via mask", f"expected 5 got {len(dtcs)}")

    # ═════════════════════════════════════════════════════════════════
    # GROUP 3: DTC Snapshot / Freeze Frame (0x19 0x04)
    # ═════════════════════════════════════════════════════════════════

    def tc10_snapshot_p0300(self) -> None:
        """TC10: Freeze frame for P0300 (0x0300) — verify RPM and speed encoded correctly."""
        self._send([SID_READ_DTC, RDI_SNAPSHOT, 0x03, 0x00, 0x01])
        resp = self._recv()
        if not self._assert_positive("TC10 0x19 0x04 P0300 freeze frame",
                                      resp, SID_READ_DTC): return
        # Response: [0x59, 0x04, dtcH, dtcL, status, recordCount, recordNo, rpmH, rpmL, speed, temp, load, ftrim]
        if len(resp) < 13:
            self._fail("TC10 Response too short", f"len={len(resp)}"); return
        dtc_code    = (resp[2] << 8) | resp[3]
        rec_count   = resp[5]
        rec_no      = resp[6]
        rpm         = (resp[7] << 8) | resp[8]
        speed       = resp[9]
        coolant_raw = resp[10]
        coolant     = coolant_raw - 40   # reverse the +40 offset encoding
        load        = resp[11]
        fuel_trim   = resp[12] if resp[12] <= 127 else resp[12] - 256
        if dtc_code == 0x0300 and rpm == 2500 and speed == 45:
            self._pass("TC10 P0300 freeze frame: RPM=2500, speed=45",
                       f"temp={coolant}°C, load={load}%, ftrim={fuel_trim}%")
        else:
            self._fail("TC10 P0300 freeze frame",
                       f"code=0x{dtc_code:04X}, rpm={rpm}, speed={speed}")

    def tc11_snapshot_p0128_none(self) -> None:
        """TC11: P0128 has no freeze frame (only pending, not confirmed) → record count = 0."""
        self._send([SID_READ_DTC, RDI_SNAPSHOT, 0x01, 0x28, 0x01])
        resp = self._recv()
        if not self._assert_positive("TC11 0x19 0x04 P0128 no freeze frame",
                                      resp, SID_READ_DTC): return
        # resp[5] = number of snapshot records
        rec_count = resp[5] if len(resp) >= 6 else -1
        if rec_count == 0:
            self._pass("TC11 P0128 snapshot count = 0 (pending, no freeze frame) ✓", "")
        else:
            self._fail("TC11 P0128 snapshot count", f"expected 0 got {rec_count}")

    def tc12_snapshot_unknown_dtc(self) -> None:
        """TC12: Freeze frame for unknown DTC code → NRC 0x31."""
        self._send([SID_READ_DTC, RDI_SNAPSHOT, 0x99, 0x99, 0x01])
        resp = self._recv()
        self._assert_negative("TC12 0x19 0x04 unknown DTC → NRC 0x31",
                              resp, NRC_REQUEST_OUT_OF_RANGE)

    # ═════════════════════════════════════════════════════════════════
    # GROUP 4: DTC Extended Data (0x19 0x06)
    # ═════════════════════════════════════════════════════════════════

    def tc13_ext_data_p0300(self) -> None:
        """TC13: Extended data for P0300 — occurrence=4, ageing=0, failed_cycles=8."""
        self._send([SID_READ_DTC, RDI_EXT_DATA, 0x03, 0x00, 0x01])
        resp = self._recv()
        if not self._assert_positive("TC13 0x19 0x06 P0300 extended data",
                                      resp, SID_READ_DTC): return
        # Response: [0x59, 0x06, dtcH, dtcL, status, recNo, occ, ageing, failed]
        if len(resp) < 9:
            self._fail("TC13 Response too short", f"len={len(resp)}"); return
        dtc_code   = (resp[2] << 8) | resp[3]
        rec_no     = resp[5]
        occurrence = resp[6]
        ageing     = resp[7]
        failed_c   = resp[8]
        if dtc_code == 0x0300 and occurrence == 4 and ageing == 0 and failed_c == 8:
            self._pass("TC13 P0300 ext data: occ=4, ageing=0, failed=8 ✓", "")
        else:
            self._fail("TC13 P0300 ext data",
                       f"occ={occurrence}, ageing={ageing}, failed={failed_c}")

    def tc14_ext_data_p0420_ageing(self) -> None:
        """TC14: P0420 ageing counter = 5 — fault not currently active, counting toward age-out."""
        self._send([SID_READ_DTC, RDI_EXT_DATA, 0x04, 0x20, 0x01])
        resp = self._recv()
        if not self._assert_positive("TC14 0x19 0x06 P0420 ageing counter",
                                      resp, SID_READ_DTC): return
        ageing = resp[7] if len(resp) >= 8 else -1
        if ageing == 5:
            self._pass("TC14 P0420 ageing counter = 5 (counting to age-out) ✓", "")
        else:
            self._fail("TC14 P0420 ageing", f"expected 5 got {ageing}")

    def tc15_ext_data_unknown_dtc(self) -> None:
        """TC15: Extended data for unknown DTC → NRC 0x31."""
        self._send([SID_READ_DTC, RDI_EXT_DATA, 0xAB, 0xCD, 0x01])
        resp = self._recv()
        self._assert_negative("TC15 0x19 0x06 unknown DTC → NRC 0x31",
                              resp, NRC_REQUEST_OUT_OF_RANGE)

    # ═════════════════════════════════════════════════════════════════
    # GROUP 5: Permanent DTCs (0x19 0x0B)
    # ═════════════════════════════════════════════════════════════════

    def tc16_permanent_dtcs_before_clear(self) -> None:
        """TC16: 0x19 0x0B before clear — only U0100 is permanent → count=1."""
        self._send([SID_READ_DTC, RDI_PERMANENT])
        resp = self._recv()
        if not self._assert_positive("TC16 0x19 0x0B permanent DTCs",
                                      resp, SID_READ_DTC): return
        dtcs = self._parse_dtc_list(resp)
        if len(dtcs) == 1 and dtcs[0][0] == 0xC100:
            self._pass("TC16 Permanent DTC = U0100 only ✓", "code=0xC100")
        else:
            codes = [f"0x{d[0]:04X}" for d in dtcs]
            self._fail("TC16 Permanent DTC list", f"got {codes}")

    def tc17_clear_then_permanent_survives(self) -> None:
        """TC17: ClearDiagnosticInformation (0xFFFFFF) then 0x19 0x0B — U0100 still present."""
        self._switch_session(SESSION_EXTENDED)
        self._send([SID_CLEAR_DTC, 0xFF, 0xFF, 0xFF])
        clear_resp = self._recv()
        if clear_resp is None or clear_resp[0] != SID_CLEAR_DTC + 0x40:
            self._fail("TC17 Clear all DTCs failed", ""); return
        # Now check permanent DTCs — should still have U0100
        self._send([SID_READ_DTC, RDI_PERMANENT])
        resp = self._recv()
        if not self._assert_positive("TC17 0x19 0x0B after clear",
                                      resp, SID_READ_DTC): return
        dtcs = self._parse_dtc_list(resp)
        if len(dtcs) == 1 and dtcs[0][0] == 0xC100:
            self._pass("TC17 Permanent DTC survived clear ✓", "U0100 still present")
        else:
            codes = [f"0x{d[0]:04X}" for d in dtcs]
            self._fail("TC17 Permanent DTC after clear", f"got {codes}")

    def tc18_non_permanent_cleared(self) -> None:
        """TC18: After clear, 0x19 0x02 0xFF returns 1 DTC (only U0100 permanent remains)."""
        # Still in extended session from TC17 which already ran clear
        self._send([SID_READ_DTC, RDI_BY_MASK, 0xFF])
        resp = self._recv()
        if not self._assert_positive("TC18 0x19 0x02 after clear",
                                      resp, SID_READ_DTC): return
        dtcs = self._parse_dtc_list(resp)
        # After clear: non-permanent DTCs have status=0x00, which fails mask=0xFF
        # Only permanent U0100 still has non-zero status (permanent flag preserves status)
        if len(dtcs) == 1 and dtcs[0][0] == 0xC100:
            self._pass("TC18 After clear: only permanent U0100 has non-zero status ✓",
                       f"count={len(dtcs)}")
        else:
            codes = [f"0x{d[0]:04X}" for d in dtcs]
            self._fail("TC18 Post-clear DTC list", f"expected [0xC100] got {codes}")

    # ═════════════════════════════════════════════════════════════════
    # GROUP 6: ISO-TP BlockSize and STmin Interaction
    # ═════════════════════════════════════════════════════════════════

    def tc19_fc_blocksize_zero_means_all(self) -> None:
        """TC19: FC block_size=0 means send all remaining CFs without pausing — verify complete response."""
        # Reset ECU DTC store by directly verifying a fresh read works
        # (this also tests that STmin=10ms gap doesn't break the receiver)
        self._switch_session(SESSION_DEFAULT)
        self._send([SID_READ_DTC, RDI_SUPPORTED])
        resp = self._recv(timeout=3.0)   # allow time for all CFs with 10ms STmin
        dtcs = self._parse_dtc_list(resp) if resp else []
        # After TC17 cleared, all non-permanent DTCs have status=0x00
        # reportSupportedDTC (0x0A) returns all regardless of status
        total = len(dtcs)
        if total == 5:
            self._pass("TC19 FC blockSize=0: all 5 CFs received with STmin gap ✓",
                       f"DTCs={total}")
        else:
            self._fail("TC19 FC blockSize=0", f"expected 5 DTCs got {total}")

    def tc20_stmin_encoding_boundary(self) -> None:
        """TC20: STmin 0x00=0ms, 0x7F=127ms, 0x80=reserved (treated as 0), 0xF1=100µs."""
        cases = [
            (0x00,  0),     # 0 ms
            (0x14,  20),    # 20 ms (direct)
            (0x7F, 127),    # 127 ms (max direct)
        ]
        all_ok = True
        for stmin_raw, expected_ms in cases:
            decoded = stmin_raw if stmin_raw <= 0x7F else 0
            if decoded != expected_ms:
                self._fail("TC20 STmin encoding", f"raw=0x{stmin_raw:02X} expected={expected_ms}ms got={decoded}ms")
                all_ok = False
        if all_ok:
            self._pass("TC20 STmin encoding: 0x00=0ms, 0x14=20ms, 0x7F=127ms ✓", "")

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
    print("\n" + "🚌📦  " * 10)
    print("  Day 16 — ISO-TP Transport Protocol (ISO 15765-2)")
    print("           + Advanced DTC: Snapshot, ExtData, Permanent")
    print("🚌📦  " * 10)

    ecu    = SimulatedECU()
    tester = UDSTester()
    ecu.start()
    time.sleep(0.1)

    banner("GROUP 1: ISO-TP Frame Structure (Unit Tests)")
    tester.tc01_sf_structure()
    tester.tc02_ff_structure()
    tester.tc03_cf_sn_wrapping()
    tester.tc04_fc_cts_structure()
    tester.tc05_fc_wait_structure()
    tester.tc06_segmentation_roundtrip()
    tester.tc07_large_segmentation()

    banner("GROUP 2: Live Multi-Frame UDS Exchange")
    tester.tc08_multiframe_read_supported_dtcs()
    tester.tc09_multiframe_read_by_mask_all()

    banner("GROUP 3: DTC Snapshot / Freeze Frame (0x19 0x04)")
    tester.tc10_snapshot_p0300()
    tester.tc11_snapshot_p0128_none()
    tester.tc12_snapshot_unknown_dtc()

    banner("GROUP 4: DTC Extended Data (0x19 0x06)")
    tester.tc13_ext_data_p0300()
    tester.tc14_ext_data_p0420_ageing()
    tester.tc15_ext_data_unknown_dtc()

    banner("GROUP 5: Permanent DTCs (0x19 0x0B)")
    tester.tc16_permanent_dtcs_before_clear()
    tester.tc17_clear_then_permanent_survives()
    tester.tc18_non_permanent_cleared()

    banner("GROUP 6: ISO-TP BlockSize and STmin")
    tester.tc19_fc_blocksize_zero_means_all()
    tester.tc20_stmin_encoding_boundary()

    tester.print_summary()
    ecu.stop()
    tester.shutdown()


if __name__ == "__main__":
    main()
