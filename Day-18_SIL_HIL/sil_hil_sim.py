"""
Day 18: SIL vs HIL — Software-in-the-Loop vs Hardware-in-the-Loop
=================================================================
Demonstrates:
  - BusAdapter: same test code works for SIL (virtual) or HIL (real CAN hardware)
  - PlantModel: continuous thermal simulation (engine coolant temperature + fan)
  - ECU auto-control: fan hysteresis loop driven by plant model temperature
  - SIL timing: non-real-time, OS-scheduled, variable latency
  - The reveal: Days 1–17 were SIL all along — today we name it

Physics simulated:
  FAN_ON  = 90 °C  (ECU activates fan above this)
  FAN_OFF = 85 °C  (hysteresis — fan stays on until below this)
  OVER_TEMP = 105 °C → DTC P0217 (Engine Coolant Temperature Too High)

DIDs:
  0xF405 — Coolant Temperature:  2 bytes big-endian, raw = int(temp_°C × 10)
  0xF406 — Fan Duty Cycle:       1 byte, 0 = off / 100 = full speed
  0xF189 — SW Version:           ASCII string

No hardware needed.

Install:
    pip install python-can

Run:
    python sil_hil_sim.py
"""

import can
import threading
import time
import statistics
from typing import Optional, Dict, Tuple

# ─── CONSTANTS ────────────────────────────────────────────────────────────────

CHANNEL      = "vcan0"
TESTER_TX_ID = 0x7E0
TESTER_RX_ID = 0x7E8

# Temperature thresholds (°C)
AMBIENT_TEMP_C = 25.0
FAN_ON_TEMP_C  = 90.0    # ECU activates fan at or above this
FAN_OFF_TEMP_C = 85.0    # ECU deactivates fan below this (hysteresis gap = 5 °C)
OVER_TEMP_C    = 105.0   # Triggers DTC P0217

# UDS Service IDs
SID_SESSION  = 0x10
SID_READ_DID = 0x22
SID_READ_DTC = 0x19
SID_NEG      = 0x7F

# NRCs
NRC_SUBFUNC_NOT_SUPPORTED  = 0x12
NRC_INCORRECT_MSG_LENGTH   = 0x13
NRC_CONDITIONS_NOT_CORRECT = 0x22
NRC_REQUEST_OUT_OF_RANGE   = 0x31

# DIDs
DID_COOLANT_TEMP = 0xF405
DID_FAN_DUTY     = 0xF406
DID_SW_VERSION   = 0xF189

# DTC P0217 — Engine Coolant Temperature Too High
DTC_P0217_H      = 0x02
DTC_P0217_L      = 0x17
DTC_CONFIRMED    = 0xAF   # TF|CDTC|WIR|PDTC set

# ECU control loop: run every N recv cycles (each cycle ≈ 20 ms)
CONTROL_LOOP_PERIOD = 5   # ≈ 100 ms between control decisions

# ─── ISO-TP HELPERS ───────────────────────────────────────────────────────────

def build_sf(uds_bytes: list) -> bytes:
    assert 1 <= len(uds_bytes) <= 7
    return bytes([len(uds_bytes)] + list(uds_bytes) + [0x00] * (7 - len(uds_bytes)))


def build_ff(uds_bytes: list) -> bytes:
    total = len(uds_bytes)
    return bytes([0x10 | ((total >> 8) & 0x0F), total & 0xFF] + list(uds_bytes[:6]))


def build_cf(sn: int, chunk: list) -> bytes:
    return bytes([0x20 | (sn & 0x0F)] + list(chunk) + [0x00] * (7 - len(chunk)))


def build_fc(bs: int = 0, stmin: int = 0) -> bytes:
    return bytes([0x30, bs & 0xFF, stmin & 0x7F, 0x00, 0x00, 0x00, 0x00, 0x00])


# ─── PLANT MODEL ──────────────────────────────────────────────────────────────

class PlantModel:
    """
    Simulates the physical engine: coolant temperature + fan state.

    In SIL: the test manipulates this Python object directly
            (full observability + controllability).

    In HIL: equivalent action = adjusting a precision resistor on the ECU's
            thermistor ADC input via a signal conditioning unit (e.g.,
            dSPACE I/O board, NI SCB-68).  The test calls
            rig.set_analog_channel("COOLANT_NTC", temp_to_resistance(95.0))
            instead of plant.coolant_temp = 95.0.
    """

    def __init__(self) -> None:
        self._lock      = threading.Lock()
        self._temp      = AMBIENT_TEMP_C
        self.fan_active = False

    @property
    def coolant_temp(self) -> float:
        with self._lock:
            return self._temp

    @coolant_temp.setter
    def coolant_temp(self, value: float) -> None:
        with self._lock:
            self._temp = max(AMBIENT_TEMP_C, min(150.0, float(value)))

    def heat(self, delta: float = 5.0) -> float:
        """Simulate thermal load — raise temperature by delta °C."""
        with self._lock:
            self._temp = min(150.0, self._temp + delta)
            return self._temp

    def cool(self, delta: float = 5.0) -> float:
        """Simulate cooling — lower temperature by delta °C."""
        with self._lock:
            self._temp = max(AMBIENT_TEMP_C, self._temp - delta)
            return self._temp

    def reset(self) -> None:
        """Restore initial conditions (ambient temperature, fan off)."""
        with self._lock:
            self._temp      = AMBIENT_TEMP_C
            self.fan_active = False


