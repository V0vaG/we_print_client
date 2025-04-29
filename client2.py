#!/usr/bin/env python3

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

PORT = '5001'

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

print(f"\nUse this token for API calls:\n")
print(f"curl -X POST -H \"Content-Type: application/json\" -H \"Authorization: {API_TOKEN}\" -d '{{\"file_path\": \"Cuboid_PLA_17m.gcode\"}}' http://localhost:{PORT}/print")
print(f"curl -X POST -H \"Authorization: {API_TOKEN}\" http://localhost:{PORT}/stop")
print(f"curl -X GET -H \"Authorization: {API_TOKEN}\" http://localhost:{PORT}/status\n")

def require_token(f):
    def decorated(*args, **kwargs):
        token = request.headers.get("Authorization")
        if not token or token != API_TOKEN:
            print("❌ Unauthorized access attempt.")
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    decorated.__name__ = f.__name__
    return decorated

def detect_printer_type(ip):
    try:
        moonraker_response = requests.get(f"http://{ip}:7125/server/info", timeout=3)
        if moonraker_response.status_code == 200:
            return "moonraker"
    except:
        pass

    try:
        octoprint_response = requests.get(f"http://{ip}:5000/api/version", timeout=3)
        if octoprint_response.status_code == 200:
            return "octoprint"
    except:
        pass

    return None

def scan_for_printers(subnet="192.168.68.0/24"):
    print(f"Scanning network {subnet} for 3D printers...")

    network = ipaddress.IPv4Network(subnet, strict=False)
    found_printers = []

    def check_ip(ip):
        printer_type = detect_printer_type(ip)
        if printer_type:
            found_printers.append((str(ip), printer_type))

    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
        executor.map(check_ip, network.hosts())

    if not found_printers:
        print("\n❌ No 3D printers found on the network.")
        sys.exit(1)

    print("\n✅ Found Printers:")
    for idx, (ip, ptype) in enumerate(found_printers, start=1):
        print(f"{idx}. {ptype} @ {ip}")

    if len(found_printers) == 1:
        return found_printers[0]

    while True:
        try:
            choice = int(input("\nSelect printer number to connect: "))
            if 1 <= choice <= len(found_printers):
                return found_printers[choice - 1]
        except ValueError:
            pass
        print("❗ Invalid selection, try again.")

# Config
REMOTE_UPLOAD_PATH = "gcodes"

# Find Printer
printer_info = scan_for_printers()
PRINTER_IP = printer_info[0]
PRINTER_TYPE = printer_info[1]

if PRINTER_TYPE == "moonraker":
    API_BASE = f"http://{PRINTER_IP}:7125"
elif PRINTER_TYPE == "octoprint":
    API_BASE = f"http://{PRINTER_IP}:5000"
    OCTOPRINT_API_KEY = os.getenv("OCTOPRINT_API_KEY")
else:
    print("❌ Unknown printer type.")
    sys.exit(1)

headers = {}
if PRINTER_TYPE == "octoprint" and OCTOPRINT_API_KEY:
    headers = {"X-Api-Key": OCTOPRINT_API_KEY}


@app.route('/status', methods=['GET'])
@require_token
def check_status():
    print("🔎 Checking printer status and metrics...")
    try:
        metrics = {}

        if PRINTER_TYPE == "moonraker":
            response = requests.get(f"{API_BASE}/printer/objects/query?print_stats&display_status&heater_bed&extruder", headers=headers, timeout=10)
            if response.status_code != 200:
                return jsonify({"printer_status": "unknown"}), 500
            data = response.json()
            status = data.get("result", {}).get("status", {}).get("print_stats", {}).get("state", "Unknown").lower()
            try:
                metrics = {
                    "printer_status": status,
                    "filename": data.get("result", {}).get("status", {}).get("print_stats", {}).get("filename", "?"),
                    "bed_temperature": data.get("result", {}).get("status", {}).get("heater_bed", {}).get("temperature", "?"),
                    "nozzle_temperature": data.get("result", {}).get("status", {}).get("extruder", {}).get("temperature", "?"),
                    "progress": data.get("result", {}).get("status", {}).get("display_status", {}).get("progress", "?")
                }
                print(f"🖨️ Status: {metrics['printer_status']}\n📂 File: {metrics['filename']}\n🔥 Bed Temp: {metrics['bed_temperature']}C\n🔥 Nozzle Temp: {metrics['nozzle_temperature']}C\n📈 Progress: {metrics['progress']}%")
            except:
                print("⚠️ Could not parse extended metrics.")

        elif PRINTER_TYPE == "octoprint":
            response = requests.get(f"{API_BASE}/api/job", headers=headers, timeout=10)
            if response.status_code != 200:
                return jsonify({"printer_status": "unknown"}), 500
            data = response.json()
            status = data.get("state", {}).get("text", "Unknown").lower()
            try:
                metrics = {
                    "printer_status": status,
                    "filename": data.get("job", {}).get("file", {}).get("name", "?"),
                    "progress": data.get("progress", {}).get("completion", 0)
                }
                print(f"🖨️ Status: {metrics['printer_status']}\n📂 File: {metrics['filename']}\n📈 Progress: {metrics['progress']:.1f}%")
            except:
                print("⚠️ Could not parse extended metrics.")

        return jsonify(metrics), 200

    except requests.RequestException:
        return jsonify({"printer_status": "unknown"}), 500
