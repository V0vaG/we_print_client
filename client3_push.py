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
import subprocess
import shutil
import threading
import time
from dotenv import load_dotenv, set_key

VERSION = '1.0.0'

# List of required environment variable names
env_var_names = ["USER_TOKEN", "PRINTER_IP", "PRINTER_TYPE", "USER", "PRINTER_NAME", "APP_URL"]

# Config
PORT= '5002'
SLICER = "prusa-slicer"
DEFAULT_CONFIG_FILE = "my_config.ini"
SLICER_COMMANDS = ["prusa-slicer", "PrusaSlicer", "prusa-slicer-console"]
REMOTE_UPLOAD_PATH = "gcodes"
app = Flask(__name__)



def load_env_vars():
    dotenv_path = os.path.join(os.getcwd(), ".env")

    # Always reload .env from the file only
    if not os.path.exists(dotenv_path):
        print("📄 .env file not found. Creating a new one...")
        with open(dotenv_path, "w") as f:
            f.write("")  # create empty .env

    load_dotenv(dotenv_path, override=True)  # force load from file only

    env_vars = {}

    for var in env_var_names:
        value = os.getenv(var, None)  # load ONLY from .env (not inherited env)
        if not value:
            if var in ("PRINTER_IP", "PRINTER_TYPE"):
                find_printer()
                value = globals().get(var)
            else:
                try:
                    value = input(f"🔑 {var} not found. Enter value for {var}: ").strip()
                    if not value:
                        print(f"❌ {var} cannot be empty. Exiting.")
                        sys.exit(1)
                except KeyboardInterrupt:
                    print(f"\n❌ Interrupted during {var}. Exiting.")
                    sys.exit(1)

            # Save to .env file
            set_key(dotenv_path, var, value)
            print(f"💾 Saved {var} to .env")
        else:
            print(f"🔒 Loaded {var} from .env: {value}")

        env_vars[var] = value

    # Assign to global scope
    for var, value in env_vars.items():
        globals()[var] = value


def find_slicer():
    for slicer in SLICER_COMMANDS:
        if shutil.which(slicer):
            return slicer
    return None

def install_prusaslicer():
    print("⚙️ PrusaSlicer not found. Attempting to install...")
    try:
        if sys.platform.startswith('linux'):
            subprocess.check_call(["sudo", "apt", "update"])
            subprocess.check_call(["sudo", "apt", "install", "-y", "prusa-slicer"])
        elif sys.platform == "darwin":
            subprocess.check_call(["brew", "install", "--cask", "prusaslicer"])
        elif sys.platform == "win32":
            print("❗ Please manually install PrusaSlicer from:")
            print("   https://www.prusa3d.com/page/prusaslicer_424/")
            sys.exit(1)
        else:
            print("❌ Unsupported OS. Please install PrusaSlicer manually.")
            sys.exit(1)
    except Exception as e:
        print(f"❌ Auto-install failed: {e}")
        sys.exit(1)

def print_commands():
    print(f"\nUse this token for API calls:\n")
    print(f"curl -X POST -H \"Content-Type: application/json\" -H \"Authorization: {USER_TOKEN}\" -d '{{\"file_path\": \"Cuboid_PLA_17m.gcode\"}}' http://localhost:{PORT}/print")
    print(f"curl -X POST -H \"Content-Type: application/json\" -H \"Authorization: {USER_TOKEN}\" -d '{{\"file_path\": \"test.stl\"}}' http://localhost:{PORT}/print")
    print(f"curl -X POST -H \"Content-Type: application/json\" -H \"Authorization: {USER_TOKEN}\" -d '{{\"file_path\": \"test.stl\", \"config_path\": \"high_speed_config.ini\"}}' http://localhost:{PORT}/print")
    print(f"curl -X POST -H \"Authorization: {USER_TOKEN}\" http://localhost:{PORT}/stop")
    print(f"curl -X GET -H \"Authorization: {USER_TOKEN}\" http://localhost:{PORT}/status\n")

def require_token(f):
    def decorated(*args, **kwargs):
        token = request.headers.get("Authorization")
        if not token or token != USER_TOKEN:
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
        octoprint_response = requests.get(f"http://{ip}:{PORT}/api/version", timeout=3)
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