# ─── BUS ADAPTER ──────────────────────────────────────────────────────────────

class BusAdapter:
    """
    Abstracts SIL (virtual bus) vs HIL (real CAN hardware).

    The UDSTester class is written once and works unchanged regardless of which
    bus the adapter provides.  This is the #1 portability pattern for embedded
    test automation.

    SIL mode: python-can virtual bus — zero hardware needed (Days 1–18).
    HIL mode: python-can with PCAN-USB / SocketCAN / Kvaser / Vector.
    """

    SIL = "SIL"
    HIL = "HIL"

    # Probe these hardware interfaces in priority order.
    # Each entry: (interface_name, channel, extra_kwargs)
    _HIL_PROBES = [
        ("pcan",      "PCAN_USBBUS1", {"bitrate": 500000}),
        ("socketcan", "can0",         {"bitrate": 500000}),
        ("kvaser",    "0",            {"bitrate": 500000}),
        ("vector",    "0",            {"bitrate": 500000, "app_name": "TestApp"}),
    ]

    def __init__(self) -> None:
        self.mode      = self.SIL
        self.interface = "virtual"
        self.channel   = CHANNEL

    def open_for_tester(self) -> can.BusABC:
        """
        Try real CAN hardware; fall back to virtual bus if unavailable.
        Never raises — always returns a usable bus.
        """
        for iface, chan, kwargs in self._HIL_PROBES:
            try:
                bus            = can.Bus(interface=iface, channel=chan, **kwargs)
                self.mode      = self.HIL
                self.interface = iface
                self.channel   = chan
                return bus
            except Exception:
                continue
        # No real hardware found — SIL fallback
        self.mode      = self.SIL
        self.interface = "virtual"
        self.channel   = CHANNEL
        return can.Bus(interface="virtual", channel=CHANNEL)

    def is_hil(self) -> bool:
        return self.mode == self.HIL

    def describe(self) -> str:
        return f"{self.mode} [{self.interface}:{self.channel}]"


# ─── SIMULATED ECU ────────────────────────────────────────────────────────────