def upload_gcode(file_path):
    print(f"⬆️ Uploading {file_path} to printer...")
    basename = os.path.basename(file_path)

    if PRINTER_TYPE == "moonraker":
        upload_url = f"{API_BASE}/server/files/upload"
        try:
            with open(file_path, "rb") as f:
                files = { 'file': (basename, f, 'application/octet-stream') }
                data = { 'path': REMOTE_UPLOAD_PATH }
                response = requests.post(upload_url, headers=headers, files=files, data=data, timeout=30)
            return basename if response.status_code in (200, 201) else None
        except:
            return None

    elif PRINTER_TYPE == "octoprint":
        upload_url = f"{API_BASE}/api/files/local"
        try:
            with open(file_path, "rb") as f:
                files = { 'file': (basename, f) }
                response = requests.post(upload_url, headers=headers, files=files, timeout=30)
            return basename if response.status_code in (200, 201) else None
        except:
            return None

def start_print(basename):
    print(f"🖨️ Starting print: {basename}")
    if PRINTER_TYPE == "moonraker":
        start_url = f"{API_BASE}/printer/print/start"
        payload = { "filename": f"{REMOTE_UPLOAD_PATH}/{basename}" }
    elif PRINTER_TYPE == "octoprint":
        start_url = f"{API_BASE}/api/job"
        payload = { "command": "start" }

    try:
        response = requests.post(start_url, headers=headers, json=payload, timeout=10)
        return response.status_code == 200
    except:
        return False

def cancel_print():
    print("⛔ Cancelling current print...")
    if PRINTER_TYPE == "moonraker":
        cancel_url = f"{API_BASE}/printer/print/cancel"
        payload = None
    elif PRINTER_TYPE == "octoprint":
        cancel_url = f"{API_BASE}/api/job"
        payload = { "command": "cancel" }

    try:
        response = requests.post(cancel_url, headers=headers, json=payload, timeout=10)
        return response.status_code == 200
    except:
        return False

@app.route('/print', methods=['POST'])
@require_token
def api_print():
    data = request.get_json()
    if not data or 'file_path' not in data:
        return jsonify({"error": "Missing file_path"}), 400

    file_path = data['file_path']

    if not os.path.exists(file_path):
        print("❌ File does not exist.")
        return jsonify({"error": "File does not exist"}), 404

    if check_status() == "printing":
        print("⚠️ Printer is already printing!")
        return jsonify({"error": "Printer is already printing"}), 409

    basename = upload_gcode(file_path)
    if not basename:
        print("❌ Upload failed.")
        return jsonify({"error": "Failed to upload file"}), 500

    if start_print(basename):
        print("✅ Print started successfully!")
        return jsonify({"success": True, "filename": basename}), 200
    else:
        print("❌ Failed to start print.")
        return jsonify({"error": "Failed to start print"}), 500

@app.route('/stop', methods=['POST'])
@require_token
def api_stop():
    if cancel_print():
        print("✅ Print cancelled.")
        return jsonify({"success": True}), 200
    else:
        print("❌ Failed to cancel print.")
        return jsonify({"error": "Failed to cancel print"}), 500

@app.route('/status', methods=['GET'])
@require_token
def api_status():
    status = check_status()
    if status in ("idle", "ready", "complete", "cancelled"):
        return jsonify({"printer_status": "ready"}), 200
    return jsonify({"printer_status": status}), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
