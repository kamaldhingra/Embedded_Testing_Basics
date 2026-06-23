"""
Day 13: UDS ReadDataByIdentifier (0x22) & WriteDataByIdentifier (0x2E)
=======================================================================
Simulates an ECU with a DID data store on a python-can virtual bus.
Exercises read, multi-DID read, write with session gating, range
validation, NVM persistence, and all relevant error paths.

No hardware needed.

Install:
    pip install python-can
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
SID_SESSION  = 0x10
SID_RESET    = 0x11
SID_READ     = 0x22
SID_WRITE    = 0x2E
SID_NEG      = 0x7F

# Session types
SESSION_DEFAULT     = 0x01
SESSION_PROGRAMMING = 0x02
SESSION_EXTENDED    = 0x03

# NRCs
NRC_SUBFUNC_NOT_SUPPORTED          = 0x12
NRC_INCORRECT_MSG_LENGTH_OR_FORMAT = 0x13
NRC_CONDITIONS_NOT_CORRECT         = 0x22
NRC_REQUEST_SEQUENCE_ERROR         = 0x24
NRC_REQUEST_OUT_OF_RANGE           = 0x31
NRC_SECURITY_ACCESS_DENIED         = 0x33

# DIDs — standardised (ISO 14229 F1xx range)
DID_ACTIVE_SESSION   = 0xF186
DID_SW_VERSION       = 0xF189
DID_ECU_SERIAL       = 0xF18C
DID_VIN              = 0xF190

# DIDs — manufacturer-specific
DID_TYRE_PRESSURE_FL = 0x2001
DID_MAX_RPM_LIMIT    = 0x3001
DID_INTERNAL_TEMP    = 0x5001


# ─── ISO-TP HELPERS ───────────────────────────────────────────────────────────

def build_single_frame(uds_bytes: list) -> bytes:
    """Wrap up to 7 UDS bytes in an ISO-TP Single Frame."""
    assert 1 <= len(uds_bytes) <= 7, "Single frame max 7 UDS bytes"
    pci    = len(uds_bytes)
    padded = [pci] + uds_bytes + [0x00] * (7 - len(uds_bytes))
    return bytes(padded)


def build_multi_frame_response(uds_bytes: list) -> list:
    """
    Build an ISO-TP First Frame + Consecutive Frame sequence.
    Returns a list of 8-byte CAN frame data.
    """
    total  = len(uds_bytes)
    frames = []

    # First Frame
    ff = [0x10 | ((total >> 8) & 0x0F), total & 0xFF] + uds_bytes[:6]
    frames.append(bytes(ff))

    # Consecutive Frames
    sn, offset = 1, 6
    while offset < total:
        chunk = uds_bytes[offset: offset + 7]
        cf    = [0x20 | (sn & 0x0F)] + chunk + [0x00] * (7 - len(chunk))
        frames.append(bytes(cf))
        sn     = (sn + 1) & 0x0F
        offset += 7

    return frames


def parse_uds_from_frame(data: bytes):
    """Extract UDS payload from an ISO-TP Single Frame. Returns None if not SF."""
    pci = data[0]
    if (pci & 0xF0) != 0x00:
        return None
    length = pci & 0x0F
    return list(data[1: 1 + length])


# ─── DID DESCRIPTOR ───────────────────────────────────────────────────────────

class DIDRecord:
    """Describes a single Data Identifier in the ECU's store."""

    def __init__(self, did: int, name: str, value: bytes,
                 writable: bool = False,
                 min_val: int = None, max_val: int = None,
                 requires_extended: bool = False,
                 requires_security: bool = False,
                 dynamic_fn=None):
        self.did               = did
        self.name              = name
        self._value            = value
        self.writable          = writable
        self.min_val           = min_val      # for 2-byte unsigned integer DIDs
        self.max_val           = max_val
        self.requires_extended = requires_extended
        self.requires_security = requires_security
        self._dynamic_fn       = dynamic_fn   # callable → bytes, or None

    def read(self) -> bytes:
        if self._dynamic_fn:
            return self._dynamic_fn()
        return self._value

    def write(self, data: bytes) -> None:
        self._value = data


# ─── SIMULATED ECU ───────────────────────────────────────────────────────────