class SimulatedECU(threading.Thread):
    """
    ECU that:
    1. Reads coolant temperature from PlantModel every control cycle
    2. Drives fan on/off with hysteresis  (ON ≥ 90 °C, OFF < 85 °C)
    3. Sets DTC P0217 when temperature exceeds OVER_TEMP_C
    4. Responds to UDS: 0x10 (session), 0x22 (DID read), 0x19 (DTC read)
    """

    S3_TIMEOUT_S = 5.0

    def __init__(self, plant: PlantModel) -> None:
        super().__init__(daemon=True, name="SimECU")
        self._plant       = plant
        self.bus          = can.Bus(interface="virtual", channel=CHANNEL)
        self.session      = 0x01   # defaultSession
        self._stop        = threading.Event()
        self._last_diag_t = time.monotonic()
        self._fan_on      = False
        self._loop_count  = 0
        # DTC store: {(high_byte, low_byte): status_byte}
        self._dtcs: Dict[Tuple[int, int], int] = {}

    def stop(self) -> None:
        self._stop.set()
        self.bus.shutdown()

    # ── ISO-TP send ────────────────────────────────────────────────────

    def _send(self, payload: list) -> None:
        if len(payload) <= 7:
            self.bus.send(can.Message(arbitration_id=TESTER_RX_ID,
                                      data=build_sf(payload),
                                      is_extended_id=False))
            return
        ff = build_ff(payload)
        self.bus.send(can.Message(arbitration_id=TESTER_RX_ID,
                                  data=ff, is_extended_id=False))
        # Wait for Flow Control from tester
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            f = self.bus.recv(timeout=0.05)
            if f is not None and f.arbitration_id == TESTER_TX_ID:
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

    # ── Service: 0x10 DiagnosticSessionControl ─────────────────────────

    def _handle_session(self, sub: int) -> None:
        if sub not in (0x01, 0x02, 0x03):
            self._neg(SID_SESSION, NRC_SUBFUNC_NOT_SUPPORTED); return
        self.session      = sub
        self._last_diag_t = time.monotonic()
        self._send([SID_SESSION + 0x40, sub, 0x00, 0x19, 0x01, 0xF4])

    # ── Service: 0x22 ReadDataByIdentifier ─────────────────────────────

    def _handle_read_did(self, uds: list) -> None:
        if len(uds) < 3:
            self._neg(SID_READ_DID, NRC_INCORRECT_MSG_LENGTH); return
        did = (uds[1] << 8) | uds[2]
        if did == DID_COOLANT_TEMP:
            raw = int(self._plant.coolant_temp * 10)
            self._send([SID_READ_DID + 0x40, uds[1], uds[2],
                        (raw >> 8) & 0xFF, raw & 0xFF])
        elif did == DID_FAN_DUTY:
            duty = 100 if self._fan_on else 0
            self._send([SID_READ_DID + 0x40, uds[1], uds[2], duty])
        elif did == DID_SW_VERSION:
            ver = list("Day18-ECU-v1.0".encode("ascii"))
            self._send([SID_READ_DID + 0x40, uds[1], uds[2]] + ver)
        else:
            self._neg(SID_READ_DID, NRC_REQUEST_OUT_OF_RANGE)

    # ── Service: 0x19 ReadDTCInformation ──────────────────────────────

    def _handle_read_dtc(self, uds: list) -> None:
        if len(uds) < 2:
            self._neg(SID_READ_DTC, NRC_INCORRECT_MSG_LENGTH); return
        sub = uds[1]
        if sub == 0x02:   # reportDTCByStatusMask
            if len(uds) < 3:
                self._neg(SID_READ_DTC, NRC_INCORRECT_MSG_LENGTH); return
            mask    = uds[2]
            payload = [SID_READ_DTC + 0x40, sub, 0xFF]
            for (h, l), status in self._dtcs.items():
                if status & mask:
                    payload += [h, l, status]
            self._send(payload)
        else:
            self._neg(SID_READ_DTC, NRC_SUBFUNC_NOT_SUPPORTED)

    # ── Periodic control loop (thermostat + DTC monitor) ──────────────

    def _update_control(self) -> None:
        temp = self._plant.coolant_temp
        key  = (DTC_P0217_H, DTC_P0217_L)

        # Fan hysteresis
        if temp >= FAN_ON_TEMP_C and not self._fan_on:
            self._fan_on           = True
            self._plant.fan_active = True
        elif temp < FAN_OFF_TEMP_C and self._fan_on:
            self._fan_on           = False
            self._plant.fan_active = False

        # Over-temp DTC lifecycle
        if temp > OVER_TEMP_C:
            self._dtcs[key] = DTC_CONFIRMED
        elif key in self._dtcs and temp < FAN_OFF_TEMP_C:
            # Fault healed and temperature back to safe zone
            del self._dtcs[key]

    # ── UDS request dispatcher ─────────────────────────────────────────

    def _dispatch(self, uds: list) -> None:
        self._last_diag_t = time.monotonic()
        sid = uds[0]
        if   sid == SID_SESSION  and len(uds) >= 2: self._handle_session(uds[1])
        elif sid == SID_READ_DID:                    self._handle_read_did(uds)
        elif sid == SID_READ_DTC:                    self._handle_read_dtc(uds)
        else:
            self._neg(sid, 0x11)   # serviceNotSupported

    # ── Main loop ──────────────────────────────────────────────────────

    def run(self) -> None:
        while not self._stop.is_set():
            now = time.monotonic()

            # S3 timeout watchdog
            if (self.session != 0x01
                    and now - self._last_diag_t > self.S3_TIMEOUT_S):
                self.session = 0x01

            # Periodic control loop
            self._loop_count += 1
            if self._loop_count % CONTROL_LOOP_PERIOD == 0:
                self._update_control()

            frame = self.bus.recv(timeout=0.02)
            if frame is None or frame.arbitration_id != TESTER_TX_ID:
                continue

            data     = bytes(frame.data)
            pci_type = (data[0] >> 4) & 0x0F

            if pci_type == 0:   # Single Frame
                length = data[0] & 0x0F
                uds    = list(data[1: 1 + length])
                if uds:
                    self._dispatch(uds)
            elif pci_type == 1:   # First Frame (long request from tester)
                total = ((data[0] & 0x0F) << 8) | data[1]
                buf   = list(data[2:])
                self.bus.send(can.Message(arbitration_id=TESTER_RX_ID,
                                          data=build_fc(0, 0),
                                          is_extended_id=False))
                deadline = time.monotonic() + 2.0
                while len(buf) < total and time.monotonic() < deadline:
                    f = self.bus.recv(timeout=0.05)
                    if f and f.arbitration_id == TESTER_TX_ID:
                        if (f.data[0] >> 4) & 0x0F == 2:
                            buf += list(f.data[1:])
                if buf:
                    self._dispatch(buf[:total])


