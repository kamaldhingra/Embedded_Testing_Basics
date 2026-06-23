# Day 17: ECU Flashing — RequestDownload, TransferData, RequestTransferExit

> **Professor Embed says:** "You've mastered reading data, writing DIDs, locking seeds,
> chaining ISO-TP frames, and sniffing DTCs. Today we do the big one — we *overwrite the
> brain*. Buckle up, because flashing a firmware image into an ECU is the most
> consequential thing a tester's Python script will ever do."
>
> **Prerequisites:** Days 1–16 (especially UDS Days 12–16 and ISO-TP Day 16)

---

## Quick Recap: Where We Are

| Day | Topic |
|-----|-------|
| 12  | UDS sessions + ECUReset |
| 13  | ReadDID / WriteDID |
| 14  | SecurityAccess + RoutineControl |
| 15  | NRCs + DTCs + TesterPresent |
| 16  | ISO-TP state machine + multi-frame |
| **17** | **ECU Flashing — the full firmware update pipeline** |

---

## What Is ECU Flashing?

**ECU flashing** (also called *reprogramming* or *reflashing*) is the process of writing
a new firmware binary image into the ECU's internal flash memory — while the ECU is still
alive on the CAN bus.

> 🌉 **From your world:** Flashing an ECU is exactly like a **zero-downtime rolling
> deployment** of a back-end service — except the service controls your car's brakes,
> and there is no rollback button once you've erased the old firmware.

---

## The Ten-Step Flash Pipeline

Every OEM flash sequence is a variation of this choreography:

```
TESTER                              ECU
  │                                   │
  ├──0x10 0x02──────────────────────►│  programmingSession
  │◄──────────────────────0x50 0x02──┤  ✓ in programming mode
  │                                   │
  ├──0x27 0x01──────────────────────►│  requestSeed
  │◄──────────────────────0x67 0x01──┤  seed = 0xDEAD1234
  │                                   │
  ├──0x27 0x02  key=0x1EFCED76──────►│  sendKey  (seed XOR 0xC0FFEE42)
  │◄──────────────────────0x67 0x02──┤  ✓ unlocked
  │                                   │
  ├──0x28 0x03 0x01─────────────────►│  CommunicationControl: stop normal tx
  │◄──────────────────────0x68 0x03──┤  ✓ silenced
  │                                   │
  ├──0x31 0x01 0xFF00────────────────►│  EraseMemory — START
  │◄──────────────────────0x71 0x01──┤  RS_RUNNING (erasure started)
  │                                   │
  │  [poll 0x31 0x03 0xFF00 until…]  │
  │◄──────────────────────0x71 0x03──┤  RS_COMPLETED (erase done)
  │                                   │
  ├──0x34 addr+size──────────────────►│  RequestDownload
  │◄──────────────────────0x74──────┤  maxBlockSize = 512 bytes
  │                                   │
  ├──0x36 0x01 [data 0..511]─────────►│  TransferData block 1
  │◄──────────────────────0x76 0x01──┤  ✓
  ├──0x36 0x02 [data 512..1023]──────►│  TransferData block 2
  │◄──────────────────────0x76 0x02──┤  ✓
  │  … (N blocks total) …            │
  ├──0x36 0xFE [data…]───────────────►│  TransferData block 0xFE
  │◄──────────────────────0x76 0xFE──┤  ✓
  ├──0x36 0xFF [data…]───────────────►│  TransferData block 0xFF
  │◄──────────────────────0x76 0xFF──┤  ✓  (counter wraps next)
  ├──0x36 0x00 [data…]───────────────►│  TransferData block 0x00 (post-wrap)
  │◄──────────────────────0x76 0x00──┤  ✓
  │                                   │
  ├──0x37 [CRC-32]───────────────────►│  RequestTransferExit
  │◄──────────────────────0x77──────┤  ✓ checksum verified
  │                                   │
  ├──0x31 0x01 0xFF01────────────────►│  CheckProgrammingDependencies
  │◄──────────────────────0x71 0x01──┤  result = 0x00 (pass)
  │                                   │
  ├──0x28 0x00 0x01──────────────────►│  CommunicationControl: re-enable
  │◄──────────────────────0x68 0x00──┤  ✓ talking again
  │                                   │
  ├──0x11 0x01───────────────────────►│  ECUReset hardReset
  │◄──────────────────────0x51 0x01──┤  ✓ booting new firmware…
  │                                   │
  ├──0x22 0xF1 0x89──────────────────►│  ReadDID SW version
  │◄──────────────────────0x62──────┤  "2.5.0-release" ✓
```

