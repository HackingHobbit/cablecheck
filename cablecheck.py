#!/usr/bin/env python3
"""cablecheck: identify a USB-C cable from this Mac.

Two ways to test a cable:
  * Loop test:   plug both ends into two USB-C ports on this Mac.
  * Device test: plug one end into the Mac and the other into a phone
                 (or any USB device). This also proves whether data works
                 and shows the speed the link actually runs at.

Reads the cable's built-in chip (the "e-marker") that macOS queries over
USB Power Delivery, plus the live USB link, and gives a plain verdict.

  cablecheck          show what's on every USB-C port right now
  cablecheck --watch  wait for you to plug a cable in, then report it
  cablecheck --raw    also print the raw values read from the cable chip
"""
import plistlib
import re
import subprocess
import sys
import time

# A few common USB-IF vendor IDs. Anything else is shown as a hex number.
VENDORS = {
    0x05AC: "Apple", 0x8087: "Intel", 0x04B4: "Cypress", 0x291A: "Anker",
    0x050D: "Belkin", 0x04E8: "Samsung", 0x18D1: "Google", 0x2230: "Plugable",
    0x0BDA: "Realtek", 0x2109: "VIA Labs", 0x1D5C: "Fresco Logic",
}

SPEEDS = {
    0: ("USB 2.0", "480 Mbps", "slow data: fine for phones, keyboards, syncing"),
    1: ("USB 5Gbps (USB 3.2 Gen 1)", "5 Gbps", "fast data: external drives, docks, video"),
    2: ("USB 10Gbps (USB 3.2 Gen 2)", "10-20 Gbps", "fast data: SSDs, docks, video"),
    3: ("40Gbps (USB4 / Thunderbolt 3/4 class)", "40 Gbps", "very fast: Thunderbolt docks, fast SSDs, 5K/6K displays"),
    4: ("80Gbps (USB4 v2 / Thunderbolt 5 class)", "80 Gbps", "fastest: Thunderbolt 5 docks and displays"),
}
# Fastest plain-USB link (in Mbps) a cable of each speed code can carry.
CABLE_USB_MAX = {0: 480, 1: 5000, 2: 10000, 3: 40000, 4: 80000}

PASSIVE_LENGTH = {1: "about 1 m or shorter", 2: "about 2 m", 3: "about 3 m", 4: "about 4 m",
                  5: "about 5 m", 6: "about 6 m", 7: "about 7 m", 8: "longer than 7 m"}
ACTIVE_LENGTH = {9: "very long (~100 m)", 10: "very long (~200 m)", 11: "very long (~300 m)"}


def read_ports():
    """Return the USB-C port entries from the I/O Registry, with their children."""
    out = subprocess.run(
        ["ioreg", "-a", "-l", "-r", "-c", "AppleHPMInterfaceType10"],
        capture_output=True, check=True).stdout
    if not out.strip():
        return []
    ports = plistlib.loads(out)
    ports = [p for p in ports if p.get("PortTypeDescription") == "USB-C"]
    return sorted(ports, key=lambda p: p.get("PortNumber", 0))


def children(node):
    return node.get("IORegistryEntryChildren", [])


def find_child(node, name):
    for c in children(node):
        if c.get("IORegistryEntryName") == name:
            return c
    return None


def vdo_int(b):
    # VDOs are stored as 4 little-endian bytes.
    return int.from_bytes(b, "little") if isinstance(b, (bytes, bytearray)) else int(b)


def vendor_name(vid):
    if vid is None:
        return "unknown"
    return VENDORS.get(vid, f"vendor 0x{vid:04X}")


def rate_mbps(text):
    """'480 Mbps (High Speed)' -> 480, '10 Gbps' -> 10000."""
    m = re.search(r"([\d.]+)\s*([MG])bps", text or "")
    if not m:
        return None
    n = float(m.group(1))
    return int(n * 1000) if m.group(2) == "G" else int(n)


def fmt_rate(mbps):
    return f"{mbps // 1000} Gbps" if mbps >= 1000 else f"{mbps} Mbps"


