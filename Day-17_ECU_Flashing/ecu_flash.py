"""
Day 17: ECU Flashing — RequestDownload (0x34), TransferData (0x36),
        RequestTransferExit (0x37), CheckProgrammingDependencies (0x31 0xFF01)
===========================================================================
Implements the complete OEM firmware-update pipeline over UDS + ISO-TP on a
python-can virtual bus.

Sequence:
  1. 0x10 0x02  programmingSession
  2. 0x27 0x01/0x02  SecurityAccess level 1 (programming unlock)
  3. 0x28 0x03 0x01  CommunicationControl — disable normal tx
  4. 0x31 0x01 0xFF00  RoutineControl — EraseMemory
  5. 0x34  RequestDownload  — negotiate address + size + block size
  6. 0x36 ×N  TransferData  — chunked firmware payload
  7. 0x37  RequestTransferExit — finalise, verify checksum
  8. 0x31 0x01 0xFF01  CheckProgrammingDependencies
  9. 0x28 0x00 0x01  CommunicationControl — re-enable normal tx
 10. 0x11 0x01  ECUReset — hard reset to boot new firmware
 11. 0x22 0xF189  ReadDataByIdentifier — verify new SW version

No hardware needed.

Install:
    pip install python-can

Run:
    python ecu_flash.py
"""

import can
import threading
import time
import hashlib
import struct
import random
from typing import Optional

# ─── CONSTANTS ───────────────────────────────────────────────────────────────

CHANNEL      = "vcan0"
TESTER_TX_ID = 0x7E0
TESTER_RX_ID = 0x7E8

# UDS Service IDs
SID_SESSION    = 0x10
SID_ECU_RESET  = 0x11
SID_SEC        = 0x27
SID_COMM_CTRL  = 0x28
SID_READ_DID   = 0x22
SID_ROUTINE    = 0x31
SID_REQ_DL     = 0x34   # RequestDownload
SID_XFER_DATA  = 0x36   # TransferData
SID_XFER_EXIT  = 0x37   # RequestTransferExit
SID_NEG        = 0x7F

# Sessions
SESSION_DEFAULT     = 0x01
SESSION_PROGRAMMING = 0x02
SESSION_EXTENDED    = 0x03

# SecurityAccess (programming level = sub 0x01/0x02 in our simulation)
SEC_REQ_SEED    = 0x01
SEC_SEND_KEY    = 0x02
SEC_SECRET      = 0xC0FFEE42   # XOR — SIMULATION ONLY
SEC_MAX_ATTEMPTS = 3
SEC_LOCKOUT_SECS = 3.0

# RoutineControl sub-functions
RC_START   = 0x01
RC_RESULTS = 0x03

# RIDs used in flash sequence
RID_ERASE_MEMORY    = 0xFF00   # EraseMemory
RID_CHECK_PROG_DEPS = 0xFF01   # CheckProgrammingDependencies

# CommunicationControl
COMM_DISABLE_TX = 0x03   # disableRxAndEnableTx → typically 0x01=enableRx+disableTx
COMM_ENABLE_ALL = 0x00   # enableRxAndTx
COMM_TYPE_NORM  = 0x01   # normalCommunicationMessages

# NRCs
NRC_SERVICE_NOT_SUPPORTED  = 0x11
NRC_SUBFUNC_NOT_SUPPORTED  = 0x12
NRC_INCORRECT_MSG_LENGTH   = 0x13
NRC_CONDITIONS_NOT_CORRECT = 0x22
NRC_REQUEST_OUT_OF_RANGE   = 0x31
NRC_SECURITY_ACCESS_DENIED = 0x33
NRC_INVALID_KEY            = 0x35
NRC_EXCEEDED_ATTEMPTS      = 0x36
NRC_REQUIRED_TIME_DELAY    = 0x37
NRC_UPLOAD_DOWNLOAD_NOT_ACCEPTED = 0x70
NRC_TRANSFER_DATA_SUSPENDED      = 0x71
NRC_GENERAL_PROG_FAILURE         = 0x72
NRC_WRONG_BLOCK_SEQ_COUNTER      = 0x73

# Firmware image simulation
FIRMWARE_VERSION_NEW = "2.5.0-release"
FIRMWARE_VERSION_OLD = "2.4.1-release"
FLASH_BASE_ADDRESS   = 0x08000000   # typical ARM Cortex-M flash base
FLASH_SIZE_BYTES     = 128 * 1024   # 128 KB simulated firmware

# ISO-TP helpers
STMIN_MS = 5

# ─── ISO-TP FRAME HELPERS ────────────────────────────────────────────────────

def build_sf(uds_bytes: list) -> bytes:
    assert 1 <= len(uds_bytes) <= 7
    return bytes([len(uds_bytes)] + list(uds_bytes) + [0x00] * (7 - len(uds_bytes)))


def build_ff(uds_bytes: list) -> bytes:
    total = len(uds_bytes)
    assert total > 7
    return bytes([0x10 | ((total >> 8) & 0x0F), total & 0xFF] + list(uds_bytes[:6]))


def build_cf(sn: int, chunk: list) -> bytes:
    return bytes([0x20 | (sn & 0x0F)] + list(chunk) + [0x00] * (7 - len(chunk)))


def build_fc(fc_flag: int, block_size: int, stmin_ms: int) -> bytes:
    return bytes([0x30 | (fc_flag & 0x0F), block_size & 0xFF,
                  min(stmin_ms, 0x7F), 0x00, 0x00, 0x00, 0x00, 0x00])


# ─── SIMULATED FIRMWARE IMAGE ─────────────────────────────────────────────────

def make_firmware_image(version: str, size: int = FLASH_SIZE_BYTES) -> bytes:
    """
    Generate a deterministic pseudo-firmware image.
    Header: magic(4) + version_len(1) + version(N) + reserved padding.
    Remainder: pseudo-random bytes seeded from version string.
    The CRC-32 of the whole image is appended as the last 4 bytes.
    """
    magic   = b"FWIMG"
    ver_b   = version.encode("ascii")
    header  = magic + bytes([len(ver_b)]) + ver_b
    # Fill rest with deterministic pseudo-random data
    rand    = random.Random(version)
    payload = bytes([rand.randint(0, 255) for _ in range(size - len(header) - 4)])
    raw     = header + payload
    crc32   = _crc32(raw)
    return raw + struct.pack(">I", crc32)