---

## Concept Deep-Dives

### 1. Why a Separate Programming Session?

When the ECU switches to `programmingSession` (`0x10 0x02`):

| Feature | Default / Extended | Programming |
|---------|--------------------|-------------|
| Normal app running | ✓ | ✗ (bootloader only) |
| Flash memory writes | ✗ | ✓ |
| Watchdog (normal) | ✓ | Disabled/stretched |
| Periodic CAN msgs | ✓ | Suppressed by CommCtrl |
| S3 timeout | 5 s | 5 s (shorter — ECU must stay active) |

> 🌉 **From your world:** `programmingSession` is the embedded equivalent of putting a
> web server into **maintenance mode** — it stops serving normal requests so it can be
> safely patched.

---

### 2. SecurityAccess in Programming: Level 1 vs. The Algorithm

In Day 14 you used SecurityAccess with a simple XOR key. The same sub-functions (0x01
requestSeed / 0x02 sendKey) are used for programming, but OEMs use stronger key-derivation
algorithms — sometimes AES-CMAC, sometimes RSA challenge-response.

In this simulation: `key = seed XOR 0xC0FFEE42` (deliberately distinct from Day 14's
`0xDEADBEEF` to remind you that **each security level uses a different secret**).

> ⚠️ **Security note:** A real OEM never ships the key algorithm in the tester binary
> — it lives in an HSM (Hardware Security Module) or a cloud signing service.

---

### 3. CommunicationControl (0x28) — The Bus Mute Switch

**Why mute normal traffic during a flash?**

An ECU typically sends 10–50 periodic CAN frames per second. During TransferData we're
pumping 512-byte UDS messages as fast as the bus allows (up to ~80% bus load on a
500 kbps bus). Periodic signals compete for bandwidth and can cause:

- Flow-control frame collisions
- N_BS timeout (ECU stops waiting for consecutive frames)
- Corrupted block counters

`0x28 0x03` = `disableRxAndTx` silences the ECU's own transmissions.

> 🌉 **From your world:** `CommunicationControl` is the embedded equivalent of a
> **circuit breaker** pattern — you reduce load to zero before a critical operation.

Sub-functions:

| Value | Meaning |
|-------|---------|
| `0x00` | enableRxAndTx — normal operations |
| `0x01` | enableRxAndDisableTx |
| `0x02` | disableRxAndEnableTx |
| `0x03` | disableRxAndTx (fully mute) |

---

### 4. RequestDownload (0x34) — The Handshake

```
Request: [0x34, dataFormatId, addrAndLengthFormatId, address_bytes…, size_bytes…]
```

| Byte | Name | Meaning |
|------|------|---------|
| `dataFormatId` | Compression / encryption | 0x00 = raw, uncompressed |
| `addrAndLengthFormatId` | Format nibbles | High nibble = # size bytes, low nibble = # addr bytes |
| `addr_bytes` | Target memory address | Where to write firmware (e.g. 0x08000000 for ARM Cortex-M) |
| `size_bytes` | Total firmware size | Bytes to expect across all TransferData blocks |

```
Response: [0x74, lengthFormatId, maxBlockSize_H, maxBlockSize_L]
```

**`maxBlockSize` is crucial.** The ECU says: "I can handle up to N data bytes per
TransferData frame." If the tester exceeds this, the ECU has every right to reject the
block with NRC `0x72`.

> 🌉 **From your world:** `RequestDownload` is the `Content-Length` + `Transfer-Encoding:
> chunked` headers negotiation in an HTTP multipart upload — you tell the server how
> much is coming, and the server tells you the max chunk size it can accept.

---

### 5. TransferData (0x36) — The Payload Stream

```
Request: [0x36, blockSequenceCounter, data_byte_0, data_byte_1, …]
Response: [0x76, blockSequenceCounter]
```

**Block counter rules (ISO 14229-1, §14.6.3):**

```
First block:  0x01
Second block: 0x02
…
Block 0xFE:   0xFE
Block 0xFF:   0xFF
Block after 0xFF: 0x00   ← wrap to ZERO (not back to 0x01)
Block after 0x00: 0x01   ← then back to 0x01
```

