"""
Day 12: UDS Session Control (0x10) & ECU Reset (0x11) Simulator
================================================================
Simulates a UDS-compliant ECU and a test client on a python-can
virtual bus. Exercises:
  - DiagnosticSessionControl (0x10): switch sessions, S3 timeout
  - ECUReset (0x11): hard/soft/keyOffOn, startup detection
  - Negative Response paths (wrong session, unknown sub-function)

No hardware needed.

Install:
    pip install python-can
"""

import can
import threading
import time

# ─── CONSTANTS ───────────────────────────────────────────────────────────────

CHANNEL      = "vcan0"
TESTER_TX_ID = 0x7E0   # Tester → ECU
TESTER_RX_ID = 0x7E8   # ECU → Tester

# Service IDs
SID_SESSION = 0x10
SID_RESET   = 0x11
SID_NEG     = 0x7F

# Session sub-functions
SESSION_DEFAULT     = 0x01
SESSION_PROGRAMMING = 0x02
SESSION_EXTENDED    = 0x03

# Reset sub-functions
RESET_HARD     = 0x01
RESET_KEYOFFON = 0x02
RESET_SOFT     = 0x03

# Negative Response Codes
NRC_SUBFUNC_NOT_SUPPORTED  = 0x12
NRC_CONDITIONS_NOT_CORRECT = 0x22
NRC_REQUEST_SEQUENCE_ERROR = 0x24


# ─── ISO-TP HELPERS ───────────────────────────────────────────────────────────

def build_single_frame(uds_bytes: list) -> bytes:
    """Wrap UDS bytes in an ISO-TP Single Frame envelope (max 7 UDS bytes)."""
    assert 1 <= len(uds_bytes) <= 7, "Single frame: max 7 UDS bytes"
    pci    = len(uds_bytes)
    padded = [pci] + uds_bytes
    padded += [0x00] * (8 - len(padded))
    return bytes(padded)


def parse_single_frame(data: bytes):
    """
    Extract UDS bytes from an ISO-TP Single Frame.
    Returns None if it is not a single frame (PCI high nibble != 0).
    """
    pci = data[0]
    if (pci & 0xF0) != 0x00:
        return None
    length = pci & 0x0F
    return list(data[1: 1 + length])


# ─── SIMULATED ECU ───────────────────────────────────────────────────────────

class SimulatedECU(threading.Thread):
    """
    A minimal UDS-compliant ECU that handles 0x10 and 0x11.
    Runs as a daemon thread; listens on TESTER_TX_ID, responds on TESTER_RX_ID.
    """

    S3_TIMEOUT_S = 5.0   # session keepalive timeout (ISO 14229 default = 5 s)

    def __init__(self):
        super().__init__(daemon=True, name="SimECU")
        self.bus          = can.Bus(interface="virtual", channel=CHANNEL)
        self.session      = SESSION_DEFAULT
        self.online       = True
        self.reboot_event = threading.Event()
        self._stop        = threading.Event()
        self._last_diag_t = time.monotonic()

    # ── Public control ────────────────────────────────────────────

    def stop(self):
        self._stop.set()
        self.bus.shutdown()

    # ── Response helpers ──────────────────────────────────────────

    def _send(self, uds_bytes: list) -> None:
        self.bus.send(can.Message(
            arbitration_id=TESTER_RX_ID,
            data=build_single_frame(uds_bytes),
            is_extended_id=False
        ))

    def _positive(self, sid: int, sub: int, extra: list = None) -> None:
        self._send([sid + 0x40, sub] + (extra or []))

    def _negative(self, sid: int, nrc: int) -> None:
        self._send([SID_NEG, sid, nrc])

    # ── Service: 0x10 DiagnosticSessionControl ────────────────────

    def _handle_session_control(self, sub: int) -> None:
        valid = {SESSION_DEFAULT, SESSION_PROGRAMMING, SESSION_EXTENDED}

        if sub not in valid:
            self._negative(SID_SESSION, NRC_SUBFUNC_NOT_SUPPORTED)
            return

        # Direct default → programming not allowed (must go via extended)
        if self.session == SESSION_DEFAULT and sub == SESSION_PROGRAMMING:
            self._negative(SID_SESSION, NRC_REQUEST_SEQUENCE_ERROR)
            return

        self.session      = sub
        self._last_diag_t = time.monotonic()

        # P2_server_max = 25 ms (0x0019), P2*_server_max = 500 ms (0x01F4)
        self._positive(SID_SESSION, sub, extra=[0x00, 0x19, 0x01, 0xF4])

    # ── Service: 0x11 ECUReset ────────────────────────────────────

    def _handle_ecu_reset(self, sub: int) -> None:
        valid = {RESET_HARD, RESET_KEYOFFON, RESET_SOFT}

        if sub not in valid:
            self._negative(SID_RESET, NRC_SUBFUNC_NOT_SUPPORTED)
            return

        # Positive response BEFORE going offline
        self._positive(SID_RESET, sub)

        if sub == RESET_SOFT:
            self.session = SESSION_DEFAULT   # soft reset: stay online, reset session
        else:
            self.online = False              # hard/keyOffOn: go offline for boot time
            self.reboot_event.set()

    # ── Main loop ─────────────────────────────────────────────────

    def run(self) -> None:
        while not self._stop.is_set():

            # S3 timer: revert to default if tester is silent too long
            if (self.session != SESSION_DEFAULT
                    and time.monotonic() - self._last_diag_t > self.S3_TIMEOUT_S):
                self.session = SESSION_DEFAULT

            # Reboot simulation
            if self.reboot_event.is_set():
                self.reboot_event.clear()
                time.sleep(0.3)            # simulated boot time: 300 ms
                self.session = SESSION_DEFAULT
                self.online  = True

            frame = self.bus.recv(timeout=0.05)
            if frame is None or frame.arbitration_id != TESTER_TX_ID:
                continue
            if not self.online:
                continue                   # ECU is still rebooting

            self._last_diag_t = time.monotonic()
            uds = parse_single_frame(bytes(frame.data))
            if uds is None or len(uds) < 2:
                continue

            sid, sub = uds[0], uds[1]

            if sid == SID_SESSION:
                self._handle_session_control(sub)
            elif sid == SID_RESET:
                self._handle_ecu_reset(sub)
            else:
                self._negative(sid, 0x11)   # serviceNotSupported