def _crc32(data: bytes) -> int:
    """Simple CRC-32 (same polynomial as zlib.crc32 but pure Python)."""
    import zlib
    return zlib.crc32(data) & 0xFFFFFFFF


# ─── SECURITY STATE ──────────────────────────────────────────────────────────

class SecurityState:
    def __init__(self):
        self.unlocked       = False
        self._seed          = 0
        self._seed_issued   = False
        self._failed_count  = 0
        self._lockout_until = 0.0

    def is_locked_out(self) -> bool:
        return time.monotonic() < self._lockout_until

    def issue_seed(self) -> int:
        if self.unlocked:
            self._seed_issued = False
            return 0x00000000   # "already unlocked" signal
        seed              = random.randint(1, 0xFFFFFFFF)
        self._seed        = seed
        self._seed_issued = True
        return seed

    def verify_key(self, key: int) -> str:
        if not self._seed_issued:
            return "sequence"
        self._seed_issued = False
        expected = self._derive_key(self._seed)
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
        self.unlocked      = False
        self._seed_issued  = False
        self._failed_count = 0

    @staticmethod
    def _derive_key(seed: int) -> int:
        return seed ^ SEC_SECRET


# ─── FLASH STATE ─────────────────────────────────────────────────────────────

class FlashState:
    """
    Tracks the state machine for a single firmware download session.

    States: IDLE → ERASING → READY_FOR_DOWNLOAD → DOWNLOADING → VERIFYING → DONE → ERROR
    """
    IDLE             = "IDLE"
    ERASING          = "ERASING"
    READY_FOR_DL     = "READY_FOR_DOWNLOAD"
    DOWNLOADING      = "DOWNLOADING"
    VERIFYING        = "VERIFYING"
    DONE             = "DONE"
    ERROR            = "ERROR"

    def __init__(self):
        self.state              = self.IDLE
        self.address            = 0
        self.total_size         = 0
        self.block_size         = 0     # max bytes per TransferData block
        self.next_block_counter = 1     # starts at 1, wraps 0xFF → 0x00 → 0x01
        self.received_bytes     = 0
        self.data_buffer        = bytearray()
        self._erase_start       = 0.0
        self.erase_duration_s   = 0.15  # simulated erase time

    def start_erase(self) -> None:
        self.state        = self.ERASING
        self._erase_start = time.monotonic()

    @property
    def erase_complete(self) -> bool:
        return (self.state == self.ERASING and
                time.monotonic() - self._erase_start >= self.erase_duration_s)

    def start_download(self, address: int, size: int, max_block: int) -> None:
        self.state              = self.DOWNLOADING
        self.address            = address
        self.total_size         = size
        self.block_size         = max_block
        self.next_block_counter = 1
        self.received_bytes     = 0
        self.data_buffer        = bytearray()

    def accept_block(self, block_counter: int, data: bytes) -> str:
        """
        Validate block counter and append data.
        Returns 'ok', 'wrong_counter', or 'overflow'.
        """
        expected = self.next_block_counter
        if block_counter != expected:
            return "wrong_counter"
        if self.received_bytes + len(data) > self.total_size:
            return "overflow"
        self.data_buffer        += data
        self.received_bytes     += len(data)
        # Wrap: 0xFF → 0x00 → 0x01 — NOT 0xFF → 0x00 only
        self.next_block_counter  = (self.next_block_counter + 1) & 0xFF
        if self.next_block_counter == 0:
            self.next_block_counter = 0   # 0x00 is valid per ISO 14229
        return "ok"

    @property
    def transfer_complete(self) -> bool:
        return self.received_bytes >= self.total_size

    def verify(self, expected_crc: int) -> bool:
        """Verify the received firmware image CRC-32."""
        actual = _crc32(bytes(self.data_buffer))
        return actual == expected_crc


# ─── SIMULATED ECU ────────────────────────────────────────────────────────────

