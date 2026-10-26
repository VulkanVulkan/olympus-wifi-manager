#!/usr/bin/env python3
"""Read-only Olympus camera discovery for Linux."""

import re
import subprocess
import urllib.error
import urllib.request


def wifi_gateway():
    """Return the IPv4 gateway of an active Wi-Fi connection."""

    try:
        devices = subprocess.run(
            ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device", "status"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )

        for line in devices.stdout.splitlines():
            parts = line.split(":", 2)

            if len(parts) != 3:
                continue

            device, kind, state = parts

            if kind != "wifi" or state != "connected":
                continue

            route = subprocess.run(
                ["ip", "-4", "route", "show", "default", "dev", device],
                capture_output=True,
                text=True,
                check=True,
                timeout=5,
            )

            match = re.search(
                r"\bdefault\s+via\s+(\d+\.\d+\.\d+\.\d+)\b",
                route.stdout,
            )

            if match:
                return match.group(1)

    except (OSError, subprocess.SubprocessError):
        return None

    return None


def is_olympus_camera(address):
    """Check for an Olympus-compatible directory endpoint."""

    try:
        url = f"http://{address}/DCIM"

        with urllib.request.urlopen(url, timeout=5) as response:
            if response.status != 200:
                return False

            data = response.read(256 * 1024)

        return (
            b"wlansd" in data
            and b"/DCIM" in data
        )

    except (OSError, urllib.error.URLError, ValueError):
        return False


def detect_camera():
    """Return the detected camera IP, or None."""

    gateway = wifi_gateway()

    if gateway and is_olympus_camera(gateway):
        return gateway

    return None


if __name__ == "__main__":
    print("Searching for Olympus camera...")

    address = detect_camera()

    if address:
        print(f"Camera detected: {address}")
    else:
        print("No compatible Olympus camera detected.")
