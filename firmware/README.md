# Reference firmware — physical oscilloscope

The Kirho oscilloscope (`OSC`) can display samples from a microcontroller over
USB-CDC serial. This directory defines the binary stream that firmware must
send. No board-specific firmware project is bundled: adapt the protocol to
your board, ADC, and toolchain.

## Reference hardware

| MCU | ADC | Notes |
| --- | --- | --- |
| Raspberry Pi Pico (RP2040) | 12-bit | A practical target for Pico SDK or MicroPython. |
| Raspberry Pi Pico 2 (RP2350) | 12-bit | Adapt the SDK project to the selected board. |
| STM32 Black Pill (F411) | 12-bit | Implement the protocol in the STM32 environment of your choice. |

Actual sample rate depends on the ADC, firmware, clock, and USB transport.
Measure it on the physical target before using it for a real measurement.

## Analog front end

The Pico ADC range is **0–3.3 V**. Condition the signal under test so it
stays inside that range; never connect a negative or higher-voltage signal
directly to the ADC.

```
 probe             1 kΩ
 ───●──────────[==========]────●──── ADC pin (for example GP26)
                                │
                            ━━━━━━━━━ 1 nF capacitor (anti-aliasing)
                                │
                                ┴ GND
```

For a ±5 V signal, add attenuation and a DC offset (typically 1.65 V) before
the ADC. Higher-voltage signals need a properly rated attenuator and, where
appropriate, an op-amp buffer. The front end must protect both the board and
the device under test.

## Binary protocol

Each frame is variable length and uses little-endian integers:

```
Offset  Bytes  Field
─────────────────────────────────────────────────────────────
0       2      Magic header: 0xAA 0x55
2       4      ts_us : uint32 — timestamp of the first sample (µs)
6       2      dt_us : uint16 — interval between samples (µs)
8       2      N     : uint16 — number of (channel A, channel B) pairs
10      4·N    samples: int16 channel A, int16 channel B, in mV
```

- `ts_us` must be monotonic between frames. Kirho uses the first received
  timestamp as time zero.
- `dt_us` is the per-channel sample interval. At 100 kS/s, send `10`.
- `N` is normally 32–256. Smaller blocks add USB overhead; larger ones add
  display latency.
- Send channel B as `0` when only channel A is in use; the decoder always
  expects pairs.
- The receiver resynchronizes by scanning for `0xAA 0x55` after corrupted or
  incomplete data.

The decoder is implemented in
[`kirho/engine/hw_stream.py`](../kirho/engine/hw_stream.py). It converts each
signed millivolt sample to volts with:

```
displayed_volts = gain × (raw_millivolts / 1000) + offset_volts
```

Set the matching gain and offset for each channel in **Hardware…** in the
oscilloscope window.

## Minimal MicroPython sender (RP2040)

This is a starting point, not a calibrated instrument. It samples GP26 and
GP27 and writes the required frames to USB serial.

```python
import struct, sys, time
from machine import ADC, Pin

adc_a = ADC(Pin(26))
adc_b = ADC(Pin(27))
BLOCK_N = 64
DT_US = 50                         # requested 20 kS/s per channel

buf = bytearray(10 + BLOCK_N * 4)
buf[:2] = b'\xaa\x55'
struct.pack_into('<HH', buf, 6, DT_US, BLOCK_N)
ts_us = 0

while True:
    t0 = time.ticks_us()
    for i in range(BLOCK_N):
        a_mv = adc_a.read_u16() * 3300 // 65535
        b_mv = adc_b.read_u16() * 3300 // 65535
        struct.pack_into('<hh', buf, 10 + 4 * i, a_mv, b_mv)
        while time.ticks_diff(time.ticks_us(), t0) < (i + 1) * DT_US:
            pass
    struct.pack_into('<I', buf, 2, ts_us)
    sys.stdout.buffer.write(buf)
    ts_us += BLOCK_N * DT_US
```

Save it as `main.py`, upload it to the board (for example,
`mpremote cp main.py :main.py`), then restart the board. Its real sample rate
depends on the MicroPython build and board; verify it with a known signal.

## Connect it to Kirho

1. Place an `OSC` in the canvas and double-click it.
2. Select **Hardware…** in the oscilloscope window.
3. Choose the serial port, or **⟨Mock device⟩** to test without hardware.
4. Enter the gain and offset that match the analog front end.
5. Connect. The oscilloscope then displays the incoming samples in real time.

`pyserial` is optional. It is required for a physical serial port but not for
the built-in mock device.

## Troubleshooting

- **No serial port**: refresh the list; on Linux, check serial permissions
  (for example, add the user to the `dialout` group and sign in again).
- **Flat trace**: verify the ADC input and calibration settings.
- **Clipped trace**: the input is outside the ADC range; fix attenuation or
  offset before the ADC.
- **Broken-looking trace**: reduce `BLOCK_N` if the firmware or USB path is
  overrunning buffers.
