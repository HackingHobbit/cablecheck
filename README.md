# Cable Check

Find out what a USB-C cable can do, using only your Mac.

Plug a cable in and Cable Check tells you:

- **Charging:** up to 60 W, 100 W, or 240 W
- **Data speed:** USB 2.0 (480 Mbps), 5 Gbps, 10/20 Gbps, 40 Gbps, or 80 Gbps
- **Video:** whether it can carry a display signal
- **Build:** passive or active/optical, rough length, chip maker
- **Whether data actually works**, and the real link speed, when a device is on the other end

It comes as a Mac app with a live window, and as a command-line tool.

## How it works

Most good USB-C cables have a tiny chip in the plug, called an *e-marker*,
that describes what the cable supports. macOS already reads this chip over
USB Power Delivery whenever a cable is connected. Cable Check reads that data
from the I/O Registry (`ioreg`) and decodes it. It also reads the live USB and
Thunderbolt link on each port.

No drivers, no extra hardware, no admin rights.

## Two ways to test a cable

| Test | How | What you learn |
|---|---|---|
| **Loop test** | Both ends into two USB-C ports on the Mac | Cable chip only: charging rating, rated speed |
| **Device test** | Mac to a phone, drive, or dock | Chip info **plus** whether data works and the real link speed |

### Which test tells you what

| Test | Charging rating | Charge-only or data? | Fast data (5–10 Gbps) | Thunderbolt (40 Gbps+) | Actual link speed |
|---|:-:|:-:|:-:|:-:|:-:|
| Loop test | ✅ | ❌ | chip's claim | chip's claim | ❌ |
| Phone | ✅ | ✅ | depends on phone | ❌ | ✅ |
| USB SSD / flash drive | ✅ | ✅ | ✅ | ❌ | ✅ |
| Thunderbolt SSD or dock | ✅ | ✅ | ✅ | ✅ | ✅ |
| Charger / battery pack | ✅ | ❌ | chip's claim | chip's claim | ❌ |

## Limits

- **Cables with no chip** are basic cables: up to 60 W, and USB 2.0 data at
  most. A loop test can't tell a charge-only cable from a USB 2.0 data cable,
  because two Mac ports are both USB "hosts" and won't talk to each other over
  the USB 2.0 wires. Use the device test (e.g. with a phone) to find out.
- **The device caps the speed result.** A phone that only does USB 2.0 will
  show 480 Mbps even on a 40 Gbps cable.
- **Charging watts come from the cable's chip**, not a measurement. The Mac
  only sends a little power to a connected device.
- **It trusts the chip.** A damaged cable, or one with a dishonest chip, can't
  be caught by software. A hardware cable tester checks the actual wires.

## Requirements

- Apple Silicon Mac (tested on an M4 Pro MacBook Pro, macOS 27)
- USB-C to USB-C cables (or adapters)
- Python 3 (the copy bundled with Xcode Command Line Tools is fine)
- To build the app: Xcode Command Line Tools (`xcode-select --install`)

## Usage

### App

```bash
./app/build.sh
open "Cable Check.app"
```

The window shows a live card for each USB-C port and the table of what each
test can tell you.

### Command line

```bash
./cablecheck.py           # show every USB-C port right now
./cablecheck.py --watch   # wait for a cable, then report it
./cablecheck.py --raw     # also show the raw cable-chip values
./cablecheck.py --json    # machine-readable output (used by the app)
```

Example:

```
Port 3: connected to USB Flash Disk
    Verdict:  DATA WORKS. 40Gbps (USB4 / Thunderbolt 3/4 class) cable, 240 W
    Charging: up to 240 W (5 A, 48 V EPR). Fastest charging, any laptop.
    Data:     linked at 10 Gbps with General USB Flash Disk.
              The cable is rated 40 Gbps, so the device is the limit, not the cable.
    Video:    Yes (DisplayPort over USB-C)
    Type:     Passive cable, about 1 m or shorter
```

## Project layout

```
cablecheck.py        Reads ports and decodes cable chips (CLI + JSON for the app)
app/CableCheck.swift SwiftUI window
app/build.sh         Builds "Cable Check.app" with the script bundled inside
```
