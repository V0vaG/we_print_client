#!/usr/bin/env python3

# curl -X POST -H "Authorization: <token>" http://localhost:5000/stop
# curl -H "Authorization: <token>" http://localhost:5000/status
# curl -X POST -H "Content-Type: application/json" -H "Authorization: <token>" -d '{"file_path": "your_gcode_file.gcode"}' http://localhost:5000/print

import os
import sys
import requests
import json
import socket
import ipaddress
import concurrent.futures
import secrets
from flask import Flask, request, jsonify
from dotenv import load_dotenv


app = Flask(__name__)

# Load environment variables if .env exists
load_dotenv()
ENV_TOKEN = os.getenv("API_TOKEN")
if ENV_TOKEN:
    API_TOKEN = ENV_TOKEN
else:
    API_TOKEN = secrets.token_hex(16)
    print(f"🔒 Generated API Token: {API_TOKEN}")

if ENV_TOKEN:
    print(f"🔒 Loaded API Token from .env")

def require_token(f):
    def decorated(*args, **kwargs):
        token = request.headers.get("Authorization")
        if not token or token != API_TOKEN:
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    decorated.__name__ = f.__name__
    return decorated

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

# Find Printer
PRINTER_IP = scan_for_printers()
MOONRAKER_API = f"http://{PRINTER_IP}:7125"
headers = {}

def check_status():
    try:
        response = requests.get(f"{MOONRAKER_API}/printer/objects/query?print_stats", headers=headers, timeout=10)
        if response.status_code != 200:
            print(f"❌ Failed to get status: {response.text}")
            return "unknown"

        data = response.json()
        status = data.get("result", {}).get("status", {}).get("print_stats", {}).get("state", "Unknown")
        print(f"🖨️ Printer Status: {status}")
        return status.lower()

    except requests.RequestException as e:
        print(f"❌ Error connecting to printer: {e}")
        return "unknown"

def upload_gcode(file_path):
    basename = os.path.basename(file_path)
    print(f"Uploading {basename} to printer via HTTP...")

    upload_url = f"{MOONRAKER_API}/server/files/upload"

    try:
        with open(file_path, "rb") as f:
            files = {
                'file': (basename, f, 'application/octet-stream')
            }
            data = {
                'path': REMOTE_UPLOAD_PATH
            }

            response = requests.post(upload_url, headers=headers, files=files, data=data, timeout=30)

        if response.status_code in (200, 201):
            print("✅ Upload successful!")
            return basename
        else:
            print(f"❌ Upload failed: {response.status_code} {response.text}")
            return None

    except Exception as e:
        print(f"❌ Error uploading file: {e}")
        return None

def start_print(basename):
    print(f"Starting print of {basename}...")

    start_url = f"{MOONRAKER_API}/printer/print/start"
    payload = {
        "filename": f"{REMOTE_UPLOAD_PATH}/{basename}"
    }

    try:
        response = requests.post(start_url, headers=headers, json=payload, timeout=10)
        if response.status_code == 200:
            print("✅ Print started successfully!")
            return True
        else:
            print(f"❌ Failed to start print: {response.status_code} {response.text}")
            return False

    except requests.RequestException as e:
        print(f"❌ Error starting print: {e}")
        return False

def cancel_print():
    status = check_status()
    if status != "printing":
        print("⚠️ Printer is not currently printing. No action taken.")
        return "no_print"

    print("Cancelling current print...")
    cancel_url = f"{MOONRAKER_API}/printer/print/cancel"
    try:
        response = requests.post(cancel_url, headers=headers, timeout=10)
        if response.status_code == 200:
            print("✅ Print cancelled successfully!")
            return "success"
        else:
            print(f"❌ Failed to cancel print: {response.status_code} {response.text}")
            return "fail"
    except requests.RequestException as e:
        print(f"❌ Error cancelling print: {e}")
        return "fail"

@app.route('/print', methods=['POST'])
@require_token
def api_print():
    data = request.get_json()
    if not data or 'file_path' not in data:
        return jsonify({"error": "Missing file_path"}), 400

    file_path = data['file_path']

    if not os.path.exists(file_path):
        return jsonify({"error": "File does not exist"}), 404

    status = check_status()
    if status == "printing":
        return jsonify({"error": "Printer is already printing"}), 409

    basename = upload_gcode(file_path)
    if not basename:
        return jsonify({"error": "Failed to upload file"}), 500

    if start_print(basename):
        return jsonify({"success": True, "filename": basename}), 200
    else:
        return jsonify({"error": "Failed to start print"}), 500

@app.route('/status', methods=['GET'])
@require_token
def api_status():
    status = check_status()
    if status in ("idle", "ready", "complete", "cancelled"):
        return jsonify({"printer_status": "ready"}), 200
    return jsonify({"printer_status": status}), 200

@app.route('/stop', methods=['POST'])
@require_token
def api_stop():
    result = cancel_print()
    if result == "success":
        return jsonify({"success": True}), 200
    elif result == "no_print":
        return jsonify({"error": "Printer is not currently printing"}), 409
    else:
        return jsonify({"error": "Failed to cancel print"}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)