So the sequence is: `01 02 … FE FF 00 01 02 …`

> ⚠️ **Common tester bug:** Wrapping counter to 0x01 instead of 0x00 after 0xFF.
> The ECU will respond with NRC `0x73` (wrongBlockSequenceCounter) and the entire
> flash attempt fails.

> 🌉 **From your world:** The block counter is the embedded equivalent of a **TCP
> sequence number** — it guarantees ordering and detects gaps. Drop even one block
> and the receiver knows immediately.

**How chunking works (max block = 512 bytes):**

```python
offset = 0
counter = 1
while offset < len(firmware):
    chunk = firmware[offset : offset + max_block]   # ≤512 bytes
    send([0x36, counter] + list(chunk))
    resp = recv()
    assert resp == [0x76, counter]
    offset  += len(chunk)
    counter  = (counter + 1) & 0xFF
    if counter == 0: counter = 0   # 0x00 is valid!
```

---

### 6. RequestTransferExit (0x37) — Finalise and Verify

```
Request:  [0x37, crc_b3, crc_b2, crc_b1, crc_b0]   (CRC-32 of transmitted data)
Response: [0x77]
```

The positive response is a single byte: `0x77` (= 0x37 + 0x40). That's it. That's the
"your firmware has been written successfully" acknowledgement.

The CRC payload is optional per spec, but every serious OEM includes it. The ECU
computes the CRC over the received `data_buffer` and compares against the tester's value.
Mismatch → NRC `0x72` (generalProgrammingFailure).

> 🌉 **From your world:** `RequestTransferExit` is like POSTing the final `sha256sum`
> after a chunked file upload. The server (ECU) re-hashes the received data and rejects
> if they don't match.

---

### 7. CheckProgrammingDependencies (0x31 0xFF01)

After the flash exits cleanly, the ECU runs an internal self-check:

- Is the firmware CRC embedded in the image header valid?
- Are all required calibration datasets present?
- Are SW/HW compatibility version bytes correct?
- Is the bootloader still intact?

If any check fails → NRC `0x72` or a routine result byte of `0x01` (fail).

> 🌉 **From your world:** This is a post-deployment **smoke test** — the same way
> your CI/CD runs a health-check endpoint immediately after deploying a new version.

---

### 8. RCRRP (NRC 0x78) — "Still Writing, Please Hold"

Flash memory write cycles take ~10 ms per 4 KB page. A 128 KB firmware image has 32
pages × 10 ms = ~320 ms of pure write time, ignoring transfer overhead.

During this time, the ECU may respond to `0x37` or `0x31 0xFF01` with:

```
[0x7F, SID, 0x78]   ← requestCorrectlyReceivedResponsePending
```

This is **not an error**. It means "I received your request; I'm still processing;
extend your timeout and I'll send a real response shortly."

A tester that treats NRC `0x78` as a failure and retries will corrupt the flash.

> 🌉 **From your world:** RCRRP is exactly HTTP `102 Processing` — the server
> acknowledges the request but hasn't completed it yet. Do NOT retry.

```
TESTER                       ECU (still writing flash)
  │                               │
  ├──0x37 [CRC]─────────────────►│
  │◄──────────── 0x7F 0x37 0x78 ─┤   "writing page 1/32…"
  │   (wait 1–5 s, do not retry) │
  │◄──────────── 0x7F 0x37 0x78 ─┤   "writing page 18/32…"
  │◄──────────────────── 0x77 ───┤   DONE — positive response at last
```

---

### 9. Flash Failure NRCs

| NRC | Value | Meaning | HTTP analogy |
|-----|-------|---------|--------------|
| uploadDownloadNotAccepted | `0x70` | Wrong address or ECU not ready for download | `400 Bad Request` |
| transferDataSuspended     | `0x71` | Ongoing transfer was interrupted/aborted | `408 Request Timeout` |
| generalProgrammingFailure | `0x72` | Flash write failed or CRC mismatch | `500 Internal Server Error` |
| wrongBlockSequenceCounter | `0x73` | Block counter out of sequence | `409 Conflict` (out-of-order) |

---

### 10. Flash Timing — Why It's Slow