def decode_cable(sopp):
    """Decode the SOP' (cable plug) identity into a dict."""
    meta = sopp.get("Metadata", {}) or {}
    vdos = [vdo_int(v) for v in meta.get("VDOs", [])]
    info = {
        "vendor_id": sopp.get("Vendor ID", meta.get("Vendor ID")),
        "product_id": sopp.get("Product ID", meta.get("Product ID")),
        "vdos": vdos,
    }
    if len(vdos) < 4:
        info["error"] = "cable chip answered, but without the details needed"
        return info

    header = vdos[0]
    active = ((header >> 27) & 0x7) == 4      # 3 = passive cable, 4 = active cable
    cable = vdos[3]                           # Passive Cable VDO, or Active Cable VDO1

    speed = cable & 0x7
    current = (cable >> 5) & 0x3
    latency = (cable >> 13) & 0xF
    epr = bool((cable >> 17) & 0x1)

    amps = {1: 3, 2: 5}.get(current, 3)
    watts = 240 if (epr and amps == 5) else 20 * amps

    optical = False
    if active and len(vdos) >= 5:
        optical = bool((vdos[4] >> 9) & 0x1)  # "optically isolated" bit

    info.update({
        "active": active,
        "optical": optical,
        "speed": speed,
        "amps": amps,
        "watts": watts,
        "length": (ACTIVE_LENGTH if active else PASSIVE_LENGTH).get(
            latency, PASSIVE_LENGTH.get(latency, "unknown length")),
    })
    return info


def usb_links(port):
    """Live USB data links on a port: direct USB2/USB3, or tunneled over Thunderbolt."""
    links = []
    nodes = [c for c in children(port) if c.get("IORegistryEntryName", "").startswith("USB")]
    cio = find_child(port, "CIO")
    if cio:
        nodes += [c for c in children(cio) if c.get("IORegistryEntryName", "").startswith("USB")]
    for n in nodes:
        if not n.get("Active"):
            continue
        links.append({
            "kind": n.get("TransportTypeDescription", n.get("IORegistryEntryName")),
            "mbps": rate_mbps(n.get("DataRateDescription")),
            "product": n.get("Product"),
            "maker": n.get("Manufacturer"),
            "vendor_id": n.get("Vendor ID"),
            "tunneled": bool(n.get("Tunneled")),
        })
    return links


def is_charger(port):
    """True when this port is receiving power from the other end (a charger)."""
    power_in = find_child(port, "Power In")
    if not power_in:
        return False
    return any(c.get("IORegistryEntryName", "").endswith("[*]") for c in children(power_in))


def port_state(port):
    cc = find_child(port, "CC") or {}
    sopp = find_child(cc, "SOP'")
    sop = find_child(cc, "SOP")
    return {
        "number": port.get("PortNumber"),
        "connected": port.get("ConnectionActive", False),
        "transports": list(port.get("TransportsActive", []) or []),
        "cable": decode_cable(sopp) if sopp else None,
        "partner_vid": sop.get("Vendor ID") if sop else None,
        "usb": usb_links(port),
        "charger": is_charger(port),
        "thunderbolt": "CIO" in (port.get("TransportsActive") or []),
    }


def charge_line(cable):
    if cable is None or "error" in cable:
        return "up to 60 W (3 A). Plenty for phones and tablets, slower than max for big laptops."
    if cable["watts"] >= 240:
        return "up to 240 W (5 A, 48 V EPR). Fastest charging, any laptop."
    if cable["watts"] >= 100:
        return "up to 100 W (5 A). Fast charging for laptops."
    return "up to 60 W (3 A). Fast for phones/tablets, fine for most laptops."


def cable_detail_lines(cable):
    kind = "Active optical cable" if cable["optical"] else ("Active cable" if cable["active"] else "Passive cable")
    return [
        f"Type:     {kind}, {cable['length']}",
        f"Maker:    {vendor_name(cable['vendor_id'])} "
        f"(chip id 0x{(cable['vendor_id'] or 0):04X}:0x{(cable['product_id'] or 0):04X})",
    ]


def describe_loop(cable):
    """Verdict when only the cable chip is known (loop test, or no data link)."""
    if cable is None:
        return [
            "No chip found in this cable.",
            "",
            "Verdict:  BASIC USB-C cable",
            "Charging: " + charge_line(None),
            "Data:     USB 2.0 at most (480 Mbps), or charge-only.",
            "          A loop test can't tell those apart. Plug it into a phone",
            "          instead to find out.",
            "Video:    No (needs a faster cable).",
        ]
    if "error" in cable:
        return [f"Cable chip found, but: {cable['error']}"]
    name, rate, use = SPEEDS.get(cable["speed"], (f"unknown speed code {cable['speed']}", "?", ""))
    return [
        f"Verdict:  {name} cable, {cable['watts']} W",
        "Charging: " + charge_line(cable),
        f"Data:     rated {rate}: {use}",
        f"Video:    {'Yes (DisplayPort over USB-C)' if cable['speed'] >= 1 else 'No'}",
    ] + cable_detail_lines(cable)


