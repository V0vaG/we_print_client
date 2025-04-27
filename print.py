#!/usr/bin/env python3

import os
import sys
import requests
import json
import socket
import ipaddress
import concurrent.futures

def scan_for_printers(subnet="192.168.68.0/24"):
    print(f"Scanning network {subnet} for 3D printers...")

    network = ipaddress.IPv4Network(subnet, strict=False)
    found_printers = []

    def check_ip(ip):
        try:
            sock = socket.create_connection((str(ip), 7125), timeout=1)
            sock.close()
            try:
                r = requests.get(f"http://{ip}:7125/printer/info", timeout=2)
                if r.status_code == 200:
                    data = r.json()
                    name = data.get("result", {}).get("machine_name", "Unknown Printer")
                    found_printers.append((str(ip), name))
            except:
                found_printers.append((str(ip), "Unknown Printer"))
        except:
            pass

    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
        executor.map(check_ip, network.hosts())

    if not found_printers:
        print("\n❌ No 3D printers found on the network.")
        sys.exit(1)

    print("\n✅ Found Printers:")
    for idx, (ip, name) in enumerate(found_printers, start=1):
        print(f"{idx}. {name} @ {ip}")

    if len(found_printers) == 1:
        return found_printers[0][0]

    while True:
        try:
            choice = int(input("\nSelect printer number to connect: "))
            if 1 <= choice <= len(found_printers):
                return found_printers[choice - 1][0]
        except ValueError:
            pass
        print("❗ Invalid selection, try again.")

# Config
REMOTE_UPLOAD_PATH = "gcodes"

# Check if user provided a file
GCODE_FILE = sys.argv[1] if len(sys.argv) > 1 else None
BASENAME = os.path.basename(GCODE_FILE) if GCODE_FILE else None

# Find Printer
PRINTER_IP = scan_for_printers()
MOONRAKER_API = f"http://{PRINTER_IP}:7125"
headers = {}

printer_status = None

def check_status():
    global printer_status
    print(f"Connecting to 3D printer API at {PRINTER_IP} to check status...")
    try:
        response = requests.get(f"{MOONRAKER_API}/printer/objects/query?print_stats", headers=headers, timeout=10)
        if response.status_code != 200:
            print(f"❌ Failed to get status: {response.text}")
            sys.exit(1)

        data = response.json()
        printer_status = data.get("result", {}).get("status", {}).get("print_stats", {}).get("state", "Unknown")
        print(f"🖨️ Printer Status: {printer_status}")

    except requests.RequestException as e:
        print(f"❌ Error connecting to printer: {e}")
        sys.exit(1)

def get_and_censor_metrics():
    print("Fetching printer metrics...")
    try:
        response = requests.get(f"{MOONRAKER_API}/printer/objects/query?print_stats&display_status", headers=headers, timeout=10)
        if response.status_code != 200:
            print(f"❌ Failed to get printer metrics: {response.text}")
            return

        data = response.json()

        sensitive_keys = [
            "extruder", "heater_bed", "temperature_sensor", "fans", "heaters",
            "motion_report", "webhooks", "toolhead", "gcode_move", "bed_screws"
        ]

        censored = {}
        for key, value in data.get("result", {}).get("status", {}).items():
            censored[key] = "CENSORED" if key in sensitive_keys else value

        print(json.dumps(censored, indent=2))

    except requests.RequestException as e:
        print(f"❌ Error fetching metrics: {e}")

def upload_gcode():
    print(f"Uploading {GCODE_FILE} to printer via HTTP...")

    upload_url = f"{MOONRAKER_API}/server/files/upload"

    try:
        with open(GCODE_FILE, "rb") as f:
            files = {
                'file': (BASENAME, f, 'application/octet-stream')
            }
            data = {
                'path': REMOTE_UPLOAD_PATH
            }

            response = requests.post(upload_url, headers=headers, files=files, data=data, timeout=30)

        if response.status_code in (200, 201):
            print("✅ Upload successful!")
        else:
            print(f"❌ Upload failed: {response.status_code} {response.text}")
            sys.exit(1)

    except Exception as e:
        print(f"❌ Error uploading file: {e}")
        sys.exit(1)

def start_print():
    print(f"Starting print of {BASENAME}...")

    start_url = f"{MOONRAKER_API}/printer/print/start"
    payload = {
        "filename": f"{REMOTE_UPLOAD_PATH}/{BASENAME}"
    }

    try:
        response = requests.post(start_url, headers=headers, json=payload, timeout=10)
        if response.status_code == 200:
            print("✅ Print started successfully!")
        else:
            print(f"❌ Failed to start print: {response.status_code} {response.text}")
            sys.exit(1)

    except requests.RequestException as e:
        print(f"❌ Error starting print: {e}")
        sys.exit(1)

def main():
    check_status()

    if printer_status.lower() == "printing":
        print("⚠️ Printer is already printing. Exiting.")
        sys.exit(0)

    if not GCODE_FILE:
        get_and_censor_metrics()
        sys.exit(0)

    upload_gcode()
    start_print()

if __name__ == "__main__":
    main()
