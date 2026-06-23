"""
Day 14: UDS SecurityAccess (0x27) & RoutineControl (0x31)
==========================================================
Simulates an ECU with a seed/key security challenge-response and
a set of routine-control procedures (self-test, sensor calibration,
DTC memory check) on a python-can virtual bus.

No hardware needed.

Install:
    pip install python-can

Run:
    python uds_security_routine.py
"""

import can
import threading
import time
import random
import struct

# ─── UDS CONSTANTS ───────────────────────────────────────────────────────────

CHANNEL      = "vcan0"
TESTER_TX_ID = 0x7E0
TESTER_RX_ID = 0x7E8

# Service IDs
SID_SESSION = 0x10
SID_SEC     = 0x27    # SecurityAccess
SID_ROUTINE = 0x31    # RoutineControl
SID_NEG     = 0x7F

# Session types
SESSION_DEFAULT     = 0x01
SESSION_PROGRAMMING = 0x02
SESSION_EXTENDED    = 0x03

# SecurityAccess sub-functions (level 1)
SEC_REQ_SEED = 0x01   # odd  = requestSeed
SEC_SEND_KEY = 0x02   # even = sendKey

# RoutineControl sub-functions
RC_START   = 0x01
RC_STOP    = 0x02
RC_RESULTS = 0x03

# Routine Identifiers (RIDs)
RID_SELF_TEST  = 0xFF00   # General ECU self-test
RID_SENSOR_CAL = 0x0203   # Wheel-speed sensor calibration (security-gated)
RID_DTC_CHECK  = 0xDF00   # DTC memory integrity check

# NRCs
NRC_SUBFUNC_NOT_SUPPORTED   = 0x12
NRC_INCORRECT_MSG_LENGTH    = 0x13
NRC_CONDITIONS_NOT_CORRECT  = 0x22
NRC_REQUEST_SEQUENCE_ERROR  = 0x24
NRC_REQUEST_OUT_OF_RANGE    = 0x31
NRC_SECURITY_ACCESS_DENIED  = 0x33
NRC_INVALID_KEY             = 0x35
NRC_EXCEEDED_ATTEMPTS       = 0x36
NRC_REQUIRED_TIME_DELAY     = 0x37

# Security parameters
SEC_SECRET       = 0xDEADBEEF   # XOR mask — SIMULATION ONLY; never XOR in prod
SEC_MAX_ATTEMPTS = 3            # lockout after N wrong keys
SEC_LOCKOUT_SECS = 3.0          # lockout duration (short for simulation)

# Routine result status bytes
RS_RUNNING   = 0x01
RS_COMPLETED = 0x02
RS_STOPPED   = 0x03
RS_FAILED    = 0x04


# ─── ISO-TP HELPERS ───────────────────────────────────────────────────────────

def build_single_frame(uds_bytes: list) -> bytes:
    assert 1 <= len(uds_bytes) <= 7, "Single frame: max 7 UDS bytes"
    pci    = len(uds_bytes)
    padded = [pci] + uds_bytes + [0x00] * (7 - len(uds_bytes))
    return bytes(padded)


def build_multi_frame_response(uds_bytes: list) -> list:
    """Build ISO-TP First Frame + Consecutive Frames. Returns list of 8-byte frames."""
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
    """Extract UDS payload from an ISO-TP Single Frame. Returns None if not SF."""
    pci = data[0]
    if (pci & 0xF0) != 0x00:
        return None
    length = pci & 0x0F
    return list(data[1: 1 + length])


# ─── SECURITY ACCESS STATE ────────────────────────────────────────────────────