def describe_device(s):
    """Verdict when a device is on the other end and we can see the live link."""
    cable = s["cable"] if s["cable"] and "error" not in s["cable"] else None
    links = s["usb"]
    lines = []

    if s["charger"] and not links:
        lines.append("The other end is a charger, so data can't be tested this way.")
        lines.append("")
        return lines + describe_loop(s["cable"])

    if not links:
        lines.append("Verdict:  NO DATA. The device connected for power only.")
        lines.append("Charging: " + charge_line(cable))
        lines.append("Data:     none seen. Either this is a charge-only cable, or the")
        lines.append("          device doesn't offer USB data (try a phone, unlocked).")
        if cable:
            lines.append("          (This cable has a chip, so charge-only is unlikely.)")
        return lines

    best = max(links, key=lambda l: l["mbps"] or 0)
    mbps = best["mbps"] or 0
    dev = best["product"] or "USB device"
    if best["maker"]:
        dev = f"{best['maker']} {dev}"

    if cable:
        name, rate, _ = SPEEDS.get(cable["speed"], ("unknown", "?", ""))
        cap = CABLE_USB_MAX.get(cable["speed"], 0)
        lines.append(f"Verdict:  DATA WORKS. {name} cable, {cable['watts']} W")
        lines.append("Charging: " + charge_line(cable))
        if s["thunderbolt"]:
            lines.append("Data:     Thunderbolt link is up, so the cable's full speed is in use.")
            lines.append(f"          (USB inside it runs at {fmt_rate(mbps)} to {dev}.)")
        else:
            lines.append(f"Data:     linked at {fmt_rate(mbps)} with {dev}.")
            if mbps < cap:
                lines.append(f"          The cable is rated {rate}, so the device is the limit, not the cable.")
            else:
                lines.append("          That's the cable's full rated USB speed.")
        lines.append(f"Video:    {'Yes (DisplayPort over USB-C)' if cable['speed'] >= 1 else 'No'}")
        lines += cable_detail_lines(cable)
    else:
        lines.append("No chip found in this cable.")
        lines.append("")
        if mbps > 480:
            lines.append("Verdict:  DATA WORKS, faster than USB 2.0, but the cable has no chip.")
            lines.append("          Unusual (most fast cables must have one). Works, but may be off-spec.")
        else:
            lines.append("Verdict:  BASIC USB 2.0 DATA cable (not charge-only)")
        lines.append("Charging: " + charge_line(None))
        lines.append(f"Data:     linked at {fmt_rate(mbps)} with {dev}.")
        if mbps < 480:
            lines.append("          That's the device's limit; the cable can do 480 Mbps.")
        lines.append("Video:    No (needs a faster cable).")
    return lines


def headline(s):
    if not s["connected"]:
        return "Empty"
    if s["usb"]:
        first = max(s["usb"], key=lambda l: l["mbps"] or 0)
        return f"Connected to {first['product'] or 'a USB device'}" + (" (Thunderbolt)" if s["thunderbolt"] else "")
    if s["charger"]:
        return "Connected to a charger"
    if s["partner_vid"]:
        return f"Connected to {vendor_name(s['partner_vid'])} device"
    return "Connected"


def lines_to_fields(lines):
    """Turn 'Label:  value' lines (with indented continuations) into pairs."""
    fields, notes = [], []
    for line in lines:
        if not line.strip():
            continue
        m = re.match(r"^(\w+):\s+(.*)$", line)
        if m and not line.startswith(" "):
            fields.append([m.group(1), m.group(2)])
        elif fields and line.startswith(" "):
            fields[-1][1] += " " + line.strip()
        else:
            notes.append(line.strip())
    return fields, notes


def loop_pairs(states):
    """Ports that look like two ends of one cable plugged into this Mac.

    Both ends show a connection, but neither side sees a USB device or a
    charger (two Mac ports have nothing to say to each other over USB)."""
    idle = [s["number"] for s in states
            if s["connected"] and not s["usb"] and not s["charger"] and not s["thunderbolt"]]
    return idle if len(idle) >= 2 else []