| Operation | Typical time |
|-----------|-------------|
| Erase one 4 KB flash sector | 8–20 ms |
| Write one 256-byte row | 0.5–1 ms |
| Full 128 KB erase + write | 300–800 ms |
| CRC verification | < 5 ms |
| CheckProgrammingDependencies | 50–200 ms |
| ECU boot after reset | 100–500 ms |
| **Total end-to-end** | **~1–2 seconds** (fast ECU, 500 kbps CAN) |

Real automotive flash sessions often take **2–10 minutes** for large ECUs because:
- Firmware images can be 1–8 MB
- CAN FD or DoIP (CAN over IP) speeds things up; classical CAN at 500 kbps is a bottleneck
- RCRRP waits accumulate

---

## Test Cases Overview

| TC  | Group | What It Tests | Expected |
|-----|-------|---------------|----------|
| TC01 | Pre-conditions | Read SW version before flash | `2.4.1-release` |
| TC02 | Pre-conditions | RequestDownload in default session | NRC 0x22 |
| TC03 | Pre-conditions | RequestDownload before erase | NRC 0x22 |
| TC04 | Session | Enter programmingSession | Positive 0x50 |
| TC05 | Security | SecurityAccess unlock | Positive 0x67 |
| TC06 | CommCtrl | Disable normal tx | Positive 0x68 |
| TC07 | EraseMemory | Start routine → RS_RUNNING | Positive 0x71 |
| TC08 | EraseMemory | Poll to RS_COMPLETED | Positive 0x71 |
| TC09 | RequestDownload | Valid address + size | 0x74 + maxBlockSize |
| TC10 | RequestDownload | Wrong address (SRAM) | NRC 0x70 |
| TC11 | TransferData | All firmware blocks | 0x76 per block |
| TC12 | TransferData | Out-of-sequence block | NRC 0x22 |
| TC13 | TransferExit | CRC verify | Positive 0x77 |
| TC14 | CheckProgDeps | Post-flash integrity | result 0x00 |
| TC15 | CommCtrl | Re-enable normal tx | Positive 0x68 |
| TC16 | ECUReset | Hard reset | Positive 0x51 |
| TC17 | Post-flash | SW version = new firmware | `2.5.0-release` |
| TC18 | Error path | TransferExit before download | NRC 0x22 |
| TC19 | Error path | Erase without security | NRC 0x33 |
| TC20 | Error path | CommCtrl in default session | NRC 0x22 |

---

## Expected Output (All 20 TCs Pass)