# ─── TESTER CLIENT ────────────────────────────────────────────────────────────

class UDSTester:
    """
    Sends UDS requests to the simulated ECU and validates responses.
    """

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
        """Receive response, transparently handling 0x78 RCRRP pending frames."""
        deadline = time.monotonic() + (timeout or self.RESPONSE_TIMEOUT_S)
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            frame = self.bus.recv(timeout=max(0.01, remaining))
            if frame is None or frame.arbitration_id != TESTER_RX_ID:
                continue
            uds = parse_single_frame(bytes(frame.data))
            if uds is None:
                continue
            # 0x78 = requestCorrectlyReceived-ResponsePending: not an error
            if len(uds) == 3 and uds[0] == SID_NEG and uds[2] == 0x78:
                print("    ⏳ 0x78 RCRRP — ECU still processing, extending wait...")
                deadline += 5.0
                continue
            return uds
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

    def _assert_positive(self, name: str, resp, expected_sid: int,
                         expected_sub: int) -> bool:
        if resp is None:
            self._fail(name, "no response (timeout)")
            return False
        if resp[0] == SID_NEG:
            nrc = resp[2] if len(resp) >= 3 else "?"
            self._fail(name, f"got NegResp NRC=0x{nrc:02X}")
            return False
        exp = expected_sid + 0x40
        if resp[0] != exp:
            self._fail(name, f"SID: expected 0x{exp:02X} got 0x{resp[0]:02X}")
            return False
        if resp[1] != expected_sub:
            self._fail(name, f"sub: expected 0x{expected_sub:02X} got 0x{resp[1]:02X}")
            return False
        self._pass(name, f"0x{resp[0]:02X} 0x{resp[1]:02X}")
        return True

    def _assert_negative(self, name: str, resp, expected_sid: int,
                         expected_nrc: int) -> bool:
        if resp is None:
            self._fail(name, "no response (timeout)")
            return False
        if resp[0] != SID_NEG:
            self._fail(name, f"expected NegResp, got 0x{resp[0]:02X}")
            return False
        actual_nrc = resp[2] if len(resp) >= 3 else 0
        if actual_nrc != expected_nrc:
            self._fail(name, f"NRC: expected 0x{expected_nrc:02X} "
                             f"got 0x{actual_nrc:02X}")
            return False
        self._pass(name, f"NRC=0x{actual_nrc:02X}")
        return True

    # ── Test cases ────────────────────────────────────────────────

    def tc01_default_to_extended(self) -> None:
        """Default → Extended: must succeed."""
        self._send([SID_SESSION, SESSION_EXTENDED])
        self._assert_positive("TC01 default→extended",
                               self._recv(), SID_SESSION, SESSION_EXTENDED)

    def tc02_default_to_programming_rejected(self) -> None:
        """Direct default → programming: must fail with 0x24."""
        # Reset to default first
        self._send([SID_SESSION, SESSION_DEFAULT])
        self._recv()

        self._send([SID_SESSION, SESSION_PROGRAMMING])
        self._assert_negative("TC02 default→programming (sequence error)",
                               self._recv(), SID_SESSION,
                               NRC_REQUEST_SEQUENCE_ERROR)

    def tc03_invalid_session_type(self) -> None:
        """Unknown session type: must fail with 0x12."""
        self._send([SID_SESSION, 0x99])
        self._assert_negative("TC03 invalid session type",
                               self._recv(), SID_SESSION,
                               NRC_SUBFUNC_NOT_SUPPORTED)

    def tc04_extended_to_programming(self) -> None:
        """Correct escalation: default → extended → programming."""
        # Go to extended first
        self._send([SID_SESSION, SESSION_EXTENDED])
        self._recv()
        # Now programming should work
        self._send([SID_SESSION, SESSION_PROGRAMMING])
        self._assert_positive("TC04 extended→programming",
                               self._recv(), SID_SESSION, SESSION_PROGRAMMING)

    def tc05_hard_reset(self, ecu: SimulatedECU) -> None:
        """Hard reset: positive response, ECU goes offline, comes back in spec time."""
        self._send([SID_SESSION, SESSION_DEFAULT])
        self._recv()

        self._send([SID_RESET, RESET_HARD])
        resp = self._recv()
        if not self._assert_positive("TC05 hard reset (pos response)",
                                      resp, SID_RESET, RESET_HARD):
            return

        time.sleep(0.1)
        if ecu.online:
            self._fail("TC05 ECU offline after hard reset", "ECU still online!")
        else:
            self._pass("TC05 ECU offline after hard reset", "correctly offline")

        # Poll for restart
        t0          = time.monotonic()
        boot_budget = 2.0   # spec: ECU must be back within 2 s
        came_back   = False
        while time.monotonic() - t0 < boot_budget:
            if ecu.online:
                boot_ms = int((time.monotonic() - t0) * 1000)
                self._pass("TC05 ECU restart within 2 s",
                           f"online after ~{boot_ms} ms")
                came_back = True
                break
            time.sleep(0.05)

        if not came_back:
            self._fail("TC05 ECU restart within 2 s", "did not come back!")
            return

        # After hard reset, session must be default (programming should fail)
        self._send([SID_SESSION, SESSION_PROGRAMMING])
        self._assert_negative("TC05 session is default after restart",
                               self._recv(), SID_SESSION,
                               NRC_REQUEST_SEQUENCE_ERROR)

    def tc06_soft_reset(self) -> None:
        """Soft reset: ECU stays online, session reverts to default."""
        self._send([SID_SESSION, SESSION_EXTENDED])
        self._recv()

        self._send([SID_RESET, RESET_SOFT])
        resp = self._recv()
        self._assert_positive("TC06 soft reset (pos response)",
                               resp, SID_RESET, RESET_SOFT)

        # Session must be back to default — programming should be rejected
        self._send([SID_SESSION, SESSION_PROGRAMMING])
        self._assert_negative("TC06 session is default after soft reset",
                               self._recv(), SID_SESSION,
                               NRC_REQUEST_SEQUENCE_ERROR)

    def tc07_invalid_reset_type(self) -> None:
        """Unknown reset sub-function: must fail with 0x12."""
        self._send([SID_RESET, 0xAA])
        self._assert_negative("TC07 invalid reset type",
                               self._recv(), SID_RESET,
                               NRC_SUBFUNC_NOT_SUPPORTED)

    def tc08_s3_timer_expiry(self, ecu: SimulatedECU) -> None:
        """S3 timer: ECU reverts to default session after silence."""
        self._send([SID_SESSION, SESSION_EXTENDED])
        resp = self._recv()
        if not self._assert_positive("TC08 enter extended (S3 setup)",
                                      resp, SID_SESSION, SESSION_EXTENDED):
            return

        wait = ecu.S3_TIMEOUT_S + 0.5
        print(f"\n    ⏰ Silent for {wait:.1f} s — waiting for S3 timer to fire...")
        time.sleep(wait)

        # Now programming should be rejected because ECU is back to default
        self._send([SID_SESSION, SESSION_PROGRAMMING])
        self._assert_negative("TC08 S3 reverted to default (prog rejected)",
                               self._recv(), SID_SESSION,
                               NRC_REQUEST_SEQUENCE_ERROR)

    # ── Summary ───────────────────────────────────────────────────

    def print_summary(self) -> None:
        total = len(self.passed) + len(self.failed)
        print(f"\n{'='*62}")
        print(f"  TEST SUMMARY: {len(self.passed)}/{total} passed, "
              f"{len(self.failed)} failed")
        print(f"{'='*62}")
        if self.failed:
            print("\n  Failed:")
            for f in self.failed:
                print(f"    {f.strip()}")


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def banner(title: str) -> None:
    print(f"\n{'─'*62}\n  {title}\n{'─'*62}")


def main() -> None:
    print("\n" + "🩺 " * 20)
    print("  Day 12 — UDS Session Control & ECU Reset Simulator")
    print("  ISO 14229 over python-can virtual bus")
    print("🩺 " * 20)

    ecu = SimulatedECU()
    ecu.start()
    time.sleep(0.1)   # let ECU thread start

    tester = UDSTester()

    banner("GROUP 1: DiagnosticSessionControl (0x10)")
    tester.tc01_default_to_extended()
    tester.tc02_default_to_programming_rejected()
    tester.tc03_invalid_session_type()
    tester.tc04_extended_to_programming()

    banner("GROUP 2: ECUReset (0x11)")
    tester.tc05_hard_reset(ecu)
    tester.tc06_soft_reset()
    tester.tc07_invalid_reset_type()

    banner("GROUP 3: S3 Session Timeout (takes ~5.5 s)")
    tester.tc08_s3_timer_expiry(ecu)

    tester.print_summary()

    ecu.stop()
    tester.shutdown()


if __name__ == "__main__":
    main()