class SecurityState:
    """
    Tracks the SecurityAccess (0x27) challenge-response state.
    Enforces:
      - Seed/key sequencing (sendKey requires prior requestSeed)
      - Brute-force lockout after SEC_MAX_ATTEMPTS wrong keys
      - Timed lockout expiry (NRC 0x37)
    """

    def __init__(self):
        self.unlocked       = False
        self._seed          = 0
        self._seed_issued   = False
        self._failed_count  = 0
        self._lockout_until = 0.0   # monotonic timestamp

    def is_locked_out(self) -> bool:
        return time.monotonic() < self._lockout_until

    def issue_seed(self) -> int:
        """
        Return a new random seed, or 0 if already unlocked.
        Zero seed = "already unlocked" signal to the tester.
        """
        if self.unlocked:
            return 0x00000000
        seed              = random.randint(0x00000001, 0xFFFFFFFF)
        self._seed        = seed
        self._seed_issued = True
        return seed

    def verify_key(self, key: int) -> str:
        """
        Verify the tester's key against the stored seed.
        Returns one of: 'ok' | 'wrong' | 'lockout' | 'no_seed'
        """
        if not self._seed_issued:
            return "no_seed"

        expected = self._derive_key(self._seed)
        self._seed_issued = False   # seed is consumed regardless of outcome

        if key == expected:
            self.unlocked      = True
            self._failed_count = 0
            return "ok"

        self._failed_count += 1
        if self._failed_count >= SEC_MAX_ATTEMPTS:
            self._lockout_until = time.monotonic() + SEC_LOCKOUT_SECS
            self._failed_count  = 0
            return "lockout"
        return "wrong"

    def lock(self) -> None:
        """Reset to locked state — called on session drop to default."""
        self.unlocked      = False
        self._seed_issued  = False
        self._failed_count = 0
        # Note: does NOT clear _lockout_until —
        #       the timed lockout persists across session changes.

    @staticmethod
    def _derive_key(seed: int) -> int:
        """
        ⚠️  SIMULATION ONLY — XOR-based key derivation.
        Real implementations use AES-CMAC, HMAC-SHA256, or OEM-specific
        algorithms, often backed by a Hardware Security Module (HSM).
        A simple XOR has zero resistance to a determined attacker.
        Never use XOR in production key derivation.
        """
        return seed ^ SEC_SECRET


# ─── ROUTINE STATE ────────────────────────────────────────────────────────────

class RoutineState:
    """
    Tracks execution of one RoutineControl routine.
    Completion is evaluated lazily (on the next status query) based
    on elapsed time, avoiding the need for a separate timer thread.
    """

    def __init__(self, rid: int, name: str, duration_ms: float,
                 requires_security: bool = False):
        self.rid               = rid
        self.name              = name
        self.duration_ms       = duration_ms
        self.requires_security = requires_security
        self._status           = "idle"   # idle | running | completed | stopped
        self._start_t          = None
        self._result: bytes    = b""

    @property
    def status(self) -> str:
        """Lazily transition 'running' → 'completed' once duration elapsed."""
        if self._status == "running":
            elapsed_ms = (time.monotonic() - self._start_t) * 1000
            if elapsed_ms >= self.duration_ms:
                self._status = "completed"
                self._result = self._generate_result()
        return self._status

    def start(self) -> None:
        self._status  = "running"
        self._start_t = time.monotonic()
        self._result  = b""

    def stop(self) -> None:
        self._status = "stopped"

    def _generate_result(self) -> bytes:
        if self.rid == RID_SELF_TEST:
            return bytes([0x00])               # 0x00 = all self-checks passed
        elif self.rid == RID_SENSOR_CAL:
            return struct.pack(">H", 125)      # calibration offset = 125 (arbitrary)
        elif self.rid == RID_DTC_CHECK:
            return struct.pack(">H", 0)        # 0 DTC memory errors
        return b""


# ─── SIMULATED ECU ────────────────────────────────────────────────────────────