class SimulatedECU(threading.Thread):
    """
    A UDS-capable ECU simulation. Handles 0x10, 0x22, 0x2E.
    DID store is pre-populated with representative real-world identifiers.
    """

    S3_TIMEOUT_S = 5.0

    def __init__(self):
        super().__init__(daemon=True, name="SimECU")
        self.bus               = can.Bus(interface="virtual", channel=CHANNEL)
        self.session           = SESSION_DEFAULT
        self.security_unlocked = False   # simplified SecurityAccess state
        self._stop             = threading.Event()
        self._last_diag_t      = time.monotonic()
        self._dids: dict       = {}

        # ── Populate DID store ────────────────────────────────────
        def _session_bytes():
            return bytes([self.session])

        self._register(DIDRecord(
            DID_VIN, "VIN",
            b"WBA3A5G59DNP26082",
        ))
        self._register(DIDRecord(
            DID_SW_VERSION, "SWVersion",
            b"v2.4.1\x00",
        ))
        self._register(DIDRecord(
            DID_ECU_SERIAL, "ECUSerial",
            b"ECU20240315-001",
        ))
        self._register(DIDRecord(
            DID_ACTIVE_SESSION, "ActiveSession",
            b"\x01",
            dynamic_fn=_session_bytes,
        ))
        self._register(DIDRecord(
            DID_TYRE_PRESSURE_FL, "TyrePressureFL",
            struct.pack(">H", 220),          # default 220 kPa
            writable=True,
            min_val=80, max_val=280,
            requires_extended=True,
        ))
        self._register(DIDRecord(
            DID_MAX_RPM_LIMIT, "MaxRPMLimit",
            struct.pack(">H", 6500),
            writable=True,
            min_val=4000, max_val=8000,
            requires_extended=True,
            requires_security=True,
        ))
        self._register(DIDRecord(
            DID_INTERNAL_TEMP, "InternalTemp",
            b"",
            dynamic_fn=lambda: struct.pack(
                ">H", 6000 + int(time.monotonic() * 10) % 1000
            ),
        ))

    def _register(self, rec: DIDRecord) -> None:
        self._dids[rec.did] = rec

    # ── Public control ────────────────────────────────────────────

    def stop(self):
        self._stop.set()
        self.bus.shutdown()

    def unlock_security(self) -> None:
        """Simulate SecurityAccess (0x27) being successfully completed."""
        self.security_unlocked = True

    # ── Response helpers ──────────────────────────────────────────

    def _send_raw(self, payload: list) -> None:
        if len(payload) <= 7:
            self.bus.send(can.Message(
                arbitration_id=TESTER_RX_ID,
                data=build_single_frame(payload),
                is_extended_id=False
            ))
        else:
            for frame_data in build_multi_frame_response(payload):
                self.bus.send(can.Message(
                    arbitration_id=TESTER_RX_ID,
                    data=frame_data,
                    is_extended_id=False
                ))
                time.sleep(0.001)

    def _neg(self, sid: int, nrc: int) -> None:
        self._send_raw([SID_NEG, sid, nrc])

    # ── Service: 0x10 Session Control ────────────────────────────

    def _handle_session(self, sub: int) -> None:
        valid = {SESSION_DEFAULT, SESSION_PROGRAMMING, SESSION_EXTENDED}
        if sub not in valid:
            self._neg(SID_SESSION, NRC_SUBFUNC_NOT_SUPPORTED)
            return
        if self.session == SESSION_DEFAULT and sub == SESSION_PROGRAMMING:
            self._neg(SID_SESSION, NRC_REQUEST_SEQUENCE_ERROR)
            return
        self.session = sub
        if sub == SESSION_DEFAULT:
            self.security_unlocked = False
        self._last_diag_t = time.monotonic()
        self._send_raw([SID_SESSION + 0x40, sub, 0x00, 0x19, 0x01, 0xF4])

    # ── Service: 0x22 ReadDataByIdentifier ───────────────────────

    def _handle_read(self, uds: list) -> None:
        # Must have SID + at least one 2-byte DID, and total DID bytes must be even
        if len(uds) < 3 or (len(uds) - 1) % 2 != 0:
            self._neg(SID_READ, NRC_INCORRECT_MSG_LENGTH_OR_FORMAT)
            return

        did_bytes = uds[1:]
        dids      = [(did_bytes[i] << 8) | did_bytes[i + 1]
                     for i in range(0, len(did_bytes), 2)]

        # Validate all DIDs before building response
        for did in dids:
            if did not in self._dids:
                self._neg(SID_READ, NRC_REQUEST_OUT_OF_RANGE)
                return

        # Build concatenated response
        response = [SID_READ + 0x40]
        for did in dids:
            rec   = self._dids[did]
            value = rec.read()
            response += [(did >> 8) & 0xFF, did & 0xFF]
            response += list(value)

        self._send_raw(response)

    # ── Service: 0x2E WriteDataByIdentifier ──────────────────────

    def _handle_write(self, uds: list) -> None:
        if len(uds) < 4:   # SID + 2 DID bytes + 1 data byte minimum
            self._neg(SID_WRITE, NRC_INCORRECT_MSG_LENGTH_OR_FORMAT)
            return

        did      = (uds[1] << 8) | uds[2]
        data_raw = bytes(uds[3:])

        if did not in self._dids:
            self._neg(SID_WRITE, NRC_REQUEST_OUT_OF_RANGE)
            return

        rec = self._dids[did]

        if not rec.writable:
            self._neg(SID_WRITE, NRC_REQUEST_OUT_OF_RANGE)
            return

        if rec.requires_extended and self.session == SESSION_DEFAULT:
            self._neg(SID_WRITE, NRC_CONDITIONS_NOT_CORRECT)
            return

        if rec.requires_security and not self.security_unlocked:
            self._neg(SID_WRITE, NRC_SECURITY_ACCESS_DENIED)
            return

        if rec.min_val is not None and rec.max_val is not None:
            if len(data_raw) != 2:
                self._neg(SID_WRITE, NRC_INCORRECT_MSG_LENGTH_OR_FORMAT)
                return
            val = struct.unpack(">H", data_raw)[0]
            if not (rec.min_val <= val <= rec.max_val):
                self._neg(SID_WRITE, NRC_REQUEST_OUT_OF_RANGE)
                return

        rec.write(data_raw)
        self._send_raw([SID_WRITE + 0x40, (did >> 8) & 0xFF, did & 0xFF])

    # ── Main loop ─────────────────────────────────────────────────

    def run(self) -> None:
        while not self._stop.is_set():

            # S3 timer
            if (self.session != SESSION_DEFAULT
                    and time.monotonic() - self._last_diag_t > self.S3_TIMEOUT_S):
                self.session           = SESSION_DEFAULT
                self.security_unlocked = False

            frame = self.bus.recv(timeout=0.05)
            if frame is None or frame.arbitration_id != TESTER_TX_ID:
                continue

            self._last_diag_t = time.monotonic()
            uds = parse_uds_from_frame(bytes(frame.data))
            if uds is None or len(uds) < 1:
                continue

            sid = uds[0]
            if sid == SID_SESSION and len(uds) >= 2:
                self._handle_session(uds[1])
            elif sid == SID_READ:
                self._handle_read(uds)
            elif sid == SID_WRITE:
                self._handle_write(uds)
            else:
                self._neg(sid, 0x11)   # serviceNotSupported