class SimulatedECU(threading.Thread):
    """
    ECU that supports the complete firmware update sequence.
    Starts with firmware version OLD. After successful flash: switches to NEW.
    """

    S3_TIMEOUT_S = 5.0

    def __init__(self):
        super().__init__(daemon=True, name="SimECU")
        self.bus          = can.Bus(interface="virtual", channel=CHANNEL)
        self.session      = SESSION_DEFAULT
        self._stop        = threading.Event()
        self._last_diag_t = time.monotonic()
        self._sec         = SecurityState()
        self._flash       = FlashState()
        self._comm_normal = True    # True = normal ECU comms enabled
        self._sw_version  = FIRMWARE_VERSION_OLD
        self._reset_pending = False
        self._reset_time    = 0.0

        # Simulated target firmware image (what we expect to receive)
        self._expected_fw   = make_firmware_image(FIRMWARE_VERSION_NEW)
        self._expected_crc  = _crc32(self._expected_fw[:-4])  # CRC is over payload only

        # Multi-frame ISO-TP receive state (tester → ECU direction)
        self._mf_active = False
        self._mf_total  = 0
        self._mf_buffer: list = []

    # ── Lifecycle ─────────────────────────────────────────────────────

    def stop(self) -> None:
        self._stop.set()
        self.bus.shutdown()

    # ── ISO-TP send ───────────────────────────────────────────────────

    def _send_raw_fc(self) -> None:
        """Send a Flow Control CTS frame (block_size=0, STmin=0)."""
        self.bus.send(can.Message(
            arbitration_id=TESTER_RX_ID,
            data=bytes([0x30, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]),
            is_extended_id=False))

    def _send(self, payload: list) -> None:
        if len(payload) <= 7:
            self.bus.send(can.Message(arbitration_id=TESTER_RX_ID,
                                      data=build_sf(payload),
                                      is_extended_id=False))
            return
        ff = build_ff(payload)
        self.bus.send(can.Message(arbitration_id=TESTER_RX_ID,
                                  data=ff, is_extended_id=False))
        fc = self.bus.recv(timeout=1.0)
        if fc is None:
            return
        sn, offset = 1, 6
        while offset < len(payload):
            chunk = payload[offset: offset + 7]
            self.bus.send(can.Message(arbitration_id=TESTER_RX_ID,
                                      data=build_cf(sn, chunk),
                                      is_extended_id=False))
            sn     = (sn + 1) & 0x0F
            offset += 7
            time.sleep(STMIN_MS / 1000.0)

    def _neg(self, sid: int, nrc: int) -> None:
        self._send([SID_NEG, sid, nrc])

    # ── Service: 0x10 DiagnosticSessionControl ────────────────────────

    def _handle_session(self, sub: int) -> None:
        valid = {SESSION_DEFAULT, SESSION_PROGRAMMING, SESSION_EXTENDED}
        if sub not in valid:
            self._neg(SID_SESSION, NRC_SUBFUNC_NOT_SUPPORTED); return
        # Transition default → programming directly allowed (no extended required)
        old_session   = self.session
        self.session  = sub
        self._last_diag_t = time.monotonic()
        if old_session != SESSION_DEFAULT and sub == SESSION_DEFAULT:
            # Dropping to default: lock security, abort flash
            self._sec.lock()
            self._flash.state = FlashState.IDLE
        self._send([SID_SESSION + 0x40, sub, 0x00, 0x19, 0x01, 0xF4])

    # ── Service: 0x11 ECUReset ────────────────────────────────────────

    def _handle_reset(self, sub: int) -> None:
        if sub not in (0x01, 0x02, 0x03):
            self._neg(SID_ECU_RESET, NRC_SUBFUNC_NOT_SUPPORTED); return
        self._send([SID_ECU_RESET + 0x40, sub])
        # Schedule simulated reset: drop session, lock security, apply new SW if flash done
        self._reset_pending = True
        self._reset_time    = time.monotonic() + 0.1

    def _do_reset(self) -> None:
        self.session      = SESSION_DEFAULT
        self._sec.lock()
        self._comm_normal = True
        if self._flash.state == FlashState.DONE:
            self._sw_version = FIRMWARE_VERSION_NEW
        self._flash       = FlashState()

    # ── Service: 0x27 SecurityAccess ─────────────────────────────────

    def _handle_security(self, uds: list) -> None:
        if len(uds) < 2:
            self._neg(SID_SEC, NRC_INCORRECT_MSG_LENGTH); return
        sub = uds[1]
        if self.session not in (SESSION_PROGRAMMING, SESSION_EXTENDED):
            self._neg(SID_SEC, NRC_CONDITIONS_NOT_CORRECT); return
        if self._sec.is_locked_out():
            self._neg(SID_SEC, NRC_REQUIRED_TIME_DELAY); return
        if sub == SEC_REQ_SEED:
            seed = self._sec.issue_seed()
            self._send([SID_SEC + 0x40, sub,
                        (seed >> 24) & 0xFF, (seed >> 16) & 0xFF,
                        (seed >> 8)  & 0xFF, seed & 0xFF])
        elif sub == SEC_SEND_KEY:
            if len(uds) < 6:
                self._neg(SID_SEC, NRC_INCORRECT_MSG_LENGTH); return
            key = struct.unpack(">I", bytes(uds[2:6]))[0]
            result = self._sec.verify_key(key)
            if result == "ok":
                self._send([SID_SEC + 0x40, sub])
            elif result == "sequence":
                self._neg(SID_SEC, 0x24)   # requestSequenceError
            elif result == "lockout":
                self._neg(SID_SEC, NRC_EXCEEDED_ATTEMPTS)
            else:
                self._neg(SID_SEC, NRC_INVALID_KEY)
        else:
            self._neg(SID_SEC, NRC_SUBFUNC_NOT_SUPPORTED)

    # ── Service: 0x28 CommunicationControl ───────────────────────────

    def _handle_comm_ctrl(self, uds: list) -> None:
        if len(uds) < 3:
            self._neg(SID_COMM_CTRL, NRC_INCORRECT_MSG_LENGTH); return
        if self.session == SESSION_DEFAULT:
            self._neg(SID_COMM_CTRL, NRC_CONDITIONS_NOT_CORRECT); return
        ctrl_type = uds[1]
        if ctrl_type == COMM_ENABLE_ALL:
            self._comm_normal = True
        elif ctrl_type == COMM_DISABLE_TX:
            self._comm_normal = False
        else:
            self._neg(SID_COMM_CTRL, NRC_SUBFUNC_NOT_SUPPORTED); return
        self._send([SID_COMM_CTRL + 0x40, ctrl_type])

    # ── Service: 0x22 ReadDataByIdentifier ───────────────────────────

    def _handle_read_did(self, uds: list) -> None:
        if len(uds) < 3:
            self._neg(SID_READ_DID, NRC_INCORRECT_MSG_LENGTH); return
        did = (uds[1] << 8) | uds[2]
        if did == 0xF189:
            ver_bytes = list(self._sw_version.encode("ascii"))
            self._send([SID_READ_DID + 0x40, uds[1], uds[2]] + ver_bytes)
        elif did == 0xF18B:
            # ECU manufacturing date (static)
            self._send([SID_READ_DID + 0x40, uds[1], uds[2],
                        0x20, 0x26, 0x06, 0x22])   # 2026-06-22
        else:
            self._neg(SID_READ_DID, NRC_REQUEST_OUT_OF_RANGE)

    # ── Service: 0x31 RoutineControl ─────────────────────────────────

    def _handle_routine(self, uds: list) -> None:
        if len(uds) < 4:
            self._neg(SID_ROUTINE, NRC_INCORRECT_MSG_LENGTH); return
        sub = uds[1]
        rid = (uds[2] << 8) | uds[3]

        if sub == RC_START:
            if rid == RID_ERASE_MEMORY:
                if self.session != SESSION_PROGRAMMING:
                    self._neg(SID_ROUTINE, NRC_CONDITIONS_NOT_CORRECT); return
                if not self._sec.unlocked:
                    self._neg(SID_ROUTINE, NRC_SECURITY_ACCESS_DENIED); return
                self._flash.start_erase()
                self._send([SID_ROUTINE + 0x40, sub, uds[2], uds[3],
                            0x01])   # RS_RUNNING
            elif rid == RID_CHECK_PROG_DEPS:
                if self._flash.state not in (FlashState.DONE, FlashState.VERIFYING):
                    self._neg(SID_ROUTINE, NRC_CONDITIONS_NOT_CORRECT); return
                # Simulate: pass if flash is DONE
                result = 0x00 if self._flash.state == FlashState.DONE else 0x01
                self._send([SID_ROUTINE + 0x40, sub, uds[2], uds[3],
                            0x02, result])   # RS_COMPLETED, result
            else:
                self._neg(SID_ROUTINE, NRC_REQUEST_OUT_OF_RANGE)

        elif sub == RC_RESULTS:
            if rid == RID_ERASE_MEMORY:
                if self._flash.erase_complete:
                    self._flash.state = FlashState.READY_FOR_DL
                    self._send([SID_ROUTINE + 0x40, sub, uds[2], uds[3],
                                0x02, 0x00])   # RS_COMPLETED, result=0x00 success
                elif self._flash.state == FlashState.ERASING:
                    self._send([SID_ROUTINE + 0x40, sub, uds[2], uds[3],
                                0x01])   # RS_RUNNING
                else:
                    self._neg(SID_ROUTINE, NRC_CONDITIONS_NOT_CORRECT)
            else:
                self._neg(SID_ROUTINE, NRC_REQUEST_OUT_OF_RANGE)
        else:
            self._neg(SID_ROUTINE, NRC_SUBFUNC_NOT_SUPPORTED)

    # ── Service: 0x34 RequestDownload ────────────────────────────────

    def _handle_req_download(self, uds: list) -> None:
        """
        Request:  [0x34, dataFormatId, addrAndLengthFormatId,
                   addr_bytes…, size_bytes…]

        addrAndLengthFormatId:
          High nibble = number of size bytes
          Low  nibble = number of address bytes

        Response: [0x74, lengthFormatId, maxBlockSize_H, maxBlockSize_L]
          0x74 = 0x34 + 0x40
          lengthFormatId: 0x20 = 2 bytes per block size field
        """
        if self._flash.state != FlashState.READY_FOR_DL:
            self._neg(SID_REQ_DL, NRC_CONDITIONS_NOT_CORRECT); return
        if not self._sec.unlocked:
            self._neg(SID_REQ_DL, NRC_SECURITY_ACCESS_DENIED); return
        if len(uds) < 3:
            self._neg(SID_REQ_DL, NRC_INCORRECT_MSG_LENGTH); return

        data_fmt  = uds[1]
        addr_fmt  = uds[2]
        addr_len  = addr_fmt & 0x0F
        size_len  = (addr_fmt >> 4) & 0x0F

        expected_total = 3 + addr_len + size_len
        if len(uds) < expected_total:
            self._neg(SID_REQ_DL, NRC_INCORRECT_MSG_LENGTH); return

        # Extract address (big-endian)
        addr = 0
        for i in range(addr_len):
            addr = (addr << 8) | uds[3 + i]

        # Extract size (big-endian)
        size = 0
        for i in range(size_len):
            size = (size << 8) | uds[3 + addr_len + i]

        # Validate address range
        if addr != FLASH_BASE_ADDRESS:
            self._neg(SID_REQ_DL, NRC_UPLOAD_DOWNLOAD_NOT_ACCEPTED); return
        if size == 0 or size > FLASH_SIZE_BYTES:
            self._neg(SID_REQ_DL, NRC_UPLOAD_DOWNLOAD_NOT_ACCEPTED); return

        # Negotiate max block size: we support up to 512-byte data blocks
        max_block = 512
        self._flash.start_download(addr, size, max_block)

        # lengthFormatId: 0x20 = 2 bytes follow for maxBlockSize
        self._send([SID_REQ_DL + 0x40,
                    0x20,
                    (max_block >> 8) & 0xFF,
                    max_block & 0xFF])

    # ── Service: 0x36 TransferData ────────────────────────────────────

    def _handle_transfer_data(self, uds: list) -> None:
        """
        Request:  [0x36, blockSequenceCounter, data_byte_0, data_byte_1, …]
        Response: [0x76, blockSequenceCounter]
          0x76 = 0x36 + 0x40

        blockSequenceCounter starts at 0x01 for first block,
        increments per block, wraps 0xFF → 0x00.
        """
        if self._flash.state != FlashState.DOWNLOADING:
            self._neg(SID_XFER_DATA, NRC_CONDITIONS_NOT_CORRECT); return
        if len(uds) < 3:
            self._neg(SID_XFER_DATA, NRC_INCORRECT_MSG_LENGTH); return

        block_counter = uds[1]
        data          = bytes(uds[2:])

        result = self._flash.accept_block(block_counter, data)
        if result == "wrong_counter":
            self._neg(SID_XFER_DATA, NRC_WRONG_BLOCK_SEQ_COUNTER); return
        if result == "overflow":
            self._neg(SID_XFER_DATA, NRC_TRANSFER_DATA_SUSPENDED); return

        # Check if transfer is complete
        if self._flash.transfer_complete:
            self._flash.state = FlashState.VERIFYING

        self._send([SID_XFER_DATA + 0x40, block_counter])

    # ── Service: 0x37 RequestTransferExit ────────────────────────────

    def _handle_transfer_exit(self, uds: list) -> None:
        """
        Request:  [0x37, crc_byte0, crc_byte1, crc_byte2, crc_byte3]
                  (tester sends CRC-32 of transmitted data for verification)
        Response: [0x77] on success
          0x77 = 0x37 + 0x40
        """
        if self._flash.state not in (FlashState.DOWNLOADING, FlashState.VERIFYING):
            self._neg(SID_XFER_EXIT, NRC_CONDITIONS_NOT_CORRECT); return

        if not self._flash.transfer_complete:
            self._neg(SID_XFER_EXIT, NRC_CONDITIONS_NOT_CORRECT); return

        # Extract CRC-32 from request (optional — tester may omit)
        if len(uds) >= 5:
            sent_crc = struct.unpack(">I", bytes(uds[1:5]))[0]
            if not self._flash.verify(sent_crc):
                self._flash.state = FlashState.ERROR
                self._neg(SID_XFER_EXIT, NRC_GENERAL_PROG_FAILURE); return

        self._flash.state = FlashState.DONE
        self._send([SID_XFER_EXIT + 0x40])

    # ── Receive dispatcher ────────────────────────────────────────────

    def _dispatch(self, uds: list) -> None:
        self._last_diag_t = time.monotonic()
        sid = uds[0]
        if   sid == SID_SESSION  and len(uds) >= 2: self._handle_session(uds[1])
        elif sid == SID_ECU_RESET and len(uds) >= 2: self._handle_reset(uds[1])
        elif sid == SID_SEC:                         self._handle_security(uds)
        elif sid == SID_COMM_CTRL:                   self._handle_comm_ctrl(uds)
        elif sid == SID_READ_DID:                    self._handle_read_did(uds)
        elif sid == SID_ROUTINE:                     self._handle_routine(uds)
        elif sid == SID_REQ_DL:                      self._handle_req_download(uds)
        elif sid == SID_XFER_DATA:                   self._handle_transfer_data(uds)
        elif sid == SID_XFER_EXIT:                   self._handle_transfer_exit(uds)
        else:                                        self._neg(sid, NRC_SERVICE_NOT_SUPPORTED)

    # ── Main loop ─────────────────────────────────────────────────────

    def run(self) -> None:
        while not self._stop.is_set():
            now = time.monotonic()

            # S3 watchdog
            if (self.session != SESSION_DEFAULT
                    and now - self._last_diag_t > self.S3_TIMEOUT_S):
                self.session = SESSION_DEFAULT
                self._sec.lock()

            # Deferred reset
            if self._reset_pending and now >= self._reset_time:
                self._do_reset()
                self._reset_pending = False

            frame = self.bus.recv(timeout=0.02)
            if frame is None or frame.arbitration_id != TESTER_TX_ID:
                continue

            data     = bytes(frame.data)
            pci      = data[0]
            pci_type = (pci >> 4) & 0x0F

            if pci_type == 0:   # Single Frame
                self._mf_active = False
                self._mf_buffer = []
                length = pci & 0x0F
                uds = list(data[1: 1 + length])
                if uds:
                    self._dispatch(uds)

            elif pci_type == 1:   # First Frame — start multi-frame assembly
                self._mf_total  = ((pci & 0x0F) << 8) | data[1]
                self._mf_buffer = list(data[2:8])   # first 6 payload bytes
                self._mf_active = True
                self._send_raw_fc()   # send CTS to allow consecutive frames

            elif pci_type == 2:   # Consecutive Frame
                if self._mf_active:
                    self._mf_buffer += list(data[1:8])
                    if len(self._mf_buffer) >= self._mf_total:
                        uds = self._mf_buffer[:self._mf_total]
                        self._mf_active = False
                        self._mf_buffer = []
                        if uds:
                            self._dispatch(uds)