class SimulatedECU(threading.Thread):
    """
    UDS ECU simulation.
    Handles services: 0x10 (session), 0x27 (security), 0x31 (routine).
    """

    S3_TIMEOUT_S = 5.0

    def __init__(self):
        super().__init__(daemon=True, name="SimECU")
        self.bus          = can.Bus(interface="virtual", channel=CHANNEL)
        self.session      = SESSION_DEFAULT
        self.security     = SecurityState()
        self._stop        = threading.Event()
        self._last_diag_t = time.monotonic()

        # Supported routines
        self._routines: dict = {
            RID_SELF_TEST:  RoutineState(RID_SELF_TEST,  "SelfTest",    200.0),
            RID_SENSOR_CAL: RoutineState(RID_SENSOR_CAL, "SensorCal",   300.0,
                                         requires_security=True),
            RID_DTC_CHECK:  RoutineState(RID_DTC_CHECK,  "DTCMemCheck", 150.0),
        }

    # ── Public control ────────────────────────────────────────────

    def stop(self) -> None:
        self._stop.set()
        self.bus.shutdown()

    # ── Response helpers ──────────────────────────────────────────

    def _send_raw(self, payload: list) -> None:
        if len(payload) <= 7:
            self.bus.send(can.Message(
                arbitration_id=TESTER_RX_ID,
                data=build_single_frame(payload),
                is_extended_id=False
            ))
        else:
            for fd in build_multi_frame_response(payload):
                self.bus.send(can.Message(
                    arbitration_id=TESTER_RX_ID,
                    data=fd,
                    is_extended_id=False
                ))
                time.sleep(0.001)

    def _neg(self, sid: int, nrc: int) -> None:
        self._send_raw([SID_NEG, sid, nrc])

    # ── Service: 0x10 DiagnosticSessionControl ────────────────────

    def _handle_session(self, sub: int) -> None:
        valid = {SESSION_DEFAULT, SESSION_PROGRAMMING, SESSION_EXTENDED}
        if sub not in valid:
            self._neg(SID_SESSION, NRC_SUBFUNC_NOT_SUPPORTED)
            return
        if self.session == SESSION_DEFAULT and sub == SESSION_PROGRAMMING:
            self._neg(SID_SESSION, 0x24)   # requestSequenceError
            return
        self.session = sub
        if sub == SESSION_DEFAULT:
            self.security.lock()           # dropping to default resets security
        self._last_diag_t = time.monotonic()
        self._send_raw([SID_SESSION + 0x40, sub, 0x00, 0x19, 0x01, 0xF4])

    # ── Service: 0x27 SecurityAccess ──────────────────────────────

    def _handle_security(self, uds: list) -> None:
        if len(uds) < 2:
            self._neg(SID_SEC, NRC_INCORRECT_MSG_LENGTH)
            return

        sub = uds[1]

        # SecurityAccess requires at least extended session
        if self.session == SESSION_DEFAULT:
            self._neg(SID_SEC, NRC_CONDITIONS_NOT_CORRECT)
            return

        # Lockout check (time-based; persists across session changes)
        if self.security.is_locked_out():
            self._neg(SID_SEC, NRC_REQUIRED_TIME_DELAY)
            return

        if sub == SEC_REQ_SEED:
            seed = self.security.issue_seed()
            self._send_raw([
                SID_SEC + 0x40, sub,
                (seed >> 24) & 0xFF,
                (seed >> 16) & 0xFF,
                (seed >> 8)  & 0xFF,
                 seed        & 0xFF,
            ])

        elif sub == SEC_SEND_KEY:
            if len(uds) < 6:
                self._neg(SID_SEC, NRC_INCORRECT_MSG_LENGTH)
                return
            key    = ((uds[2] << 24) | (uds[3] << 16) |
                      (uds[4] << 8)  |  uds[5])
            result = self.security.verify_key(key)
            if result == "ok":
                self._send_raw([SID_SEC + 0x40, sub])
            elif result == "wrong":
                self._neg(SID_SEC, NRC_INVALID_KEY)
            elif result == "lockout":
                self._neg(SID_SEC, NRC_EXCEEDED_ATTEMPTS)
            elif result == "no_seed":
                self._neg(SID_SEC, NRC_REQUEST_SEQUENCE_ERROR)

        else:
            self._neg(SID_SEC, NRC_SUBFUNC_NOT_SUPPORTED)

    # ── Service: 0x31 RoutineControl ──────────────────────────────

    def _handle_routine(self, uds: list) -> None:
        if len(uds) < 4:
            self._neg(SID_ROUTINE, NRC_INCORRECT_MSG_LENGTH)
            return

        sub = uds[1]
        rid = (uds[2] << 8) | uds[3]

        # All routine operations require at least extended session
        if self.session == SESSION_DEFAULT:
            self._neg(SID_ROUTINE, NRC_CONDITIONS_NOT_CORRECT)
            return

        if rid not in self._routines:
            self._neg(SID_ROUTINE, NRC_REQUEST_OUT_OF_RANGE)
            return

        routine = self._routines[rid]

        if sub == RC_START:
            # Security check for security-gated routines
            if routine.requires_security and not self.security.unlocked:
                self._neg(SID_ROUTINE, NRC_SECURITY_ACCESS_DENIED)
                return
            # Re-starting a completed/stopped routine is allowed (resets it)
            if routine.status == "running":
                self._send_raw([SID_ROUTINE + 0x40, sub,
                                 (rid >> 8) & 0xFF, rid & 0xFF, RS_RUNNING])
                return
            routine.start()
            self._send_raw([SID_ROUTINE + 0x40, sub,
                             (rid >> 8) & 0xFF, rid & 0xFF, RS_RUNNING])

        elif sub == RC_STOP:
            if routine.status != "running":
                self._neg(SID_ROUTINE, NRC_CONDITIONS_NOT_CORRECT)
                return
            routine.stop()
            self._send_raw([SID_ROUTINE + 0x40, sub,
                             (rid >> 8) & 0xFF, rid & 0xFF])

        elif sub == RC_RESULTS:
            st = routine.status
            if st == "idle":
                # Never started — can't request results
                self._neg(SID_ROUTINE, NRC_CONDITIONS_NOT_CORRECT)
                return
            status_byte = {
                "running":   RS_RUNNING,
                "completed": RS_COMPLETED,
                "stopped":   RS_STOPPED,
            }.get(st, RS_FAILED)
            result_data = routine._result if st == "completed" else b""
            payload     = ([SID_ROUTINE + 0x40, sub,
                             (rid >> 8) & 0xFF, rid & 0xFF,
                             status_byte] + list(result_data))
            self._send_raw(payload)

        else:
            self._neg(SID_ROUTINE, NRC_SUBFUNC_NOT_SUPPORTED)

    # ── Main loop ─────────────────────────────────────────────────

    def run(self) -> None:
        while not self._stop.is_set():

            # S3 session-timeout watchdog
            if (self.session != SESSION_DEFAULT
                    and time.monotonic() - self._last_diag_t > self.S3_TIMEOUT_S):
                self.session = SESSION_DEFAULT
                self.security.lock()

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
            elif sid == SID_SEC:
                self._handle_security(uds)
            elif sid == SID_ROUTINE:
                self._handle_routine(uds)
            else:
                self._neg(sid, 0x11)   # serviceNotSupported