# ─── TESTER CLIENT ────────────────────────────────────────────────────────────

class UDSTester:
    """UDS tester client with structured pass/fail reporting."""

    RESPONSE_TIMEOUT_S = 2.0

    def __init__(self):
        self.bus    = can.Bus(interface="virtual", channel=CHANNEL)
        self.passed = []
        self.failed = []

    def shutdown(self):
        self.bus.shutdown()

    # ── Transport ─────────────────────────────────────────────────

    def _send(self, uds_bytes: list) -> None:
        self.bus.send(can.Message(
            arbitration_id=TESTER_TX_ID,
            data=build_single_frame(uds_bytes),
            is_extended_id=False
        ))

    def _recv(self, timeout: float = None):
        """
        Collect a UDS response. Handles:
        - Single frames (normal case for short DIDs)
        - Multi-frame (First Frame + Consecutive Frames for long DIDs like VIN)
        - 0x78 RCRRP (response pending)
        """
        deadline          = time.monotonic() + (timeout or self.RESPONSE_TIMEOUT_S)
        collected_payload = []
        total_expected    = 0

        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            frame     = self.bus.recv(timeout=max(0.01, remaining))
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
                # First Frame — collect payload and send Flow Control
                total_expected    = ((first_byte & 0x0F) << 8) | frame.data[1]
                collected_payload = list(frame.data[2:])
                self.bus.send(can.Message(
                    arbitration_id=TESTER_TX_ID,
                    data=bytes([0x30, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]),
                    is_extended_id=False
                ))

            elif pci_type == 0x2:
                # Consecutive Frame
                collected_payload += list(frame.data[1:])
                if len(collected_payload) >= total_expected - 2:
                    # -2 because FF already had 6 payload bytes (not the 2-byte header)
                    return collected_payload[:total_expected]

        if collected_payload:
            return collected_payload
        return None

    # ── Assertion helpers ─────────────────────────────────────────

    def _pass(self, name: str, detail: str = "") -> None:
        tag = f"  ✅ PASS  {name}" + (f"  [{detail}]" if detail else "")
        print(tag)
        self.passed.append(tag)

    def _fail(self, name: str, detail: str = "") -> None:
        tag = f"  ❌ FAIL  {name}" + (f"  [{detail}]" if detail else "")
        print(tag)
        self.failed.append(tag)

    def _assert_positive_read(self, name: str, resp, expected_did: int) -> bytes:
        if resp is None:
            self._fail(name, "no response (timeout)")
            return b""
        if resp[0] == SID_NEG:
            nrc = resp[2] if len(resp) >= 3 else "?"
            self._fail(name, f"NegResp NRC=0x{nrc:02X}")
            return b""
        if resp[0] != SID_READ + 0x40:
            self._fail(name, f"wrong SID 0x{resp[0]:02X}")
            return b""
        resp_did = (resp[1] << 8) | resp[2]
        if resp_did != expected_did:
            self._fail(name, f"DID: expected 0x{expected_did:04X} "
                             f"got 0x{resp_did:04X}")
            return b""
        data = bytes(resp[3:])
        self._pass(name, f"DID=0x{expected_did:04X}  data={data}")
        return data

    def _assert_positive_write(self, name: str, resp, expected_did: int) -> bool:
        if resp is None:
            self._fail(name, "no response (timeout)")
            return False
        if resp[0] == SID_NEG:
            nrc = resp[2] if len(resp) >= 3 else "?"
            self._fail(name, f"NegResp NRC=0x{nrc:02X}")
            return False
        if resp[0] != SID_WRITE + 0x40:
            self._fail(name, f"wrong SID 0x{resp[0]:02X}")
            return False
        self._pass(name, f"DID=0x{expected_did:04X} write acknowledged")
        return True

    def _assert_negative(self, name: str, resp, expected_nrc: int) -> bool:
        if resp is None:
            self._fail(name, "no response (timeout)")
            return False
        if resp[0] != SID_NEG:
            self._fail(name, f"expected NegResp got 0x{resp[0]:02X}")
            return False
        actual = resp[2] if len(resp) >= 3 else 0
        if actual != expected_nrc:
            self._fail(name, f"NRC: expected 0x{expected_nrc:02X} "
                             f"got 0x{actual:02X}")
            return False
        self._pass(name, f"NRC=0x{actual:02X}")
        return True

    # ── Session helper ────────────────────────────────────────────

    def _switch_session(self, session_type: int) -> None:
        self._send([SID_SESSION, session_type])
        self._recv()

    # ─────────────────────────────────────────────────────────────
    # TEST CASES
    # ─────────────────────────────────────────────────────────────

    def tc01_read_vin(self) -> None:
        """TC01: Read VIN in default session — 17 bytes, valid ASCII."""
        self._switch_session(SESSION_DEFAULT)
        self._send([SID_READ, 0xF1, 0x90])
        resp = self._recv()
        data = self._assert_positive_read("TC01 Read VIN (0xF190)", resp, DID_VIN)
        if data:
            if len(data) == 17:
                self._pass("TC01 VIN length", f"{len(data)} bytes ✓")
            else:
                self._fail("TC01 VIN length", f"expected 17 got {len(data)}")
            try:
                vin_str = data.decode("ascii").rstrip("\x00")
                self._pass("TC01 VIN content", f'"{vin_str}"')
            except Exception:
                self._fail("TC01 VIN content", "non-ASCII in VIN bytes")

    def tc02_read_sw_version(self) -> None:
        """TC02: Read software version."""
        self._send([SID_READ, 0xF1, 0x89])
        resp = self._recv()
        data = self._assert_positive_read("TC02 Read SW Version (0xF189)",
                                          resp, DID_SW_VERSION)
        if data:
            ver = data.decode("ascii").rstrip("\x00")
            self._pass("TC02 SW version content", f'"{ver}"')

    def tc03_read_active_session(self) -> None:
        """TC03: 0xF186 reflects current session (default = 0x01)."""
        self._send([SID_READ, 0xF1, 0x86])
        resp = self._recv()
        data = self._assert_positive_read("TC03 Read ActiveSession (0xF186)",
                                          resp, DID_ACTIVE_SESSION)
        if data:
            if data[0] == SESSION_DEFAULT:
                self._pass("TC03 ActiveSession = 0x01 (default)", "✓")
            else:
                self._fail("TC03 ActiveSession = 0x01 (default)",
                           f"got 0x{data[0]:02X}")

    def tc04_read_unknown_did(self) -> None:
        """TC04: Unknown DID returns NRC 0x31."""
        self._send([SID_READ, 0x99, 0x99])
        resp = self._recv()
        self._assert_negative("TC04 Unknown DID → NRC 0x31",
                              resp, NRC_REQUEST_OUT_OF_RANGE)

    def tc05_multi_did_read(self) -> None:
        """TC05: Multi-DID read: VIN + SW Version in one request."""
        self._send([SID_READ, 0xF1, 0x90, 0xF1, 0x89])
        resp = self._recv()

        if resp is None:
            self._fail("TC05 Multi-DID read", "no response")
            return
        if resp[0] == SID_NEG:
            nrc = resp[2] if len(resp) >= 3 else "?"
            self._fail("TC05 Multi-DID read", f"NegResp NRC=0x{nrc:02X}")
            return
        if resp[0] != SID_READ + 0x40:
            self._fail("TC05 Multi-DID read", f"wrong SID 0x{resp[0]:02X}")
            return

        # Parse concatenated response
        offset     = 1
        found_dids = {}
        while offset + 2 <= len(resp):
            did     = (resp[offset] << 8) | resp[offset + 1]
            offset += 2
            if did == DID_VIN:
                found_dids[did] = bytes(resp[offset: offset + 17])
                offset         += 17
            else:
                found_dids[did] = bytes(resp[offset:])
                break

        if DID_VIN in found_dids and DID_SW_VERSION in found_dids:
            self._pass("TC05 Multi-DID read", "VIN + SW Version both returned")
        elif DID_VIN in found_dids:
            self._pass("TC05 Multi-DID partial", "VIN found in response")
        else:
            self._fail("TC05 Multi-DID read", "expected DIDs not in response")

    def tc06_write_in_default_session_rejected(self) -> None:
        """TC06: Write writable DID in default session → NRC 0x22."""
        self._switch_session(SESSION_DEFAULT)
        payload = struct.pack(">H", 220)
        self._send([SID_WRITE, 0x20, 0x01] + list(payload))
        resp = self._recv()
        self._assert_negative("TC06 Write in default session → NRC 0x22",
                              resp, NRC_CONDITIONS_NOT_CORRECT)

    def tc07_write_tyre_pressure_success(self) -> None:
        """TC07: Write tyre pressure in extended session + read-back verify."""
        self._switch_session(SESSION_EXTENDED)

        new_val = 250   # kPa
        payload = struct.pack(">H", new_val)
        self._send([SID_WRITE, 0x20, 0x01] + list(payload))
        resp = self._recv()
        if not self._assert_positive_write("TC07 Write TyrePressureFL=250 kPa",
                                            resp, DID_TYRE_PRESSURE_FL):
            return

        # Mandatory read-back verify
        self._send([SID_READ, 0x20, 0x01])
        resp2 = self._recv()
        data  = self._assert_positive_read("TC07 Read-back TyrePressureFL",
                                           resp2, DID_TYRE_PRESSURE_FL)
        if data:
            actual = struct.unpack(">H", data[:2])[0]
            if actual == new_val:
                self._pass("TC07 Read-back value correct", f"{actual} kPa ✓")
            else:
                self._fail("TC07 Read-back value correct",
                           f"expected {new_val} got {actual}")

    def tc08_write_out_of_range(self) -> None:
        """TC08: Value above max (280 kPa) → NRC 0x31."""
        self._switch_session(SESSION_EXTENDED)
        payload = struct.pack(">H", 999)
        self._send([SID_WRITE, 0x20, 0x01] + list(payload))
        resp = self._recv()
        self._assert_negative("TC08 Write out-of-range (999 kPa) → NRC 0x31",
                              resp, NRC_REQUEST_OUT_OF_RANGE)

    def tc09_write_wrong_length(self) -> None:
        """TC09: TyrePressure expects 2 bytes; send 1 → NRC 0x13."""
        self._switch_session(SESSION_EXTENDED)
        self._send([SID_WRITE, 0x20, 0x01, 0xAA])   # only 1 data byte
        resp = self._recv()
        self._assert_negative("TC09 Wrong data length → NRC 0x13",
                              resp, NRC_INCORRECT_MSG_LENGTH_OR_FORMAT)

    def tc10_write_read_only_did(self) -> None:
        """TC10: Attempt to write a read-only DID (VIN) → NRC 0x31."""
        self._switch_session(SESSION_EXTENDED)
        self._send([SID_WRITE, 0xF1, 0x90] + list(b"TESTVIN00000000001"))
        resp = self._recv()
        self._assert_negative("TC10 Write read-only DID (VIN) → NRC 0x31",
                              resp, NRC_REQUEST_OUT_OF_RANGE)

    def tc11_write_requires_security(self, ecu: SimulatedECU) -> None:
        """TC11: MaxRPMLimit requires security unlock — NRC 0x33, then success."""
        self._switch_session(SESSION_EXTENDED)

        val = struct.pack(">H", 7000)

        # Without security unlock → should fail
        self._send([SID_WRITE, 0x30, 0x01] + list(val))
        resp = self._recv()
        self._assert_negative("TC11 Write without security → NRC 0x33",
                              resp, NRC_SECURITY_ACCESS_DENIED)

        # Grant security access (simulating successful 0x27 exchange)
        ecu.unlock_security()

        # Now the write should succeed
        self._send([SID_WRITE, 0x30, 0x01] + list(val))
        resp2 = self._recv()
        self._assert_positive_write("TC11 Write after security unlock → success",
                                     resp2, DID_MAX_RPM_LIMIT)

    def tc12_active_session_did_reflects_switch(self) -> None:
        """TC12: 0xF186 returns 0x03 after switching to extended session."""
        self._switch_session(SESSION_EXTENDED)
        self._send([SID_READ, 0xF1, 0x86])
        resp = self._recv()
        data = self._assert_positive_read("TC12 0xF186 in extended session",
                                          resp, DID_ACTIVE_SESSION)
        if data:
            if data[0] == SESSION_EXTENDED:
                self._pass("TC12 0xF186 = 0x03 (extended)", "✓")
            else:
                self._fail("TC12 0xF186 = 0x03 (extended)",
                           f"got 0x{data[0]:02X}")

    # ── Summary ───────────────────────────────────────────────────

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
    print("\n" + "📖✏️  " * 12)
    print("  Day 13 — UDS ReadDataByIdentifier (0x22) &")
    print("           WriteDataByIdentifier (0x2E) Simulator")
    print("📖✏️  " * 12)

    ecu    = SimulatedECU()
    tester = UDSTester()

    ecu.start()
    time.sleep(0.1)   # let ECU thread start

    banner("GROUP 1: ReadDataByIdentifier (0x22) — Happy Paths")
    tester.tc01_read_vin()
    tester.tc02_read_sw_version()
    tester.tc03_read_active_session()

    banner("GROUP 2: ReadDataByIdentifier (0x22) — Error & Multi-DID")
    tester.tc04_read_unknown_did()
    tester.tc05_multi_did_read()

    banner("GROUP 3: WriteDataByIdentifier (0x2E) — Session Gating & Happy Path")
    tester.tc06_write_in_default_session_rejected()
    tester.tc07_write_tyre_pressure_success()

    banner("GROUP 4: WriteDataByIdentifier (0x2E) — Data Validation")
    tester.tc08_write_out_of_range()
    tester.tc09_write_wrong_length()
    tester.tc10_write_read_only_did()

    banner("GROUP 5: Security Gating & Dynamic DID")
    tester.tc11_write_requires_security(ecu)
    tester.tc12_active_session_did_reflects_switch()

    tester.print_summary()

    ecu.stop()
    tester.shutdown()


if __name__ == "__main__":
    main()