def find_printer():
    global PRINTER_IP, PRINTER_TYPE, API_BASE, OCTOPRINT_API_KEY, headers

    # Check if printer info already exists in .env
    cached_ip = os.getenv("PRINTER_IP")
    cached_type = os.getenv("PRINTER_TYPE")

    if cached_ip and cached_type:
        PRINTER_IP = cached_ip
        PRINTER_TYPE = cached_type
        print(f"📦 Loaded printer from .env: {PRINTER_TYPE} @ {PRINTER_IP}")
    else:
        # Run detection only if not cached
        printer_info = scan_for_printers()
        PRINTER_IP = printer_info[0]
        PRINTER_TYPE = printer_info[1]

        # Save to .env
        dotenv_path = os.path.join(os.getcwd(), ".env")
        set_key(dotenv_path, "PRINTER_IP", PRINTER_IP)
        set_key(dotenv_path, "PRINTER_TYPE", PRINTER_TYPE)
        print(f"💾 Saved printer info to .env: {PRINTER_TYPE} @ {PRINTER_IP}")

    # Setup printer API base and headers
    if PRINTER_TYPE == "moonraker":
        API_BASE = f"http://{PRINTER_IP}:7125"
        headers = {}
    elif PRINTER_TYPE == "octoprint":
        API_BASE = f"http://{PRINTER_IP}:5000"
        OCTOPRINT_API_KEY = os.getenv("OCTOPRINT_API_KEY")
        headers = {"X-Api-Key": OCTOPRINT_API_KEY} if OCTOPRINT_API_KEY else {}
    else:
        print("❌ Unknown printer type.")
        sys.exit(1)


def send_status_loop():
    while True:
        try:
            # Step 1: Get local printer status
            status_response = requests.get(f"http://localhost:{PORT}/status", headers={"Authorization": USER_TOKEN}, timeout=5)

            if status_response.status_code == 200:
                metrics = status_response.json()
                print(f"📡 Local Printer Metrics: {metrics}")

                # Add authentication and printer info
                metrics["user"] = USER
                metrics["user_token"] = USER_TOKEN
                metrics["printer_name"] = PRINTER_NAME

                # Step 2: Send to cloud server
                post_response = requests.post(
                    f"http://{APP_URL}/receive_status",
                    headers={
                        "Authorization": USER_TOKEN,
                        "Content-Type": "application/json"
                    },
                    json=metrics,
                    timeout=5
                )

                if post_response.status_code == 200:
                    result = post_response.json()
                    print(f"✅ Metrics successfully sent: {result}")

                    # 🧠 Process returned command if present
                    if "command" in result:
                        command = result["command"]
                        print(f"📨 Dispatching received command: {command}")
                        try:
                            local_response = requests.post(
                                f"http://localhost:{PORT}/remote_command",
                                headers={
                                    "Authorization": USER_TOKEN,
                                    "Content-Type": "application/json"
                                },
                                json=command,
                                timeout=10
                            )
                            print(f"📤 Local handler response: {local_response.status_code} {local_response.text}")
                        except Exception as e:
                            print(f"❌ Failed to forward command to local handler: {e}")
                else:
                    print(f"⚠️ Failed to send metrics: {post_response.status_code} {post_response.text}")

            else:
                print(f"⚠️ Failed to get local printer status: {status_response.status_code} {status_response.text}")

        except Exception as e:
            print(f"❌ Error during status sending: {e}")

        time.sleep(10)



def slice_stl_to_gcode(stl_path, config_file=DEFAULT_CONFIG_FILE):
    basename_no_ext = os.path.splitext(os.path.basename(stl_path))[0]
    gcode_output = f"{basename_no_ext}.gcode"

    cmd = [
        SLICER,
        "--slice",
        "--load", config_file,
        "--output", gcode_output,
        stl_path
    ]

    try:
        print(f"🛠️ Slicing {stl_path} into {gcode_output} using {config_file}...")
        subprocess.check_call(cmd)
        print(f"✅ Sliced successfully: {gcode_output}")
        return gcode_output
    except subprocess.CalledProcessError as e:
        print(f"❌ Slicing failed: {e}")
        return None

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
    config_path = data.get('config_path', DEFAULT_CONFIG_FILE)

    if not os.path.exists(file_path):
        print("❌ File does not exist.")
        return jsonify({"error": "File does not exist"}), 404

    if check_status() == "printing":
        print("⚠️ Printer is already printing!")
        return jsonify({"error": "Printer is already printing"}), 409

    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".stl":
        if not os.path.exists(config_path):
            print(f"❌ Configuration file '{config_path}' does not exist.")
            return jsonify({"error": "Config file does not exist"}), 404

        gcode_path = slice_stl_to_gcode(file_path, config_file=config_path)
        if not gcode_path or not os.path.exists(gcode_path):
            return jsonify({"error": "Failed to slice STL file"}), 500
        file_path = gcode_path

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

