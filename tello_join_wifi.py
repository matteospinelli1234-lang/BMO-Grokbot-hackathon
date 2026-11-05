#!/usr/bin/env python3
"""Push a Tello Talent onto a shared Wi-Fi (station / router mode).

Run this WHILE your PC is connected to the drone's own AP
(TELLO-XXXX / RMTT-XXXX), typically at 192.168.10.1.

Default target network: ssid=hackathon  password=hackathon

  # 1) Join the drone AP Wi-Fi on this PC, then:
  python tello_join_wifi.py

  # 2) After the drone reboots, flip the Talent open-source-controller
  #    switch to ROUTER / SERVER (station) mode and power-cycle if needed.

  # 3) Join this PC to "hackathon", then find the drone's new IP:
  python tello_join_wifi.py --discover

SDK command used (Tello EDU / Talent / RoboMaster TT):
  ap <ssid> <password>
"""

from __future__ import annotations

import argparse
import ipaddress
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterable

TELLO_AP_IP = "192.168.10.1"
TELLO_CMD_PORT = 8889
DEFAULT_SSID = "hackathon"
DEFAULT_PASSWORD = "hackathon"


def _udp_exchange(
    host: str,
    payload: str,
    *,
    timeout: float = 8.0,
    local_port: int = 0,
) -> tuple[str, str]:
    """Send one UDP SDK command; return (response_text, peer_ip)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("", local_port))
        sock.settimeout(timeout)
        sock.sendto(payload.encode("utf-8"), (host, TELLO_CMD_PORT))
        data, addr = sock.recvfrom(1024)
        text = data.decode("utf-8", errors="replace").strip()
        return text, addr[0]
    finally:
        sock.close()


def reachable(host: str, timeout_s: float = 1.5) -> bool:
    """Best-effort ICMP ping (Linux)."""
    try:
        r = subprocess.run(
            ["ping", "-c", "1", "-W", str(max(1, int(timeout_s))), host],
            capture_output=True,
            text=True,
            timeout=timeout_s + 1.5,
        )
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def current_wifi_hint() -> str | None:
    """Try to print which SSID this PC is on (nmcli / iwgetid)."""
    for cmd in (
        ["nmcli", "-t", "-f", "active,ssid", "dev", "wifi"],
        ["iwgetid", "-r"],
    ):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if r.returncode != 0 or not r.stdout.strip():
            continue
        if cmd[0] == "nmcli":
            for line in r.stdout.splitlines():
                if line.startswith("yes:"):
                    return line.split(":", 1)[1] or None
        else:
            return r.stdout.strip()
    return None


def join_router(ssid: str, password: str, drone_ip: str = TELLO_AP_IP) -> None:
    wifi = current_wifi_hint()
    print("=" * 60)
    print("Tello Talent → station mode (join shared Wi-Fi)")
    print("=" * 60)
    if wifi:
        print(f"PC Wi-Fi SSID right now: {wifi}")
        if not wifi.upper().startswith(("TELLO", "RMTT")):
            print(
                "WARNING: that does not look like the drone AP "
                "(expected TELLO-* or RMTT-*)."
            )
            print("Connect this PC to the drone hotspot first, then re-run.")
    else:
        print("Could not detect PC Wi-Fi SSID (continuing anyway).")

    print(f"Probing drone at {drone_ip} ...")
    if not reachable(drone_ip):
        print(
            f"Cannot ping {drone_ip}. Are you on the drone AP?\n"
            "  1) Power on the Talent in DIRECT / AP mode "
            "(switch DOWN on the open-source controller).\n"
            "  2) Connect this PC to TELLO-XXXX / RMTT-XXXX.\n"
            "  3) Re-run this script."
        )
        sys.exit(1)

    print("Entering SDK mode (`command`)...")
    try:
        resp, _ = _udp_exchange(drone_ip, "command", timeout=5.0)
    except socket.timeout:
        print(
            "No reply to `command`. Is another app already talking to the "
            "drone (Tello EDU app / Mind+ / another script)?"
        )
        sys.exit(1)
    print(f"  <- {resp!r}")
    if "ok" not in resp.lower():
        print("Unexpected reply; aborting.")
        sys.exit(1)

    try:
        bat, _ = _udp_exchange(drone_ip, "battery?", timeout=5.0)
        print(f"Battery: {bat}%")
    except socket.timeout:
        print("Battery query timed out (non-fatal).")

    cmd = f"ap {ssid} {password}"
    print(f"Sending: {cmd}")
    print("(drone should reply then reboot into station config)")
    try:
        resp, _ = _udp_exchange(drone_ip, cmd, timeout=12.0)
    except socket.timeout:
        # Some firmwares reboot before the UDP reply arrives.
        print(
            "No UDP reply (common if reboot started immediately).\n"
            "Assume the `ap` command was accepted if the drone reboots now."
        )
        resp = "(no reply)"
    else:
        print(f"  <- {resp!r}")

    print()
    print("Next steps")
    print("-" * 60)
    print(f"1. Wait ~10–20s for the drone to store SSID `{ssid}` and reboot.")
    print("2. Flip the Talent open-source-controller switch to ROUTER /")
    print("   SERVER (station) mode — usually the UP position.")
    print("3. Power-cycle the drone if it does not auto-join.")
    print(f"4. Connect THIS PC to Wi-Fi `{ssid}` (password `{password}`).")
    print("5. Find the drone's DHCP address:")
    print("      python tello_join_wifi.py --discover")
    print("6. Point the bridge at that IP:")
    print("      python tello_agent_bridge.py --real --tello-host x.x.x.x")
    print()
    print("To go back to AP mode later: flip the switch back to DIRECT/AP")
    print("and power-cycle (credentials stay stored for next station use).")


def wifi_ipv4_cidr() -> tuple[ipaddress.IPv4Address, ipaddress.IPv4Network] | None:
    """Return (local_ip, /24) for the active Wi-Fi NIC only (skip docker bridges)."""
    try:
        out = subprocess.check_output(
            ["ip", "-4", "-o", "addr", "show", "scope", "global"],
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in out.splitlines():
        # e.g. "3: wlo1    inet 192.168.5.24/24 brd ..."
        parts = line.split()
        if len(parts) < 4 or parts[2] != "inet":
            continue
        iface = parts[1]
        if not (iface.startswith("wl") or iface.startswith("wlan")):
            continue
        cidr = parts[3]
        ip = ipaddress.IPv4Address(cidr.split("/")[0])
        net = ipaddress.IPv4Network(f"{ip}/24", strict=False)
        return ip, net
    return None


def scan_rmtt_aps() -> list[str]:
    """SSIDs currently advertising as Talent APs (still in DIRECT mode)."""
    try:
        out = subprocess.check_output(
            ["nmcli", "-t", "-f", "SSID", "device", "wifi", "list"],
            text=True,
            timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    found: list[str] = []
    for line in out.splitlines():
        ssid = line.strip()
        if ssid.upper().startswith(("RMTT-", "TELLO-")) and ssid not in found:
            found.append(ssid)
    return found


def candidate_hosts(extra: Iterable[str] | None = None) -> list[str]:
    hosts: list[str] = []
    wifi = wifi_ipv4_cidr()
    if wifi is not None:
        local_ip, net = wifi
        for host in net.hosts():
            if host == local_ip:
                continue
            hosts.append(str(host))
    if extra:
        for h in extra:
            if h not in hosts:
                hosts.append(h)
    return hosts


def probe_tello(host: str, timeout: float = 0.35) -> str | None:
    try:
        resp, peer = _udp_exchange(host, "command", timeout=timeout)
    except (socket.timeout, OSError):
        return None
    if "ok" in resp.lower() or resp.lower() in {"ok", "error"}:
        return peer
    # Some units echo battery or other text after command mode is already on
    if resp:
        return peer
    return None


def discover(subnet_hint: str | None = None, workers: int = 64) -> None:
    wifi = current_wifi_hint()
    print("=" * 60)
    print("Discover Tello on local LAN (station / router mode)")
    print("=" * 60)
    if wifi:
        print(f"PC Wi-Fi SSID: {wifi}")
    wifi_cidr = wifi_ipv4_cidr()
    if wifi_cidr is not None:
        print(f"Wi-Fi iface address: {wifi_cidr[0]}  (scan {wifi_cidr[1]})")

    aps = scan_rmtt_aps()
    if aps:
        print(f"Talent APs still visible (DIRECT mode): {', '.join(aps)}")

    extras = [subnet_hint] if subnet_hint else None
    if subnet_hint and "/" in subnet_hint:
        net = ipaddress.ip_network(subnet_hint, strict=False)
        hosts = [str(h) for h in net.hosts()]
    else:
        hosts = candidate_hosts(extras)
        # also always try classic AP IP in case still in AP mode
        if TELLO_AP_IP not in hosts:
            hosts.insert(0, TELLO_AP_IP)

    print(f"Scanning {len(hosts)} addresses on UDP/{TELLO_CMD_PORT} ...")
    found: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(probe_tello, h): h for h in hosts}
        for fut in as_completed(futs):
            peer = fut.result()
            if peer and peer not in found:
                found.append(peer)
                print(f"  FOUND Tello SDK at {peer}")

    if not found:
        print("No Tello answered.")
        print("Checks:")
        print("  • Extension-kit switch UP = ROUTER / SERVER mode, then power-cycle.")
        print("  • On success the motors often spin slowly — keep clear.")
        print("  • Matrix may show 'Wifi UNACTIVE!' if the kit was never activated")
        print("    in the Tello / Tello EDU app with the expansion kit attached.")
        print("  • Venue Wi-Fi is often WPA2/WPA3 mixed — Talent commonly fails.")
        print("    Workaround: phone hotspot WPA2-only, re-run join with that SSID.")
        print("  • Guest / client-isolation must be OFF.")
        if aps:
            print(f"  • Still seeing DIRECT APs {aps} — those drones are not in station mode.")
        sys.exit(2)

    print()
    print("Use one of these hosts with djitellopy / the bridge:")
    for ip in found:
        print(f"  Tello(host={ip!r})")
        try:
            bat, _ = _udp_exchange(ip, "battery?", timeout=3.0)
            print(f"    battery? -> {bat}")
        except socket.timeout:
            print("    battery? timed out")


def main() -> None:
    p = argparse.ArgumentParser(
        description="Join Tello Talent to a Wi-Fi network (ap ssid pass)"
    )
    p.add_argument(
        "--ssid",
        default=DEFAULT_SSID,
        help=f"Router SSID (default: {DEFAULT_SSID})",
    )
    p.add_argument(
        "--password",
        default=DEFAULT_PASSWORD,
        help=f"Router password (default: {DEFAULT_PASSWORD})",
    )
    p.add_argument(
        "--drone-ip",
        default=TELLO_AP_IP,
        help=f"Drone IP while in AP mode (default: {TELLO_AP_IP})",
    )
    p.add_argument(
        "--discover",
        action="store_true",
        help="After station join: scan LAN for a Tello answering on UDP 8889",
    )
    p.add_argument(
        "--subnet",
        default=None,
        help="Optional CIDR for --discover (e.g. 192.168.1.0/24)",
    )
    args = p.parse_args()

    if args.discover:
        discover(subnet_hint=args.subnet)
        return

    join_router(args.ssid, args.password, drone_ip=args.drone_ip)


if __name__ == "__main__":
    main()