```
💾⚡  💾⚡  💾⚡  💾⚡  💾⚡  💾⚡  💾⚡  💾⚡  💾⚡  💾⚡
  Day 17 — ECU Flashing: 0x34 RequestDownload,
           0x36 TransferData, 0x37 RequestTransferExit
💾⚡  💾⚡  💾⚡  💾⚡  💾⚡  💾⚡  💾⚡  💾⚡  💾⚡  💾⚡

  Firmware: 2.5.0-release, 131076 bytes, CRC-32=0xXXXXXXXX

────────────────────────────────────────────────────────────────
  GROUP 1: Pre-Flash Preconditions
────────────────────────────────────────────────────────────────
  ✅ PASS  TC01 Read initial SW version  [SID=0x62]
  ✅ PASS  TC01 SW version is OLD (pre-flash) ✓  [2.4.1-release]
  ✅ PASS  TC02 RequestDownload in default → NRC 0x22  [NRC=0x22]
  ✅ PASS  TC03 RequestDownload before erase → NRC 0x22  [NRC=0x22]

────────────────────────────────────────────────────────────────
  GROUP 2: Programming Session + SecurityAccess + CommCtrl
────────────────────────────────────────────────────────────────
  ✅ PASS  TC04 Enter programmingSession  [SID=0x50]
  ✅ PASS  TC04 Session sub-function echoed = 0x02 ✓
  ✅ PASS  TC05 SecurityAccess unlock in programming session ✓
  ✅ PASS  TC06 CommCtrl disable normal tx  [SID=0x68]
  ✅ PASS  TC06 CommCtrl echo = 0x03 ✓

────────────────────────────────────────────────────────────────
  GROUP 3: EraseMemory Routine
────────────────────────────────────────────────────────────────
  ✅ PASS  TC07 EraseMemory start  [SID=0x71]
  ✅ PASS  TC07 EraseMemory status = RS_RUNNING ✓
  ✅ PASS  TC08 EraseMemory polled to RS_COMPLETED ✓

────────────────────────────────────────────────────────────────
  GROUP 4: RequestDownload Negotiation
────────────────────────────────────────────────────────────────
  ✅ PASS  TC09 RequestDownload valid  [SID=0x74]
  ✅ PASS  TC09 maxBlockSize negotiated  [512 bytes/block]
  ✅ PASS  TC10 RequestDownload wrong address → NRC 0x70  [NRC=0x70]

────────────────────────────────────────────────────────────────
  GROUP 5: TransferData — Chunked Firmware
────────────────────────────────────────────────────────────────
  ✅ PASS  TC11 All firmware blocks transferred ✓  [257 blocks, 131076 bytes]
  ✅ PASS  TC12 TransferData out of sequence → NRC 0x22  [NRC=0x22]

────────────────────────────────────────────────────────────────
  GROUP 6: RequestTransferExit + CheckProgDeps + CommCtrl
────────────────────────────────────────────────────────────────
  ✅ PASS  TC13 RequestTransferExit: CRC verified, response 0x77 ✓
  ✅ PASS  TC14 CheckProgDeps start  [SID=0x71]
  ✅ PASS  TC14 CheckProgDeps result = 0x00 (pass) ✓
  ✅ PASS  TC15 CommCtrl re-enable normal tx  [SID=0x68]
  ✅ PASS  TC15 CommCtrl echo = 0x00 ✓

────────────────────────────────────────────────────────────────
  GROUP 7: ECUReset + Post-Flash SW Version Verification
────────────────────────────────────────────────────────────────
  ✅ PASS  TC16 ECUReset hardReset → 0x51 ✓
  ✅ PASS  TC17 Read SW version post-flash  [SID=0x62]
  ✅ PASS  TC17 SW version is NEW (post-flash) ✓  [2.5.0-release]

────────────────────────────────────────────────────────────────
  GROUP 8: Error Path — NRC Validation
────────────────────────────────────────────────────────────────
  ✅ PASS  TC18 TransferExit before download → NRC 0x22  [NRC=0x22]
  ✅ PASS  TC19 Erase without security → NRC 0x33  [NRC=0x33]
  ✅ PASS  TC20 CommCtrl in default session → NRC 0x22  [NRC=0x22]

================================================================
  TEST SUMMARY: 27/27 passed, 0 failed
================================================================
```

---

## Software QA Bridge: The Full Flash Pipeline

| Flash Step | Web / Software Equivalent |
|-----------|--------------------------|
| `programmingSession` | Put server in **maintenance mode** |
| `SecurityAccess` level | **OAuth PKCE** or HSM signing token |
| `CommunicationControl` | **Circuit breaker** — stop all background jobs |
| `EraseMemory` routine | **Drop + recreate** database schema |
| `RequestDownload` | `Content-Length` + `multipart/form-data` header negotiation |
| `maxBlockSize` | Server's `max_allowed_packet` or chunk size |
| `TransferData` | Chunked HTTP upload with **sequence numbers** |
| Block counter wrap 0xFF→0x00 | TCP sequence number wrap-around handling |
| `RequestTransferExit` + CRC | `sha256sum` post-upload hash verification |
| RCRRP (NRC 0x78) | HTTP **102 Processing** — async long-running job |
| `CheckProgrammingDependencies` | Post-deploy **smoke test / health check** |
| `ECUReset` | Container **restart** / `systemctl restart` |
| Read SW version after reset | Verify `/version` endpoint returns new tag |

---

## Quiz

**Q1.** The `addrAndLengthFormatId` byte in a `RequestDownload` request is `0x44`.
How many address bytes and how many size bytes does the tester include?

<details><summary>Answer</summary>

High nibble `4` = **4 size bytes**. Low nibble `4` = **4 address bytes**.
Total = 3 (SID + fmt + addrFmt) + 4 (address) + 4 (size) = **11 bytes** in the request.

</details>

---

**Q2.** After sending block `0xFF`, what block counter should the tester use next?

<details><summary>Answer</summary>

`0x00` — **not** `0x01`. The counter wraps to zero, then increments to `0x01` on
the block after that. Wrapping directly to `0x01` causes NRC `0x73`.

</details>

---

**Q3.** The ECU responds to `RequestTransferExit` with `[0x7F, 0x37, 0x78]`.
What should the tester do?

<details><summary>Answer</summary>