def analyze(states):
    loop = loop_pairs(states)
    loop_reader = None
    if loop:
        reads = [s["number"] for s in states if s["number"] in loop and s["cable"]]
        loop_reader = reads[0] if reads else loop[0]
    out = []
    for s in states:
        in_loop = s["number"] in loop
        if not s["connected"]:
            mode, lines = "empty", []
        elif in_loop:
            mode = "loop"
            lines = describe_loop(s["cable"]) if s["number"] == loop_reader else []
        elif s["charger"] and not s["usb"]:
            mode, lines = "charger", describe_device(s)
        elif s["usb"]:
            mode, lines = "device", describe_device(s)
        else:
            mode, lines = "nodata", describe_device(s)
        fields, notes = lines_to_fields(lines)
        verdict = next((v for k, v in fields if k == "Verdict"), "")
        out.append({
            "number": s["number"],
            "connected": bool(s["connected"]),
            "mode": mode,
            "headline": headline(s) if not in_loop else
                        "Loop test with port " + ", ".join(str(n) for n in loop if n != s["number"]),
            "verdict": verdict,
            "fields": [f for f in fields if f[0] != "Verdict"],
            "notes": notes,
            "loop_partner_reads_cable": in_loop and s["number"] != loop_reader,
            "has_chip": bool(s["cable"] and "error" not in s["cable"]),
            "raw": " ".join(f"{v:08X}" for v in (s["cable"] or {}).get("vdos", [])),
        })
    return out


def report(states, raw=False, only=None, loop=False):
    for s in states:
        if only is not None and s["number"] not in only:
            continue
        print(f"Port {s['number']}: ", end="")
        if not s["connected"]:
            print("empty")
            continue
        if s["usb"]:
            first = max(s["usb"], key=lambda l: l["mbps"] or 0)
            print(f"connected to {first['product'] or 'a USB device'}"
                  + (" (Thunderbolt)" if s["thunderbolt"] else ""))
        elif s["charger"]:
            print("connected to a charger")
        elif s["partner_vid"]:
            print(f"connected to {vendor_name(s['partner_vid'])} device")
        else:
            print("connected")
        body = describe_loop(s["cable"]) if loop else describe_device(s)
        for line in body:
            print("    " + line if line else "")
        if raw and s["cable"] and s["cable"].get("vdos"):
            print("    Raw:      " + " ".join(f"{v:08X}" for v in s["cable"]["vdos"]))
        print()


def watch(raw=False):
    def states():
        return [port_state(p) for p in read_ports()]

    def connected(sts):
        return {s["number"] for s in sts if s["connected"]}

    print("Plug in a cable. Either:")
    print("  * both ends into two free USB-C ports (loop test), or")
    print("  * one end into this Mac and the other into a phone or USB device.")
    print("(Watching... press Ctrl-C to stop.)\n")
    before = connected(states())
    while True:
        time.sleep(0.5)
        now = connected(states())
        new = now - before
        if new:
            print("Detected, reading the cable...", flush=True)
            # Give Power Delivery time to read the chip and the device time
            # to show up on USB. Stop early once a data link appears.
            deadline = time.time() + 8
            time.sleep(2)
            while True:
                sts = states()
                new = connected(sts) - before
                loop = len(new) >= 2
                has_data = any(s["usb"] for s in sts if s["number"] in new)
                if loop or has_data or time.time() > deadline:
                    break
                time.sleep(0.5)
            time.sleep(1)
            sts = states()
            new = connected(sts) - before or new
            print("=" * 60)
            if loop:
                print(f"Loop test: cable between ports {' and '.join(map(str, sorted(new)))}.\n")
                # Usually only one end reads the chip; show that one.
                reads = [s for s in sts if s["number"] in new and s["cable"]]
                show = {reads[0]["number"]} if reads else {min(new)}
                report(sts, raw=raw, only=show, loop=True)
            else:
                print(f"Device test on port {min(new)}.\n")
                report(sts, raw=raw, only={min(new)})
            print("Unplug the cable to test another one.\n")
            while connected(states()) & new:
                time.sleep(0.5)
            print("Cable removed. Ready for the next one.\n")
            now = connected(states())
        before = now


def main():
    args = sys.argv[1:]
    raw = "--raw" in args
    if "-h" in args or "--help" in args:
        print(__doc__)
        return
    try:
        if "--json" in args:
            import json
            print(json.dumps({"ports": analyze([port_state(p) for p in read_ports()])}))
        elif "--watch" in args or "-w" in args:
            watch(raw)
        else:
            report([port_state(p) for p in read_ports()], raw=raw)
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    main()