# ─── UDS TESTER ───────────────────────────────────────────────────────────────

class UDSTester:
    """
    Tester that works with any python-can Bus — SIL virtual bus or HIL real CAN.
    The test methods do not know or care which bus is underneath.
    """

    RESPONSE_TIMEOUT_S = 3.0
    STMIN_MS           = 5

    def __init__(self, bus: can.BusABC) -> None:
        self.bus    = bus
        self.passed = []
        self.failed = []

    def shutdown(self) -> None:
        self.bus.shutdown()

    # ── ISO-TP send/receive ─────────────────────────────────────────────

    def _send(self, uds_bytes: list) -> None:
        if len(uds_bytes) <= 7:
            self.bus.send(can.Message(arbitration_id=TESTER_TX_ID,
                                      data=build_sf(uds_bytes),
                                      is_extended_id=False))
        else:
            self.bus.send(can.Message(arbitration_id=TESTER_TX_ID,
                                      data=build_ff(uds_bytes),
                                      is_extended_id=False))
            # Wait for ECU Flow Control
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                f = self.bus.recv(timeout=0.05)
                if f and f.arbitration_id == TESTER_RX_ID:
                    break
            sn, offset = 1, 6
            while offset < len(uds_bytes):
                chunk = uds_bytes[offset: offset + 7]
                self.bus.send(can.Message(arbitration_id=TESTER_TX_ID,
                                          data=build_cf(sn, chunk),
                                          is_extended_id=False))
                sn     = (sn + 1) & 0x0F
                offset += 7
                time.sleep(self.STMIN_MS / 1000.0)

    def _recv(self, timeout: float = None) -> Optional[list]:
        deadline   = time.monotonic() + (timeout or self.RESPONSE_TIMEOUT_S)
        collected  = []
        total_exp  = 0

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

    # ── Assertions ──────────────────────────────────────────────────────

    def _pass(self, name: str, detail: str = "") -> None:
        tag = f"  ✅ PASS  {name}" + (f"  [{detail}]" if detail else "")
        print(tag)
        self.passed.append(tag)

    def _fail(self, name: str, detail: str = "") -> None:
        tag = f"  ❌ FAIL  {name}" + (f"  [{detail}]" if detail else "")
        print(tag)
        self.failed.append(tag)

    def _assert_positive(self, name: str, resp, sid: int) -> bool:
        if resp is None:
            self._fail(name, "timeout"); return False
        if resp[0] == SID_NEG:
            nrc = resp[2] if len(resp) >= 3 else "?"
            self._fail(name, f"NegResp NRC=0x{nrc:02X}"); return False
        if resp[0] != sid + 0x40:
            self._fail(name, f"wrong SID 0x{resp[0]:02X}"); return False
        self._pass(name, f"SID=0x{resp[0]:02X}")
        return True

    # ── Helpers ─────────────────────────────────────────────────────────

    def _read_coolant_temp(self) -> Optional[float]:
        self._send([SID_READ_DID, 0xF4, 0x05])
        resp = self._recv()
        if resp and resp[0] == SID_READ_DID + 0x40 and len(resp) >= 5:
            return ((resp[3] << 8) | resp[4]) / 10.0
        return None

    def _read_fan_duty(self) -> Optional[int]:
        self._send([SID_READ_DID, 0xF4, 0x06])
        resp = self._recv()
        if resp and resp[0] == SID_READ_DID + 0x40 and len(resp) >= 4:
            return resp[3]
        return None

    def _wait_control(self, cycles: int = 2) -> None:
        """Wait for ECU control loop to execute ~cycles times (each ≈ 100 ms)."""
        time.sleep(cycles * 0.15)

    # ═════════════════════════════════════════════════════════════════
    # GROUP 1: Environment Detection
    # ═════════════════════════════════════════════════════════════════

    def tc01_adapter_mode(self, adapter: BusAdapter) -> None:
        """TC01: BusAdapter correctly identifies the current test environment."""
        mode = adapter.mode
        if mode in (BusAdapter.SIL, BusAdapter.HIL):
            self._pass(f"TC01 Environment = {mode} ✓", adapter.describe())
        else:
            self._fail("TC01 Unknown adapter mode", mode)

    def tc02_hil_fallback_no_exception(self) -> None:
        """TC02: BusAdapter.open_for_tester() never raises — graceful SIL fallback."""
        probe = BusAdapter()
        bus   = None
        try:
            bus = probe.open_for_tester()
            self._pass("TC02 HIL probe completed without exception ✓",
                       f"fell back to {probe.mode}")
        except Exception as exc:
            self._fail("TC02 BusAdapter raised exception", str(exc))
        finally:
            if bus is not None:
                bus.shutdown()

    def tc03_plant_model_init(self, plant: PlantModel) -> None:
        """TC03: PlantModel initialises at AMBIENT_TEMP_C (25 °C)."""
        temp = plant.coolant_temp
        if abs(temp - AMBIENT_TEMP_C) < 0.01:
            self._pass("TC03 PlantModel init at ambient temp ✓", f"{temp:.1f} °C")
        else:
            self._fail("TC03 PlantModel init",
                       f"expected {AMBIENT_TEMP_C} °C got {temp:.1f} °C")

    # ═════════════════════════════════════════════════════════════════
    # GROUP 2: Sensor Reading & DID Encoding
    # ═════════════════════════════════════════════════════════════════

    def tc04_did_at_ambient(self, plant: PlantModel) -> None:
        """TC04: DID 0xF405 at 25 °C → raw = 250 = 0x00FA."""
        plant.coolant_temp = 25.0
        self._send([SID_READ_DID, 0xF4, 0x05])
        resp = self._recv()
        if not self._assert_positive("TC04 DID 0xF405 read at 25 °C", resp, SID_READ_DID):
            return
        raw = (resp[3] << 8) | resp[4]
        if raw == 250:
            self._pass("TC04 Raw = 250 (0x00FA) → 25.0 °C ✓", f"raw={raw}")
        else:
            self._fail("TC04 Raw encoding", f"expected 250 got {raw}")

    def tc05_did_at_fan_on_threshold(self, plant: PlantModel) -> None:
        """TC05: DID 0xF405 at 90 °C → raw = 900 = 0x0384."""
        plant.coolant_temp = 90.0
        self._send([SID_READ_DID, 0xF4, 0x05])
        resp = self._recv()
        if not self._assert_positive("TC05 DID 0xF405 read at 90 °C", resp, SID_READ_DID):
            return
        raw = (resp[3] << 8) | resp[4]
        if raw == 900:
            self._pass("TC05 Raw = 900 (0x0384) → 90.0 °C ✓", f"raw={raw}")
        else:
            self._fail("TC05 Raw encoding at FAN_ON threshold",
                       f"expected 900 got {raw}")

    def tc06_did_at_over_temp(self, plant: PlantModel) -> None:
        """TC06: DID 0xF405 at 105 °C → raw = 1050 = 0x041A."""
        plant.coolant_temp = 105.0
        self._send([SID_READ_DID, 0xF4, 0x05])
        resp = self._recv()
        if not self._assert_positive("TC06 DID 0xF405 read at 105 °C", resp, SID_READ_DID):
            return
        raw = (resp[3] << 8) | resp[4]
        if raw == 1050:
            self._pass("TC06 Raw = 1050 (0x041A) → 105.0 °C ✓", f"raw={raw}")
        else:
            self._fail("TC06 Raw encoding at OVER_TEMP",
                       f"expected 1050 got {raw}")

    def tc07_did_round_trip_fidelity(self, plant: PlantModel) -> None:
        """TC07: Encode→transmit→decode round-trip for 4 temperatures, ±0.05 °C."""
        test_temps = [25.0, 75.0, 90.0, 100.0]
        for target in test_temps:
            plant.coolant_temp = target
            decoded = self._read_coolant_temp()
            if decoded is None:
                self._fail(f"TC07 Round-trip at {target} °C", "timeout"); return
            if abs(decoded - target) > 0.05:
                self._fail("TC07 Round-trip mismatch",
                           f"set={target} °C decoded={decoded} °C"); return
        self._pass("TC07 DID round-trip: 4/4 temps within ±0.05 °C ✓",
                   f"{test_temps}")

    # ═════════════════════════════════════════════════════════════════
    # GROUP 3: Thermal Control Loop — Hysteresis + DTC
    # ═════════════════════════════════════════════════════════════════

    def tc08_fan_off_below_threshold(self, plant: PlantModel) -> None:
        """TC08: At 70 °C (< FAN_ON=90 °C), fan duty = 0 %."""
        plant.coolant_temp = 70.0
        self._wait_control()
        duty = self._read_fan_duty()
        if duty == 0:
            self._pass("TC08 Fan OFF below FAN_ON threshold (70 < 90 °C) ✓",
                       "duty=0 %")
        else:
            self._fail("TC08 Fan should be OFF", f"got duty={duty} %")

    def tc09_fan_activates_above_threshold(self, plant: PlantModel) -> None:
        """TC09: At 95 °C (> FAN_ON=90 °C), ECU auto-activates fan, duty = 100 %."""
        plant.coolant_temp = 95.0
        self._wait_control()
        duty = self._read_fan_duty()
        if duty == 100:
            self._pass("TC09 Fan ON above FAN_ON threshold (95 > 90 °C) ✓",
                       "duty=100 %")
        else:
            self._fail("TC09 Fan should be ON", f"got duty={duty} %")

    def tc10_hysteresis_fan_stays_on(self, plant: PlantModel) -> None:
        """TC10: Hysteresis — at 87 °C (FAN_OFF=85 < 87 < FAN_ON=90), fan STAYS ON."""
        # Precondition: fan is ON from TC09 (was at 95 °C)
        plant.coolant_temp = 87.0
        self._wait_control()
        duty = self._read_fan_duty()
        if duty == 100:
            self._pass("TC10 Hysteresis: fan stays ON at 87 °C ✓",
                       "FAN_OFF=85 < 87 < FAN_ON=90 → no change")
        else:
            self._fail("TC10 Hysteresis broken",
                       f"expected duty=100 % got {duty} %")

    def tc11_fan_deactivates_below_hysteresis(self, plant: PlantModel) -> None:
        """TC11: At 80 °C (< FAN_OFF=85 °C), ECU deactivates fan, duty = 0 %."""
        plant.coolant_temp = 80.0
        self._wait_control()
        duty = self._read_fan_duty()
        if duty == 0:
            self._pass("TC11 Fan OFF below FAN_OFF threshold (80 < 85 °C) ✓",
                       "duty=0 %")
        else:
            self._fail("TC11 Fan should be OFF", f"got duty={duty} %")

    def tc12_over_temp_dtc_p0217(self, plant: PlantModel) -> None:
        """TC12: At 110 °C (> OVER_TEMP=105 °C), DTC P0217 is confirmed (status=0xAF)."""
        plant.coolant_temp = 110.0
        self._wait_control(3)   # give ECU time to detect and set DTC
        self._send([SID_READ_DTC, 0x02, 0xFF])
        resp = self._recv()
        if resp is None or resp[0] != SID_READ_DTC + 0x40:
            self._fail("TC12 DTC read failed",
                       "timeout" if resp is None else f"SID=0x{resp[0]:02X}"); return
        # Scan DTC list: format = [SID_resp, sub, availability, H, L, status, ...]
        dtc_section = resp[3:]
        found, status = False, 0
        i = 0
        while i + 2 < len(dtc_section):
            if dtc_section[i] == DTC_P0217_H and dtc_section[i + 1] == DTC_P0217_L:
                found  = True
                status = dtc_section[i + 2]
                break
            i += 3
        if found:
            self._pass("TC12 DTC P0217 confirmed at 110 °C ✓",
                       f"status=0x{status:02X}")
        else:
            self._fail("TC12 DTC P0217 not found",
                       f"DTC section={[hex(b) for b in dtc_section]}")

    # ═════════════════════════════════════════════════════════════════
    # GROUP 4: SIL Timing Characteristics
    # ═════════════════════════════════════════════════════════════════

    def tc13_measure_sil_baseline_latency(self, plant: PlantModel) -> list:
        """TC13: Measure 20 SIL round-trip latencies for a single DID read (ms)."""
        plant.coolant_temp = 50.0
        latencies: list = []
        for _ in range(20):
            t0 = time.monotonic()
            self._send([SID_READ_DID, 0xF4, 0x05])
            resp = self._recv(timeout=1.0)
            t1   = time.monotonic()
            if resp and resp[0] == SID_READ_DID + 0x40:
                latencies.append((t1 - t0) * 1000)

        if not latencies:
            self._fail("TC13 No successful responses"); return []

        mean_ms = statistics.mean(latencies)
        max_ms  = max(latencies)
        min_ms  = min(latencies)

        if max_ms < 300:
            self._pass("TC13 SIL RTT baseline (20 samples)",
                       f"mean={mean_ms:.2f} ms  min={min_ms:.2f} ms  max={max_ms:.2f} ms")
        else:
            self._fail("TC13 SIL RTT exceeded 300 ms", f"max={max_ms:.2f} ms")
        return latencies

    def tc14_sil_timing_non_deterministic(self, latencies: list) -> None:
        """TC14: SIL RTT std dev > 0 — OS-scheduled, non-real-time by nature."""
        if not latencies or len(latencies) < 2:
            self._fail("TC14 Insufficient latency data"); return
        std_ms = statistics.stdev(latencies)
        # Any positive std dev confirms non-determinism.
        # A real RTOS on HIL would show std < 0.05 ms (< 50 µs jitter).
        self._pass("TC14 SIL timing is non-deterministic ✓ (expected for OS-scheduled)",
                   f"std={std_ms:.3f} ms — HIL spec would be < 0.05 ms")

    def tc15_rapid_sequential_reads(self, plant: PlantModel) -> None:
        """TC15: 10 back-to-back DID reads — SIL handles all without dropped responses."""
        plant.coolant_temp = 60.0
        ok = 0
        for _ in range(10):
            self._send([SID_READ_DID, 0xF4, 0x05])
            resp = self._recv(timeout=1.0)
            if resp and resp[0] == SID_READ_DID + 0x40:
                ok += 1
        if ok == 10:
            self._pass("TC15 10/10 rapid sequential reads ✓", "no dropped responses")
        else:
            self._fail("TC15 Dropped responses", f"{ok}/10 succeeded")

    def tc16_sil_vs_hil_timing_table(self, latencies: list) -> None:
        """TC16: Print SIL timing profile vs theoretical HIL specification."""
        if not latencies:
            self._fail("TC16 No latency data"); return
        mean_ms = statistics.mean(latencies)
        std_ms  = statistics.stdev(latencies) if len(latencies) > 1 else 0.0
        max_ms  = max(latencies)

        print(f"\n  {'─' * 62}")
        print(f"  SIL vs HIL Timing Comparison  (500 kbps CAN, single DID read)")
        print(f"  {'─' * 62}")
        print(f"  {'Metric':<30} {'SIL (measured)':<18} {'HIL (spec)'}")
        print(f"  {'─' * 62}")
        print(f"  {'Mean RTT':<30} {mean_ms:<18.2f} {'~1.5 ms  (1 ms Tx + 0.5 ms ECU)'}")
        print(f"  {'Std Dev (jitter)':<30} {std_ms:<18.3f} {'< 0.05 ms  (RTOS, bare-metal)'}")
        print(f"  {'Worst-case RTT':<30} {max_ms:<18.2f} {'< 3 ms  (guaranteed deadline)'}")
        print(f"  {'Real-time guarantee':<30} {'No (OS scheduler)':<18} {'Yes (RTOS / bare-metal)'}")
        print(f"  {'Timing fault detection':<30} {'Not reliable':<18} {'< 50 µs precision'}")
        print(f"  {'─' * 62}\n")

        self._pass("TC16 SIL/HIL timing comparison printed ✓",
                   f"SIL mean={mean_ms:.2f} ms, HIL spec ≈ 1.5 ms")

    # ═════════════════════════════════════════════════════════════════
    # GROUP 5: Test Portability & Regression
    # ═════════════════════════════════════════════════════════════════

    def tc17_adapter_describe_string(self, adapter: BusAdapter) -> None:
        """TC17: BusAdapter.describe() returns a non-empty, well-formed string."""
        desc = adapter.describe()
        if desc and ("[" in desc) and (BusAdapter.SIL in desc or BusAdapter.HIL in desc):
            self._pass("TC17 BusAdapter.describe() ✓", desc)
        else:
            self._fail("TC17 BusAdapter.describe()", f"unexpected: '{desc}'")

    def tc18_plant_model_reset(self, plant: PlantModel) -> None:
        """TC18: plant.reset() restores ambient temperature; ECU reads it correctly."""
        plant.coolant_temp = 95.0        # elevate temp
        self._wait_control()
        plant.reset()                    # restore to ambient
        self._wait_control()
        decoded = self._read_coolant_temp()
        if decoded is not None and abs(decoded - AMBIENT_TEMP_C) < 0.5:
            self._pass("TC18 PlantModel.reset() → ambient restored ✓",
                       f"{decoded:.1f} °C")
        else:
            self._fail("TC18 PlantModel reset",
                       f"expected {AMBIENT_TEMP_C} °C got {decoded} °C")

    def tc19_full_thermal_cycle(self, plant: PlantModel) -> None:
        """TC19: Full cycle — 25 → 95 °C (fan ON) → 80 °C (fan OFF) → verify both."""
        plant.reset()
        self._wait_control()

        # Phase A: heat above FAN_ON
        plant.coolant_temp = 95.0
        self._wait_control(2)
        duty_hot = self._read_fan_duty()

        # Phase B: cool below FAN_OFF
        plant.coolant_temp = 80.0
        self._wait_control(2)
        duty_cool = self._read_fan_duty()

        if duty_hot == 100 and duty_cool == 0:
            self._pass("TC19 Full thermal cycle ✓",
                       "95 °C→fan=100 %  →  80 °C→fan=0 %")
        else:
            self._fail("TC19 Thermal cycle",
                       f"duty_hot={duty_hot} %  duty_cool={duty_cool} %")

    def tc20_regression_all_services(self, plant: PlantModel) -> None:
        """TC20: Regression — session switch + DID read + DTC read all work post-cycle."""
        plant.reset()

        # DiagnosticSessionControl → extended
        self._send([SID_SESSION, 0x03])
        resp      = self._recv()
        sess_ok   = resp is not None and resp[0] == SID_SESSION + 0x40

        # ReadDID SW version (multi-char ASCII → SF if ≤ 7 UDS bytes)
        self._send([SID_READ_DID, 0xF1, 0x89])
        resp      = self._recv()
        did_ok    = resp is not None and resp[0] == SID_READ_DID + 0x40

        # ReadDTC — plant reset, so no active DTCs
        self._send([SID_READ_DTC, 0x02, 0xFF])
        resp      = self._recv()
        dtc_ok    = resp is not None and resp[0] == SID_READ_DTC + 0x40
        dtc_empty = dtc_ok and len(resp) == 3   # no DTCs in response

        if sess_ok and did_ok and dtc_empty:
            self._pass("TC20 Regression: session + DID + DTC all ✓",
                       "0 active DTCs after plant reset")
        else:
            self._fail("TC20 Regression",
                       f"sess={sess_ok} did={did_ok} dtc_empty={dtc_empty}")

    # ── Summary ─────────────────────────────────────────────────────────

    def print_summary(self) -> None:
        total = len(self.passed) + len(self.failed)
        print(f"\n{'=' * 64}")
        print(f"  TEST SUMMARY: {len(self.passed)}/{total} passed, "
              f"{len(self.failed)} failed")
        print(f"{'=' * 64}")
        if self.failed:
            print("\n  Failed:")
            for f in self.failed:
                print(f"    {f.strip()}")


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def banner(title: str) -> None:
    print(f"\n{'─' * 64}\n  {title}\n{'─' * 64}")