def print_help():
    print(f"    We_Print Client Version: {VERSION}")
    print(f"""
    Usage: python3 client3.py [OPTIONS]
    
    Options:
      -v, --version     Show the client version and exit
      -h, --help        Show this help message and exit
    
    Description:
    This client connects to your 3D printer API server.
    It can upload G-code files, slice STL files automatically, and manage prints.
    
    API Example Usage:
    
      ▶ Upload and start a print from an existing G-code file:
        curl -X POST -H "Content-Type: application/json" -H "Authorization: <USER_TOKEN>" \\
             -d '{{"file_path": "Cuboid_PLA_17m.gcode"}}' http://localhost:<PORT>/print
    
      ▶ Upload an STL file, slice it, then start the print:
        curl -X POST -H "Content-Type: application/json" -H "Authorization: <USER_TOKEN>" \\
             -d '{{"file_path": "test.stl"}}' http://localhost:<PORT>/print
    
      ▶ Upload an STL file with config file, slice it, then start the print:
        curl -X POST -H "Content-Type: application/json" -H "Authorization: <USER_TOKEN>" \\
             -d '{{"file_path": "test.stl", "config_path": "config.ini"}}' http://localhost:<PORT>/print
    
      ▶ Stop the current ongoing print:
        curl -X POST -H "Authorization: <USER_TOKEN>" http://localhost:<PORT>/stop
    
      ▶ Check printer status (idle, printing, complete, etc.):
        curl -X GET -H "Authorization: <USER_TOKEN>" http://localhost:<PORT>/status
    
    Notes:
    - Replace <USER_TOKEN> with your real token generated at server start.
    - Replace <PORT> with your server port (default 5000).
    """)



@app.route('/remote_command', methods=['POST'])
@require_token
def remote_command():
    data = request.get_json()
    if not data:
        return jsonify({"error": "Missing command payload"}), 400

    command = data.get("command")

    if command == "stop_print":
        if cancel_print():
            return jsonify({"success": True, "message": "Print stopped"}), 200
        else:
            return jsonify({"error": "Failed to stop print"}), 500

    elif command == "print":
        # Accept either STL or GCODE file
        stl_url = data.get("stl_path")
        gcode_url = data.get("gcode_path")
        ini_file = data.get("ini_file")
        config_path = ini_file if ini_file else DEFAULT_CONFIG_FILE

        gcode_path = None

        if stl_url:
            stl_filename = data.get("stl_file", "file.stl")
            print(f"\U0001F310 Downloading STL from {stl_url}...")
            try:
                stl_response = requests.get(stl_url, timeout=10)
                stl_response.raise_for_status()
                with open(stl_filename, "wb") as f:
                    f.write(stl_response.content)
                print(f"✅ STL downloaded: {stl_filename}")
            except Exception as e:
                return jsonify({"error": f"Failed to download STL: {str(e)}"}), 500

            if not os.path.exists(config_path):
                print(f"⚠️ Config file {config_path} not found. Using default.")
                config_path = DEFAULT_CONFIG_FILE

            gcode_path = slice_stl_to_gcode(stl_filename, config_file=config_path)
            if not gcode_path or not os.path.exists(gcode_path):
                return jsonify({"error": "Failed to slice STL"}), 500

        elif gcode_url:
            gcode_filename = data.get("gcode_file", "file.gcode")
            print(f"\U0001F310 Downloading GCODE from {gcode_url}...")
            try:
                gcode_response = requests.get(gcode_url, timeout=10)
                gcode_response.raise_for_status()
                with open(gcode_filename, "wb") as f:
                    f.write(gcode_response.content)
                print(f"✅ GCODE downloaded: {gcode_filename}")
                gcode_path = gcode_filename
            except Exception as e:
                return jsonify({"error": f"Failed to download GCODE: {str(e)}"}), 500

        else:
            return jsonify({"error": "Missing STL or GCODE path"}), 400

        # Upload and start print
        basename = upload_gcode(gcode_path)
        if not basename:
            return jsonify({"error": "Failed to upload G-code"}), 500

        if start_print(basename):
            return jsonify({"success": True, "filename": basename}), 200
        else:
            return jsonify({"error": "Failed to start print"}), 500

    return jsonify({"error": f"Unsupported command: {command}"}), 400






if __name__ == "__main__":
    if "-v" in sys.argv or "--version" in sys.argv:
        print(f"We_Print Client Version: {VERSION}")
        sys.exit(0)

    if "-h" in sys.argv or "--help" in sys.argv:
        print_help()
        sys.exit(0)
        
    load_env_vars()
    
    print_commands()
    
    slicer = find_slicer()
    if slicer:
        print(f"🛠️ Found slicer: {slicer}")
    else:
        install_prusaslicer()
        slicer = find_slicer()
        if not slicer:
            print("❌ Still no slicer found after attempted install.")
            NO_SLICER = True
        else:
            print(f"🛠️ Installed and found slicer: {slicer}")
            

    find_printer()
    
    # 🆕 Start background status loop
    threading.Thread(target=send_status_loop, daemon=True).start()
    
    app.run(host="0.0.0.0", port=int(PORT))