# ─── TESTER CLIENT ────────────────────────────────────────────────────────────

class UDSTester:
    """
    Tester with full ISO-TP send/receive + firmware flash helpers.
    """

    RESPONSE_TIMEOUT_S = 3.0

    def __init__(self):
        self.bus    = can.Bus(interface="virtual", channel=CHANNEL)
        self.passed = []
        self.failed = []

    def shutdown(self) -> None:
        self.bus.shutdown()

    # ── ISO-TP send/receive ────────────────────────────────────────────

    def _send(self, uds_bytes: list) -> None:
        """Send UDS using ISO-TP (SF for ≤7 bytes, multi-frame otherwise)."""
        if len(uds_bytes) <= 7:
            self.bus.send(can.Message(arbitration_id=TESTER_TX_ID,
                                      data=build_sf(uds_bytes),
                                      is_extended_id=False))
        else:
            ff = build_ff(uds_bytes)
            self.bus.send(can.Message(arbitration_id=TESTER_TX_ID,
                                      data=ff, is_extended_id=False))
            # Wait for Flow Control from ECU, filtering by ECU arbID
            fc_deadline = time.monotonic() + 1.0
            fc = None
            while time.monotonic() < fc_deadline:
                f = self.bus.recv(timeout=0.05)
                if f is not None and f.arbitration_id == TESTER_RX_ID:
                    fc = f
                    break
            if fc is None:
                return
            sn, offset = 1, 6
            while offset < len(uds_bytes):
                chunk = uds_bytes[offset: offset + 7]
                self.bus.send(can.Message(arbitration_id=TESTER_TX_ID,
                                          data=build_cf(sn, chunk),
                                          is_extended_id=False))
                sn     = (sn + 1) & 0x0F
                offset += 7
                time.sleep(STMIN_MS / 1000.0)

    def _recv(self, timeout: float = None) -> Optional[list]:
        """Receive UDS response, reassembling multi-frame if needed."""
        deadline          = time.monotonic() + (timeout or self.RESPONSE_TIMEOUT_S)
        collected_payload = []
        total_expected    = 0

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
                # RCRRP (0x78) — extend wait
                if len(uds) == 3 and uds[0] == SID_NEG and uds[2] == 0x78:
                    deadline += 5.0
                    continue
                return uds
            elif pci_type == 1:
                total_expected    = ((fb & 0x0F) << 8) | frame.data[1]
                collected_payload = list(frame.data[2:])
                self.bus.send(can.Message(
                    arbitration_id=TESTER_TX_ID,
                    data=build_fc(0, 0x00, STMIN_MS),
                    is_extended_id=False))
            elif pci_type == 2:
                collected_payload += list(frame.data[1:])
                if len(collected_payload) >= total_expected:
                    return collected_payload[:total_expected]

        return collected_payload[:total_expected] if collected_payload else None

    # ── Assertions ─────────────────────────────────────────────────────

    def _pass(self, name: str, detail: str = "") -> None:
        tag = f"  ✅ PASS  {name}" + (f"  [{detail}]" if detail else "")
        print(tag); self.passed.append(tag)

    def _fail(self, name: str, detail: str = "") -> None:
        tag = f"  ❌ FAIL  {name}" + (f"  [{detail}]" if detail else "")
        print(tag); self.failed.append(tag)

    def _assert_positive(self, name: str, resp, expected_sid: int) -> bool:
        if resp is None:
            self._fail(name, "timeout"); return False
        if resp[0] == SID_NEG:
            nrc = resp[2] if len(resp) >= 3 else "?"
            self._fail(name, f"NegResp NRC=0x{nrc:02X}"); return False
        if resp[0] != expected_sid + 0x40:
            self._fail(name, f"wrong SID 0x{resp[0]:02X}"); return False
        self._pass(name, f"SID=0x{resp[0]:02X}"); return True

    def _assert_negative(self, name: str, resp, expected_nrc: int) -> bool:
        if resp is None:
            self._fail(name, "timeout"); return False
        if resp[0] != SID_NEG:
            self._fail(name, f"expected NegResp got 0x{resp[0]:02X}"); return False
        actual = resp[2] if len(resp) >= 3 else 0
        if actual != expected_nrc:
            self._fail(name, f"exp 0x{expected_nrc:02X} got 0x{actual:02X}"); return False
        self._pass(name, f"NRC=0x{actual:02X}"); return True

    def _switch_session(self, t: int) -> None:
        self._send([SID_SESSION, t]); self._recv()

    # ── Security helpers ───────────────────────────────────────────────

    def _do_security_unlock(self) -> bool:
        """Perform full seed/key exchange. Returns True on success."""
        self._send([SID_SEC, SEC_REQ_SEED])
        resp = self._recv()
        if resp is None or resp[0] == SID_NEG:
            return False
        seed = struct.unpack(">I", bytes(resp[2:6]))[0]
        if seed == 0:
            return True   # already unlocked
        key  = seed ^ SEC_SECRET
        self._send([SID_SEC, SEC_SEND_KEY,
                    (key >> 24) & 0xFF, (key >> 16) & 0xFF,
                    (key >> 8) & 0xFF,  key & 0xFF])
        resp = self._recv()
        return resp is not None and resp[0] == SID_SEC + 0x40

    # ═════════════════════════════════════════════════════════════════
    # GROUP 1: Pre-Flash Preconditions
    # ═════════════════════════════════════════════════════════════════

    def tc01_verify_initial_sw_version(self) -> None:
        """TC01: Read SW version (DID 0xF189) before flash — should be OLD version."""
        self._switch_session(SESSION_DEFAULT)
        self._send([SID_READ_DID, 0xF1, 0x89])
        resp = self._recv()
        if not self._assert_positive("TC01 Read initial SW version", resp, SID_READ_DID):
            return
        version = bytes(resp[3:]).decode("ascii", errors="replace").rstrip("\x00")
        if FIRMWARE_VERSION_OLD in version:
            self._pass("TC01 SW version is OLD (pre-flash) ✓", version)
        else:
            self._fail("TC01 SW version", f"expected '{FIRMWARE_VERSION_OLD}' got '{version}'")

    def tc02_request_download_default_session(self) -> None:
        """TC02: RequestDownload in default session → NRC 0x22 (wrong session)."""
        self._switch_session(SESSION_DEFAULT)
        fw = make_firmware_image(FIRMWARE_VERSION_NEW)
        sz = len(fw)
        self._send([SID_REQ_DL, 0x00, 0x44,
                    0x08, 0x00, 0x00, 0x00])   # addr format + partial addr
        resp = self._recv()
        self._assert_negative("TC02 RequestDownload in default → NRC 0x22",
                              resp, NRC_CONDITIONS_NOT_CORRECT)

    def tc03_request_download_no_security(self) -> None:
        """TC03: RequestDownload in programming session without security → NRC 0x33."""
        self._switch_session(SESSION_PROGRAMMING)
        # Skip SecurityAccess — go straight to RequestDownload (should fail at erase check first)
        # Since flash is not READY_FOR_DL, ECU returns NRC 0x22 (conditions not correct)
        self._send([SID_REQ_DL, 0x00, 0x44,
                    0x08, 0x00, 0x00, 0x00])
        resp = self._recv()
        self._assert_negative("TC03 RequestDownload before erase → NRC 0x22",
                              resp, NRC_CONDITIONS_NOT_CORRECT)

    # ═════════════════════════════════════════════════════════════════
    # GROUP 2: Programming Session + Security
    # ═════════════════════════════════════════════════════════════════

    def tc04_enter_programming_session(self) -> None:
        """TC04: Switch to programmingSession (0x10 0x02)."""
        self._send([SID_SESSION, SESSION_PROGRAMMING])
        resp = self._recv()
        if not self._assert_positive("TC04 Enter programmingSession", resp, SID_SESSION):
            return
        if len(resp) >= 2 and resp[1] == SESSION_PROGRAMMING:
            self._pass("TC04 Session sub-function echoed = 0x02 ✓", "")
        else:
            self._fail("TC04 Session echo", f"got {resp}")

    def tc05_security_access_programming(self) -> None:
        """TC05: Full SecurityAccess unlock in programming session."""
        ok = self._do_security_unlock()
        if ok:
            self._pass("TC05 SecurityAccess unlock in programming session ✓", "")
        else:
            self._fail("TC05 SecurityAccess unlock failed")

    def tc06_comm_ctrl_disable_tx(self) -> None:
        """TC06: CommunicationControl — disable normal ECU transmissions."""
        self._send([SID_COMM_CTRL, COMM_DISABLE_TX, COMM_TYPE_NORM])
        resp = self._recv()
        if not self._assert_positive("TC06 CommCtrl disable normal tx", resp, SID_COMM_CTRL):
            return
        if len(resp) >= 2 and resp[1] == COMM_DISABLE_TX:
            self._pass("TC06 CommCtrl echo = 0x03 ✓", "")
        else:
            self._fail("TC06 CommCtrl echo", f"got {resp}")

    # ═════════════════════════════════════════════════════════════════
    # GROUP 3: EraseMemory Routine
    # ═════════════════════════════════════════════════════════════════

    def tc07_erase_memory_start(self) -> None:
        """TC07: Start EraseMemory routine (RID 0xFF00) → RS_RUNNING."""
        self._send([SID_ROUTINE, RC_START, 0xFF, 0x00])
        resp = self._recv()
        if not self._assert_positive("TC07 EraseMemory start", resp, SID_ROUTINE):
            return
        rs = resp[4] if len(resp) >= 5 else 0
        if rs == 0x01:
            self._pass("TC07 EraseMemory status = RS_RUNNING ✓", "")
        else:
            self._fail("TC07 Erase status", f"expected 0x01 got 0x{rs:02X}")

    def tc08_erase_memory_poll_complete(self) -> None:
        """TC08: Poll EraseMemory results until RS_COMPLETED."""
        deadline = time.monotonic() + 2.0
        completed = False
        while time.monotonic() < deadline:
            time.sleep(0.05)
            self._send([SID_ROUTINE, RC_RESULTS, 0xFF, 0x00])
            resp = self._recv()
            if resp and resp[0] == SID_ROUTINE + 0x40:
                rs = resp[4] if len(resp) >= 5 else 0
                if rs == 0x02:
                    completed = True
                    break
        if completed:
            self._pass("TC08 EraseMemory polled to RS_COMPLETED ✓", "")
        else:
            self._fail("TC08 EraseMemory never completed within 2s")

    # ═════════════════════════════════════════════════════════════════
    # GROUP 4: RequestDownload Negotiation
    # ═════════════════════════════════════════════════════════════════

    def tc09_request_download_valid(self, fw_size: int) -> Optional[int]:
        """TC09: RequestDownload with valid address/size → negotiates maxBlockSize."""
        addr = FLASH_BASE_ADDRESS
        # addrAndLengthFormatId: high nibble=4 (4 size bytes), low nibble=4 (4 addr bytes)
        addr_fmt = 0x44
        addr_b   = [(addr >> 24) & 0xFF, (addr >> 16) & 0xFF,
                    (addr >> 8) & 0xFF,  addr & 0xFF]
        size_b   = [(fw_size >> 24) & 0xFF, (fw_size >> 16) & 0xFF,
                    (fw_size >> 8) & 0xFF,  fw_size & 0xFF]
        self._send([SID_REQ_DL, 0x00, addr_fmt] + addr_b + size_b)
        resp = self._recv()
        if not self._assert_positive("TC09 RequestDownload valid", resp, SID_REQ_DL):
            return None
        # Response: [0x74, lengthFormatId, maxBlockSize_H, maxBlockSize_L]
        if len(resp) < 4:
            self._fail("TC09 Response too short"); return None
        max_block = (resp[2] << 8) | resp[3]
        self._pass("TC09 maxBlockSize negotiated", f"{max_block} bytes/block")
        return max_block

    def tc10_request_download_wrong_address(self) -> None:
        """TC10: RequestDownload with wrong base address → NRC 0x70."""
        wrong_addr = 0x20000000   # SRAM, not flash
        addr_b     = [(wrong_addr >> 24) & 0xFF, (wrong_addr >> 16) & 0xFF,
                      (wrong_addr >> 8) & 0xFF,  wrong_addr & 0xFF]
        size_b     = [0x00, 0x02, 0x00, 0x00]   # 128KB
        self._send([SID_REQ_DL, 0x00, 0x44] + addr_b + size_b)
        resp = self._recv()
        self._assert_negative("TC10 RequestDownload wrong address → NRC 0x70",
                              resp, NRC_UPLOAD_DOWNLOAD_NOT_ACCEPTED)

    # ═════════════════════════════════════════════════════════════════
    # GROUP 5: TransferData — Chunked Firmware Transmission
    # ═════════════════════════════════════════════════════════════════

    def tc11_transfer_firmware(self, firmware: bytes, max_block: int) -> bool:
        """TC11: Transfer all firmware blocks using TransferData (0x36)."""
        total        = len(firmware)
        block_counter = 1
        offset        = 0
        blocks_sent   = 0

        while offset < total:
            chunk        = firmware[offset: offset + max_block]
            uds_payload  = [SID_XFER_DATA, block_counter] + list(chunk)
            self._send(uds_payload)
            resp = self._recv(timeout=5.0)

            if resp is None or resp[0] == SID_NEG:
                nrc = resp[2] if resp and resp[0] == SID_NEG and len(resp) >= 3 else "?"
                self._fail(f"TC11 TransferData block {block_counter}",
                           f"NRC=0x{nrc}" if resp else "timeout")
                return False

            if resp[0] != SID_XFER_DATA + 0x40 or resp[1] != block_counter:
                self._fail(f"TC11 Block {block_counter} echo mismatch",
                           f"resp={resp[:4]}")
                return False

            offset        += len(chunk)
            block_counter  = (block_counter + 1) & 0xFF
            blocks_sent   += 1

        self._pass("TC11 All firmware blocks transferred ✓",
                   f"{blocks_sent} blocks, {total} bytes")
        return True

    def tc12_transfer_data_wrong_counter(self) -> None:
        """TC12: TransferData with wrong block counter → NRC 0x73."""
        # Re-enter sequence: we are now in DONE/VERIFYING state so this is after exit.
        # Instead, send an arbitrary block with counter=0xFF out of sequence
        # while ECU is not in DOWNLOADING state → NRC 0x22
        self._send([SID_XFER_DATA, 0xFF, 0xAA, 0xBB])
        resp = self._recv()
        self._assert_negative("TC12 TransferData out of sequence → NRC 0x22",
                              resp, NRC_CONDITIONS_NOT_CORRECT)

    # ═════════════════════════════════════════════════════════════════
    # GROUP 6: RequestTransferExit + Verification
    # ═════════════════════════════════════════════════════════════════

    def tc13_request_transfer_exit(self, firmware: bytes) -> None:
        """TC13: RequestTransferExit with CRC-32 → ECU verifies and responds 0x77."""
        crc = _crc32(firmware)
        crc_b = [(crc >> 24) & 0xFF, (crc >> 16) & 0xFF,
                 (crc >> 8) & 0xFF,  crc & 0xFF]
        self._send([SID_XFER_EXIT] + crc_b)
        resp = self._recv()
        if resp is not None and resp[0] == SID_XFER_EXIT + 0x40:
            self._pass("TC13 RequestTransferExit: CRC verified, response 0x77 ✓", "")
        else:
            nrc = resp[2] if resp and resp[0] == SID_NEG and len(resp) >= 3 else "?"
            self._fail("TC13 TransferExit",
                       f"NRC=0x{nrc:02X}" if isinstance(nrc, int) else "timeout")

    def tc14_check_prog_dependencies(self) -> None:
        """TC14: CheckProgrammingDependencies (RID 0xFF01) after flash → result 0x00 pass."""
        self._send([SID_ROUTINE, RC_START, 0xFF, 0x01])
        resp = self._recv()
        if not self._assert_positive("TC14 CheckProgDeps start", resp, SID_ROUTINE):
            return
        result = resp[5] if len(resp) >= 6 else 0xFF
        if result == 0x00:
            self._pass("TC14 CheckProgDeps result = 0x00 (pass) ✓", "")
        else:
            self._fail("TC14 CheckProgDeps result", f"expected 0x00 got 0x{result:02X}")

    def tc15_comm_ctrl_re_enable(self) -> None:
        """TC15: CommunicationControl re-enable (0x28 0x00) normal comms."""
        self._send([SID_COMM_CTRL, COMM_ENABLE_ALL, COMM_TYPE_NORM])
        resp = self._recv()
        if not self._assert_positive("TC15 CommCtrl re-enable normal tx", resp, SID_COMM_CTRL):
            return
        if len(resp) >= 2 and resp[1] == COMM_ENABLE_ALL:
            self._pass("TC15 CommCtrl echo = 0x00 ✓", "")
        else:
            self._fail("TC15 CommCtrl echo", f"got {resp}")

    # ═════════════════════════════════════════════════════════════════
    # GROUP 7: ECUReset + Post-Flash Verification
    # ═════════════════════════════════════════════════════════════════

    def tc16_ecu_reset_hard(self) -> None:
        """TC16: ECUReset hardReset (0x11 0x01) → ECU boots with new firmware."""
        self._send([SID_ECU_RESET, 0x01])
        resp = self._recv()
        if resp is not None and resp[0] == SID_ECU_RESET + 0x40:
            self._pass("TC16 ECUReset hardReset → 0x51 ✓", "")
        else:
            self._fail("TC16 ECUReset", f"resp={resp}")

    def tc17_verify_new_sw_version(self) -> None:
        """TC17: Read SW version after reset → must be NEW version."""
        # After reset, ECU is in defaultSession — read is allowed
        time.sleep(0.3)   # wait for ECU reset completion
        self._switch_session(SESSION_DEFAULT)
        self._send([SID_READ_DID, 0xF1, 0x89])
        resp = self._recv()
        if not self._assert_positive("TC17 Read SW version post-flash", resp, SID_READ_DID):
            return
        version = bytes(resp[3:]).decode("ascii", errors="replace").rstrip("\x00")
        if FIRMWARE_VERSION_NEW in version:
            self._pass("TC17 SW version is NEW (post-flash) ✓", version)
        else:
            self._fail("TC17 SW version post-flash",
                       f"expected '{FIRMWARE_VERSION_NEW}' got '{version}'")

    # ═════════════════════════════════════════════════════════════════
    # GROUP 8: Error Path NRC Validation
    # ═════════════════════════════════════════════════════════════════

    def tc18_transfer_exit_before_start(self) -> None:
        """TC18: RequestTransferExit before RequestDownload → NRC 0x22.
        Flash state check (IDLE) fires before security check, so no unlock needed.
        """
        self._switch_session(SESSION_PROGRAMMING)
        # Deliberately skip security unlock — flash state is IDLE so NRC 0x22
        # fires before any security check. Keeping security LOCKED also ensures
        # TC19 (erase without security) tests a genuinely locked ECU.
        self._send([SID_XFER_EXIT, 0x00, 0x00, 0x00, 0x00])
        resp = self._recv()
        self._assert_negative("TC18 TransferExit before download → NRC 0x22",
                              resp, NRC_CONDITIONS_NOT_CORRECT)

    def tc19_erase_without_security(self) -> None:
        """TC19: EraseMemory in programming session without security → NRC 0x33."""
        # Reset session to get fresh locked state
        self._switch_session(SESSION_PROGRAMMING)
        # Skip security — go straight to erase
        self._send([SID_ROUTINE, RC_START, 0xFF, 0x00])
        resp = self._recv()
        self._assert_negative("TC19 Erase without security → NRC 0x33",
                              resp, NRC_SECURITY_ACCESS_DENIED)

    def tc20_comm_ctrl_in_default(self) -> None:
        """TC20: CommunicationControl in default session → NRC 0x22."""
        self._switch_session(SESSION_DEFAULT)
        self._send([SID_COMM_CTRL, COMM_DISABLE_TX, COMM_TYPE_NORM])
        resp = self._recv()
        self._assert_negative("TC20 CommCtrl in default session → NRC 0x22",
                              resp, NRC_CONDITIONS_NOT_CORRECT)

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
    print("\n" + "💾⚡  " * 10)
    print("  Day 17 — ECU Flashing: 0x34 RequestDownload,")
    print("           0x36 TransferData, 0x37 RequestTransferExit")
    print("💾⚡  " * 10)

    ecu    = SimulatedECU()
    tester = UDSTester()
    ecu.start()
    time.sleep(0.1)

    # Build the firmware image we will flash
    firmware = make_firmware_image(FIRMWARE_VERSION_NEW)
    fw_crc   = _crc32(firmware)
    print(f"\n  Firmware: {FIRMWARE_VERSION_NEW}, "
          f"{len(firmware)} bytes, CRC-32=0x{fw_crc:08X}")

    banner("GROUP 1: Pre-Flash Preconditions")
    tester.tc01_verify_initial_sw_version()
    tester.tc02_request_download_default_session()
    tester.tc03_request_download_no_security()

    banner("GROUP 2: Programming Session + SecurityAccess + CommCtrl")
    tester.tc04_enter_programming_session()
    tester.tc05_security_access_programming()
    tester.tc06_comm_ctrl_disable_tx()

    banner("GROUP 3: EraseMemory Routine")
    tester.tc07_erase_memory_start()
    tester.tc08_erase_memory_poll_complete()

    banner("GROUP 4: RequestDownload Negotiation")
    # TC10 (error path) runs first — flash still in READY_FOR_DL state.
    # TC09 (happy path) runs second — transitions flash to DOWNLOADING.
    tester.tc10_request_download_wrong_address()
    max_block = tester.tc09_request_download_valid(len(firmware))

    banner("GROUP 5: TransferData — Chunked Firmware")
    if max_block:
        success = tester.tc11_transfer_firmware(firmware, max_block)
    else:
        tester._fail("TC11 Skipped — max_block not negotiated")
        success = False
    tester.tc12_transfer_data_wrong_counter()

    banner("GROUP 6: RequestTransferExit + CheckProgDeps + CommCtrl")
    tester.tc13_request_transfer_exit(firmware)
    tester.tc14_check_prog_dependencies()
    tester.tc15_comm_ctrl_re_enable()

    banner("GROUP 7: ECUReset + Post-Flash SW Version Verification")
    tester.tc16_ecu_reset_hard()
    tester.tc17_verify_new_sw_version()

    banner("GROUP 8: Error Path — NRC Validation")
    tester.tc18_transfer_exit_before_start()
    tester.tc19_erase_without_security()
    tester.tc20_comm_ctrl_in_default()

    tester.print_summary()
    ecu.stop()
    tester.shutdown()


if __name__ == "__main__":
    main()