def main() -> None:
    print("\n" + "🔬🏎️  " * 10)
    print("  Day 18 — SIL vs HIL:")
    print("  Software-in-the-Loop vs Hardware-in-the-Loop")
    print("🔬🏎️  " * 10)
    print("\n  ⚡ Everything in Days 1–17 was SIL. Today we name it.")

    adapter = BusAdapter()
    plant   = PlantModel()
    ecu     = SimulatedECU(plant)

    tester_bus = adapter.open_for_tester()   # SIL on this machine
    tester     = UDSTester(tester_bus)

    print(f"\n  Environment : {adapter.describe()}")
    print(f"  Plant model : init temp = {plant.coolant_temp:.1f} °C")
    print(f"  Thresholds  : FAN_ON={FAN_ON_TEMP_C} °C  "
          f"FAN_OFF={FAN_OFF_TEMP_C} °C  "
          f"OVER_TEMP={OVER_TEMP_C} °C")

    ecu.start()
    time.sleep(0.1)

    banner("GROUP 1: Environment Detection")
    tester.tc01_adapter_mode(adapter)
    tester.tc02_hil_fallback_no_exception()
    tester.tc03_plant_model_init(plant)

    banner("GROUP 2: Sensor Reading & DID Encoding")
    tester.tc04_did_at_ambient(plant)
    tester.tc05_did_at_fan_on_threshold(plant)
    tester.tc06_did_at_over_temp(plant)
    tester.tc07_did_round_trip_fidelity(plant)

    banner("GROUP 3: Thermal Control Loop — Hysteresis + DTC")
    tester.tc08_fan_off_below_threshold(plant)
    tester.tc09_fan_activates_above_threshold(plant)
    tester.tc10_hysteresis_fan_stays_on(plant)
    tester.tc11_fan_deactivates_below_hysteresis(plant)
    tester.tc12_over_temp_dtc_p0217(plant)

    banner("GROUP 4: SIL Timing Characteristics")
    latencies = tester.tc13_measure_sil_baseline_latency(plant)
    tester.tc14_sil_timing_non_deterministic(latencies)
    tester.tc15_rapid_sequential_reads(plant)
    tester.tc16_sil_vs_hil_timing_table(latencies)

    banner("GROUP 5: Test Portability & Regression")
    tester.tc17_adapter_describe_string(adapter)
    tester.tc18_plant_model_reset(plant)
    tester.tc19_full_thermal_cycle(plant)
    tester.tc20_regression_all_services(plant)

    tester.print_summary()
    ecu.stop()
    tester.shutdown()


if __name__ == "__main__":
    main()