**Wait and do nothing.** NRC `0x78` means the ECU is still writing flash and will
send a real response (positive `0x77` or negative) within a few seconds. Retrying
or sending a new request risks corrupting the flash write in progress.

</details>

---

**Q4.** Why does the tester send `CommunicationControl 0x28 0x03` before starting the
flash? What happens on the bus if it skips this step?

<details><summary>Answer</summary>

The ECU broadcasts periodic CAN frames (engine RPM, speed, temperatures, etc.) that
compete with the large ISO-TP multi-frame UDS messages. Without muting:
- Bus load spikes (>80%), causing arbitration collisions
- The ECU may miss flow-control frames → N_BS timeout → transfer aborted
- Block counter gets out of sync

</details>

---

**Q5.** You have a 512 KB firmware image to flash via classical CAN at 500 kbps.
The ECU sets maxBlockSize = 512 bytes. Roughly how many `TransferData` blocks do you
need, and what is the theoretical minimum time just for the data transmission?

<details><summary>Answer</summary>

**Blocks:** ⌈512 × 1024 / 512⌉ = **1024 blocks**.

Each 512-byte block needs ~11 ISO-TP consecutive frames (7 bytes/frame payload after
PCI). At 500 kbps with 8-byte frames: each frame ≈ 128 µs. 11 frames ≈ 1.4 ms/block.
1024 blocks × 1.4 ms ≈ **~1.4 seconds** for raw data only.

Add erase time (512 KB / 4 KB sector × 15 ms = ~2 s), write time (~1 s), RCRRP waits,
and CheckProgDeps → **total ~5–10 seconds** in a fast simulation.

</details>

---

## Key Takeaways

1. **Order is everything.** Skip any step in the ten-step pipeline and the ECU returns
   NRC `0x22` (conditions not correct) — not a nice error message, just a hex code.

2. **maxBlockSize is a contract.** The ECU sets it in the `RequestDownload` response.
   Exceeding it is undefined behaviour (typically NRC `0x72` or silent data corruption).

3. **Block counter wraps 0xFF → 0x00, then → 0x01**, not directly to 0x01.
   This is the #1 beginner mistake in flash tool development.

4. **NRC 0x78 is not a failure.** It means "I'm busy writing; extend your timeout."
   A tester that retries on 0x78 may corrupt the flash mid-write.

5. **CommunicationControl is load management.** Mute the ECU before transferring data;
   re-enable it before ECUReset so the vehicle bus returns to normal.

6. **SecurityAccess algorithm is security-level-specific.** Programming-level unlock
   uses a different (usually stronger) algorithm than diagnostic-level unlock.

7. **CheckProgrammingDependencies is the safety net.** Even if TransferExit succeeds,
   a failed dependency check (0xFF01) means the image is rejected before the reset.
   The old firmware is gone. The ECU is bricked until re-flashed correctly.

---

## What's Next? (Day 18 Options)

| Option | Topic |
|--------|-------|
| **18A** | **DoIP (Diagnostics over IP)** — same UDS services, but over Ethernet + TCP |
| **18B** | **OBD-II** — Mode 01/02/03, PIDs, emissions readiness monitors |
| **18C** | **Automotive Cybersecurity** — UDS attack surfaces, fuzzing, AUTOSAR SecOC |
| **18D** | **CAN FD Deep Dive** — BRS bit, ESI bit, up to 64-byte payload, ISO 11898-1:2015 |

---

## Running the Simulation

```bash
cd "Day-17_ECU_Flashing"
pip install python-can
python ecu_flash.py
```

**What to watch:**

- TC01 reads SW version `2.4.1-release` (old firmware installed)
- TC07/TC08 show the erase polling loop in action
- TC09 returns `maxBlockSize = 512` — watch for this negotiation
- TC11 counts 257 blocks (128 KB + 4-byte CRC + padding, in 512-byte chunks)
- TC11 also exercises the block counter wrap (0xFF → 0x00 → 0x01)
- TC13 sends CRC-32 and gets `0x77` back — one byte, that's the "flash success" signal
- TC17 reads SW version `2.5.0-release` — the ECU is now running new firmware
- TC18/TC19/TC20 confirm error paths all return the right NRCs

> **Runtime:** approximately 2–4 seconds (dominated by TC08 erase polling + TC11 transfer)

---

*Generated from a live mentoring session with Professor Embed. 🚗⚡🔥*