# ─── TESTER CLIENT ────────────────────────────────────────────────────────────

class UDSTester:
    """UDS tester with SecurityAccess and RoutineControl test cases."""

    RESPONSE_TIMEOUT_S = 2.0

    def __init__(self):
        self.bus    = can.Bus(interface="virtual", channel=CHANNEL)
        self.passed = []
        self.failed = []

    def shutdown(self) -> None:
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
        Collect a UDS response, handling multi-frame and 0x78 RCRRP.
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
                length = first_byte & 0x0F
                uds    = list(frame.data[1: 1 + length])
                if len(uds) == 3 and uds[0] == SID_NEG and uds[2] == 0x78:
                    print("    ⏳ RCRRP 0x78 — extending wait...")
                    deadline += 5.0
                    continue
                return uds

            elif pci_type == 0x1:
                total_expected    = ((first_byte & 0x0F) << 8) | frame.data[1]
                collected_payload = list(frame.data[2:])
                self.bus.send(can.Message(
                    arbitration_id=TESTER_TX_ID,
                    data=bytes([0x30, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]),
                    is_extended_id=False
                ))

            elif pci_type == 0x2:
                collected_payload += list(frame.data[1:])
                if len(collected_payload) >= total_expected - 2:
                    return collected_payload[:total_expected]

        return collected_payload if collected_payload else None

    # ── Assertion helpers ─────────────────────────────────────────

    def _pass(self, name: str, detail: str = "") -> None:
        tag = f"  ✅ PASS  {name}" + (f"  [{detail}]" if detail else "")
        print(tag)
        self.passed.append(tag)

    def _fail(self, name: str, detail: str = "") -> None:
        tag = f"  ❌ FAIL  {name}" + (f"  [{detail}]" if detail else "")
        print(tag)
        self.failed.append(tag)

    def _assert_positive(self, name: str, resp,
                          expected_sid: int, expected_sub: int) -> bool:
        if resp is None:
            self._fail(name, "no response (timeout)")
            return False
        if resp[0] == SID_NEG:
            nrc = resp[2] if len(resp) >= 3 else "?"
            self._fail(name, f"NegResp NRC=0x{nrc:02X}")
            return False
        if resp[0] != expected_sid + 0x40:
            self._fail(name, f"wrong SID 0x{resp[0]:02X}")
            return False
        if len(resp) < 2 or resp[1] != expected_sub:
            got = resp[1] if len(resp) >= 2 else "?"
            self._fail(name, f"wrong sub 0x{got:02X}")
            return False
        self._pass(name, f"0x{resp[0]:02X} 0x{resp[1]:02X}")
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

    # ── Reusable helpers ──────────────────────────────────────────

    def _switch_session(self, session_type: int) -> None:
        self._send([SID_SESSION, session_type])
        self._recv()

    def _do_security_unlock(self) -> bool:
        """
        Perform a full SecurityAccess exchange (requestSeed → sendKey).
        Returns True if the ECU confirms unlocked (0x67 0x02).
        """
        self._send([SID_SEC, SEC_REQ_SEED])
        resp = self._recv()
        if resp is None or resp[0] != SID_SEC + 0x40 or len(resp) < 6:
            return False
        seed = ((resp[2] << 24) | (resp[3] << 16) |
                (resp[4] << 8)  |  resp[5])
        if seed == 0x00000000:
            return True   # already unlocked
        key = seed ^ SEC_SECRET
        self._send([SID_SEC, SEC_SEND_KEY,
                    (key >> 24) & 0xFF,
                    (key >> 16) & 0xFF,
                    (key >> 8)  & 0xFF,
                     key        & 0xFF])
        resp2 = self._recv()
        return (resp2 is not None
                and resp2[0] == SID_SEC + 0x40
                and resp2[1] == SEC_SEND_KEY)

    # ─────────────────────────────────────────────────────────────
    # TEST CASES
    # ─────────────────────────────────────────────────────────────

    # GROUP 1: SecurityAccess — Session Gating ────────────────────

    def tc01_seed_in_default_session(self) -> None:
        """TC01: requestSeed in defaultSession → NRC 0x22."""
        self._switch_session(SESSION_DEFAULT)
        self._send([SID_SEC, SEC_REQ_SEED])
        resp = self._recv()
        self._assert_negative("TC01 requestSeed in default → NRC 0x22",
                              resp, NRC_CONDITIONS_NOT_CORRECT)

    def tc02_seed_in_extended_session(self) -> None:
        """TC02: requestSeed in extendedSession → positive with 4-byte seed."""
        self._switch_session(SESSION_EXTENDED)
        self._send([SID_SEC, SEC_REQ_SEED])
        resp = self._recv()
        if not self._assert_positive("TC02 requestSeed in extended",
                                      resp, SID_SEC, SEC_REQ_SEED):
            return
        if len(resp) >= 6:
            seed = ((resp[2] << 24) | (resp[3] << 16) |
                    (resp[4] << 8)  |  resp[5])
            if seed != 0:
                self._pass("TC02 Non-zero seed received", f"0x{seed:08X}")
            else:
                self._fail("TC02 Seed is non-zero", "got 0x00000000 (already unlocked?)")
        else:
            self._fail("TC02 Seed length", f"expected 4 seed bytes, got {len(resp) - 2}")

    # GROUP 2: SecurityAccess — Key Exchange ──────────────────────

    def tc03_correct_key_unlocks(self) -> None:
        """TC03: Compute correct key → security unlocked (0x67 0x02)."""
        self._switch_session(SESSION_EXTENDED)
        # Request a fresh seed
        self._send([SID_SEC, SEC_REQ_SEED])
        resp = self._recv()
        if resp is None or resp[0] == SID_NEG:
            self._fail("TC03 setup: requestSeed failed")
            return
        seed = ((resp[2] << 24) | (resp[3] << 16) |
                (resp[4] << 8)  |  resp[5])
        key  = seed ^ SEC_SECRET   # correct derivation
        self._send([SID_SEC, SEC_SEND_KEY,
                    (key >> 24) & 0xFF,
                    (key >> 16) & 0xFF,
                    (key >> 8)  & 0xFF,
                     key        & 0xFF])
        resp2 = self._recv()
        self._assert_positive("TC03 sendKey correct → security unlocked",
                               resp2, SID_SEC, SEC_SEND_KEY)

    def tc04_sendkey_without_seed(self) -> None:
        """TC04: sendKey without prior requestSeed → NRC 0x24 (sequence error)."""
        self._switch_session(SESSION_EXTENDED)
        # Send key directly — no prior seed request
        self._send([SID_SEC, SEC_SEND_KEY, 0xDE, 0xAD, 0xBE, 0xEF])
        resp = self._recv()
        self._assert_negative("TC04 sendKey without seed → NRC 0x24",
                              resp, NRC_REQUEST_SEQUENCE_ERROR)

    # GROUP 3: SecurityAccess — Brute-Force Protection ────────────

    def tc05_wrong_key_nrc35(self) -> None:
        """TC05: One wrong key → NRC 0x35 (invalidKey)."""
        # Reset attempt counter by cycling through default session
        self._switch_session(SESSION_DEFAULT)
        self._switch_session(SESSION_EXTENDED)
        self._send([SID_SEC, SEC_REQ_SEED])
        self._recv()   # consume seed (don't compute the correct key)
        self._send([SID_SEC, SEC_SEND_KEY, 0x00, 0x00, 0x00, 0x01])   # wrong key
        resp = self._recv()
        self._assert_negative("TC05 Wrong key → NRC 0x35",
                              resp, NRC_INVALID_KEY)

    def tc06_lockout_after_three_fails(self) -> None:
        """TC06: Three consecutive wrong keys → NRC 0x36 (exceededNumberOfAttempts)."""
        # Reset attempt counter
        self._switch_session(SESSION_DEFAULT)
        self._switch_session(SESSION_EXTENDED)

        for attempt in range(1, SEC_MAX_ATTEMPTS + 1):
            self._send([SID_SEC, SEC_REQ_SEED])
            seed_resp = self._recv()
            if seed_resp is None or seed_resp[0] == SID_NEG:
                self._fail(f"TC06 attempt {attempt}: seed request failed")
                break

            # Always send a deliberately wrong key
            self._send([SID_SEC, SEC_SEND_KEY, 0x00, 0x00, 0x00, 0x00])
            resp = self._recv()

            if attempt < SEC_MAX_ATTEMPTS:
                if (resp and resp[0] == SID_NEG
                        and resp[2] == NRC_INVALID_KEY):
                    self._pass(f"TC06 attempt {attempt}/{SEC_MAX_ATTEMPTS} → NRC 0x35", "✓")
                else:
                    nrc = f"0x{resp[2]:02X}" if resp and len(resp) >= 3 else "?"
                    self._fail(f"TC06 attempt {attempt}", f"expected NRC 0x35 got {nrc}")
            else:
                # Final attempt triggers lockout
                self._assert_negative(
                    f"TC06 attempt {attempt}/{SEC_MAX_ATTEMPTS} → NRC 0x36 (lockout)",
                    resp, NRC_EXCEEDED_ATTEMPTS)

    def tc07_lockout_period(self) -> None:
        """TC07: During lockout → NRC 0x37; after expiry, seed available again."""
        # Must be called immediately after TC06 while lockout is still active
        self._send([SID_SEC, SEC_REQ_SEED])
        resp = self._recv()
        self._assert_negative("TC07 During lockout → NRC 0x37",
                              resp, NRC_REQUIRED_TIME_DELAY)

        wait_s = SEC_LOCKOUT_SECS + 0.5
        print(f"    ⏰ Waiting {wait_s:.1f}s for lockout to expire...")
        time.sleep(wait_s)

        # After lockout, requestSeed should succeed again
        self._send([SID_SEC, SEC_REQ_SEED])
        resp2 = self._recv()
        if resp2 and resp2[0] == SID_SEC + 0x40:
            self._pass("TC07 Lockout expired — seed available again", "✓")
        else:
            nrc = f"0x{resp2[2]:02X}" if resp2 and len(resp2) >= 3 else "?"
            self._fail("TC07 Lockout expired", f"still blocked NRC={nrc}")

    def tc08_already_unlocked_zero_seed(self) -> None:
        """TC08: requestSeed when already unlocked → ECU returns 0x00000000."""
        self._switch_session(SESSION_DEFAULT)   # resets security lock
        self._switch_session(SESSION_EXTENDED)
        if not self._do_security_unlock():
            self._fail("TC08 setup: security unlock failed")
            return
        # Request seed again — should get zero seed (already-unlocked signal)
        self._send([SID_SEC, SEC_REQ_SEED])
        resp = self._recv()
        if resp is None or resp[0] == SID_NEG:
            self._fail("TC08 requestSeed when unlocked", "got NegResp")
            return
        if len(resp) >= 6:
            seed = ((resp[2] << 24) | (resp[3] << 16) |
                    (resp[4] << 8)  |  resp[5])
            if seed == 0:
                self._pass("TC08 Already unlocked → zero seed (0x00000000)", "✓")
            else:
                self._fail("TC08 Already unlocked → zero seed",
                           f"got 0x{seed:08X}")

    # GROUP 4: RoutineControl — Basic Usage ───────────────────────

    def tc09_routine_in_default_session(self) -> None:
        """TC09: startRoutine in defaultSession → NRC 0x22."""
        self._switch_session(SESSION_DEFAULT)
        self._send([SID_ROUTINE, RC_START, 0xFF, 0x00])   # RID_SELF_TEST
        resp = self._recv()
        self._assert_negative("TC09 startRoutine in default → NRC 0x22",
                              resp, NRC_CONDITIONS_NOT_CORRECT)

    def tc10_start_self_test(self) -> None:
        """TC10: Start self-test in extendedSession → running status."""
        self._switch_session(SESSION_EXTENDED)
        self._send([SID_ROUTINE, RC_START, 0xFF, 0x00])
        resp = self._recv()
        if not self._assert_positive("TC10 startRoutine SelfTest",
                                      resp, SID_ROUTINE, RC_START):
            return
        rid_echo    = (resp[2] << 8) | resp[3] if len(resp) >= 4 else 0
        status_byte = resp[4] if len(resp) >= 5 else 0
        if rid_echo == RID_SELF_TEST:
            self._pass("TC10 RID echoed correctly", f"0x{rid_echo:04X}")
        else:
            self._fail("TC10 RID echoed correctly", f"got 0x{rid_echo:04X}")
        if status_byte == RS_RUNNING:
            self._pass("TC10 Status = RS_RUNNING (0x01)", "✓")
        else:
            self._fail("TC10 Status = RS_RUNNING", f"got 0x{status_byte:02X}")

    def tc11_results_while_running(self) -> None:
        """TC11: requestRoutineResults immediately → running or completed status."""
        self._send([SID_ROUTINE, RC_RESULTS, 0xFF, 0x00])
        resp = self._recv()
        if (resp and resp[0] == SID_ROUTINE + 0x40
                and resp[1] == RC_RESULTS):
            status = resp[4] if len(resp) >= 5 else 0
            if status == RS_RUNNING:
                self._pass("TC11 requestResults while running → RS_RUNNING", "✓")
            elif status == RS_COMPLETED:
                self._pass("TC11 requestResults → completed (fast ECU OK)", "✓")
            else:
                self._fail("TC11 unexpected status", f"0x{status:02X}")
        else:
            nrc = resp[2] if resp and len(resp) >= 3 else "?"
            self._fail("TC11 requestResults while running", f"NRC=0x{nrc:02X}")

    def tc12_results_after_completion(self) -> None:
        """TC12: Start fresh, wait for completion, results contain result bytes."""
        self._send([SID_ROUTINE, RC_START, 0xFF, 0x00])
        self._recv()
        print("    ⏱  Waiting 300ms for SelfTest (200ms) to complete...")
        time.sleep(0.3)
        self._send([SID_ROUTINE, RC_RESULTS, 0xFF, 0x00])
        resp = self._recv()
        if (resp and resp[0] == SID_ROUTINE + 0x40
                and resp[1] == RC_RESULTS):
            status = resp[4] if len(resp) >= 5 else 0
            if status == RS_COMPLETED:
                result = bytes(resp[5:]) if len(resp) > 5 else b""
                self._pass("TC12 requestResults → RS_COMPLETED",
                           f"result={result.hex()}")
                if result == bytes([0x00]):
                    self._pass("TC12 SelfTest result = 0x00 (pass)", "✓")
                else:
                    self._fail("TC12 SelfTest result", f"unexpected {result.hex()}")
            else:
                self._fail("TC12 expected RS_COMPLETED", f"got 0x{status:02X}")
        else:
            nrc = resp[2] if resp and len(resp) >= 3 else "?"
            self._fail("TC12 requestResults after completion", f"NRC=0x{nrc:02X}")

    # GROUP 5: RoutineControl — Error Paths & Advanced ────────────

    def tc13_stop_routine(self) -> None:
        """TC13: Start DTC check, stop it mid-run → positive stopRoutine."""
        self._switch_session(SESSION_EXTENDED)
        self._send([SID_ROUTINE, RC_START, 0xDF, 0x00])   # RID_DTC_CHECK (150ms)
        self._recv()
        self._send([SID_ROUTINE, RC_STOP, 0xDF, 0x00])
        resp = self._recv()
        self._assert_positive("TC13 stopRoutine DTCCheck",
                               resp, SID_ROUTINE, RC_STOP)

    def tc14_unknown_rid(self) -> None:
        """TC14: Unknown RID → NRC 0x31 (requestOutOfRange)."""
        self._switch_session(SESSION_EXTENDED)
        self._send([SID_ROUTINE, RC_START, 0x99, 0x99])
        resp = self._recv()
        self._assert_negative("TC14 Unknown RID → NRC 0x31",
                              resp, NRC_REQUEST_OUT_OF_RANGE)

    def tc15_security_gated_routine_blocked(self) -> None:
        """TC15: Security-gated SensorCal without unlock → NRC 0x33."""
        # Ensure security is locked
        self._switch_session(SESSION_DEFAULT)
        self._switch_session(SESSION_EXTENDED)
        # Security is locked after cycling through default
        self._send([SID_ROUTINE, RC_START, 0x02, 0x03])   # RID_SENSOR_CAL
        resp = self._recv()
        self._assert_negative("TC15 Security-gated routine → NRC 0x33",
                              resp, NRC_SECURITY_ACCESS_DENIED)

    def tc16_full_security_gated_routine(self) -> None:
        """TC16: Full flow — SecurityAccess unlock → start SensorCal → results."""
        self._switch_session(SESSION_DEFAULT)
        self._switch_session(SESSION_EXTENDED)

        if not self._do_security_unlock():
            self._fail("TC16 SecurityAccess unlock failed")
            return
        self._pass("TC16 SecurityAccess unlocked", "✓")

        self._send([SID_ROUTINE, RC_START, 0x02, 0x03])   # RID_SENSOR_CAL
        resp = self._recv()
        if not self._assert_positive("TC16 startRoutine SensorCal (after unlock)",
                                      resp, SID_ROUTINE, RC_START):
            return

        print("    ⏱  Waiting 400ms for SensorCal (300ms) to complete...")
        time.sleep(0.4)

        self._send([SID_ROUTINE, RC_RESULTS, 0x02, 0x03])
        resp2 = self._recv()
        if (resp2 and resp2[0] == SID_ROUTINE + 0x40
                and resp2[1] == RC_RESULTS):
            status = resp2[4] if len(resp2) >= 5 else 0
            if status == RS_COMPLETED:
                result = bytes(resp2[5:]) if len(resp2) > 5 else b""
                if len(result) >= 2:
                    cal_offset = struct.unpack(">H", result[:2])[0]
                    self._pass("TC16 SensorCal completed",
                               f"calibration offset = {cal_offset}")
                else:
                    self._pass("TC16 SensorCal completed", "no result data")
            else:
                self._fail("TC16 SensorCal results", f"status=0x{status:02X}")
        else:
            nrc = resp2[2] if resp2 and len(resp2) >= 3 else "?"
            self._fail("TC16 SensorCal results", f"NRC=0x{nrc:02X}")

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
    print("\n" + "🔐🔑  " * 10)
    print("  Day 14 — UDS SecurityAccess (0x27) &")
    print("           RoutineControl (0x31) Simulator")
    print("🔐🔑  " * 10)

    ecu    = SimulatedECU()
    tester = UDSTester()
    ecu.start()
    time.sleep(0.1)

    banner("GROUP 1: SecurityAccess (0x27) — Session Gating")
    tester.tc01_seed_in_default_session()
    tester.tc02_seed_in_extended_session()

    banner("GROUP 2: SecurityAccess (0x27) — Key Exchange")
    tester.tc03_correct_key_unlocks()
    tester.tc04_sendkey_without_seed()

    banner("GROUP 3: SecurityAccess (0x27) — Brute-Force Protection")
    tester.tc05_wrong_key_nrc35()
    tester.tc06_lockout_after_three_fails()
    tester.tc07_lockout_period()
    tester.tc08_already_unlocked_zero_seed()

    banner("GROUP 4: RoutineControl (0x31) — Basic Usage")
    tester.tc09_routine_in_default_session()
    tester.tc10_start_self_test()
    tester.tc11_results_while_running()
    tester.tc12_results_after_completion()

    banner("GROUP 5: RoutineControl (0x31) — Error Paths & Advanced")
    tester.tc13_stop_routine()
    tester.tc14_unknown_rid()
    tester.tc15_security_gated_routine_blocked()
    tester.tc16_full_security_gated_routine()

    tester.print_summary()

    ecu.stop()
    tester.shutdown()


if __name__ == "__main__":
    main()
