from flask import Flask, request, jsonify
import subprocess
import os
import base64
import uuid
import shutil
import tempfile

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ARDUINO_CLI = os.path.join(BASE_DIR, "bin", "arduino-cli")
DATA_DIR = os.path.join(BASE_DIR, "arduino_data")

if not os.path.exists(ARDUINO_CLI):
    ARDUINO_CLI = "arduino-cli"

BUILD_DIR = tempfile.gettempdir()

def run_cli(args, timeout=300):
    env = os.environ.copy()
    env["ARDUINO_DATA_DIR"]      = DATA_DIR
    env["ARDUINO_DOWNLOADS_DIR"] = os.path.join(DATA_DIR, "staging")
    env["ARDUINO_USER_DIR"]      = os.path.join(DATA_DIR, "user")
    return subprocess.run(
        [ARDUINO_CLI] + args,
        capture_output=True, text=True,
        timeout=timeout, env=env
    )

def ensure_platform():
    """Install arduino:avr if not already installed — runs at startup"""
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(os.path.join(DATA_DIR, "staging"), exist_ok=True)
    os.makedirs(os.path.join(DATA_DIR, "user"), exist_ok=True)

    # Check if already installed
    r = run_cli(["core", "list"])
    if "arduino:avr" in r.stdout:
        print("[SETUP] arduino:avr already installed ✅")
        return True

    print("[SETUP] arduino:avr not found — installing now...")
    r = run_cli(["core", "update-index"], timeout=60)
    print(f"[SETUP] update-index: {r.returncode}")

    r = run_cli(["core", "install", "arduino:avr"], timeout=300)
    print(f"[SETUP] install arduino:avr rc={r.returncode}")
    print(f"[SETUP] stdout={r.stdout[-300:]}")
    print(f"[SETUP] stderr={r.stderr[-300:]}")

    if r.returncode == 0:
        print("[SETUP] arduino:avr installed successfully ✅")
        return True
    else:
        print("[SETUP] arduino:avr install FAILED ❌")
        return False

# Run at startup
print(f"[SERVER] arduino-cli: {ARDUINO_CLI}")
print(f"[SERVER] exists: {os.path.exists(ARDUINO_CLI)}")
print(f"[SERVER] data dir: {DATA_DIR}")
platform_ready = ensure_platform()
print(f"[SERVER] platform ready: {platform_ready}")

# ── Routes ────────────────────────────────────────────────────────
@app.route('/ping', methods=['GET'])
def ping():
    return jsonify({"status": "ok", "message": "Tecky Compile Server running"})

@app.route('/', methods=['GET'])
def root():
    return jsonify({"status": "ok", "message": "Tecky Compile Server running"})

@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        "status": "ok",
        "arduino_cli_exists": os.path.exists(ARDUINO_CLI),
        "platform_ready": platform_ready,
        "data_dir": DATA_DIR
    })

@app.route('/debug', methods=['GET'])
def debug():
    r = run_cli(["core", "list"])
    cores = r.stdout + r.stderr

    try:
        data_files = os.listdir(DATA_DIR)
    except:
        data_files = "not found"

    pkg_dir = os.path.join(DATA_DIR, "packages")
    try:
        pkg_files = os.listdir(pkg_dir)
    except:
        pkg_files = "not found"

    return jsonify({
        "arduino_cli_exists": os.path.exists(ARDUINO_CLI),
        "platform_ready": platform_ready,
        "cores": cores,
        "data_dir_contents": data_files,
        "packages": pkg_files
    })

# ── Compile ───────────────────────────────────────────────────────
@app.route('/compile', methods=['POST'])
def compile_code():
    data = request.get_json()

    if not data:
        return jsonify({"success": False, "error": "No JSON body"}), 400
    if 'code' not in data:
        return jsonify({"success": False, "error": "Missing 'code' field"}), 400
    if 'board' not in data:
        return jsonify({"success": False, "error": "Missing 'board' field"}), 400

    code      = data['code']
    board     = data['board']
    proj_name = data.get('projectName', 'RobotSketch')

    proj_name = ''.join(c for c in proj_name if c.isalnum() or c == '_')
    if not proj_name:
        proj_name = 'RobotSketch'

    job_id      = str(uuid.uuid4())[:8]
    sketch_name = f"{proj_name}_{job_id}"
    sketch_dir  = os.path.join(BUILD_DIR, sketch_name)
    output_dir  = os.path.join(sketch_dir, "build")

    try:
        os.makedirs(sketch_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)

        ino_path = os.path.join(sketch_dir, f"{sketch_name}.ino")
        with open(ino_path, 'w', encoding='utf-8') as f:
            f.write(code)

        print(f"[COMPILE] board={board} sketch={ino_path}")

        env = os.environ.copy()
        env["ARDUINO_DATA_DIR"]      = DATA_DIR
        env["ARDUINO_DOWNLOADS_DIR"] = os.path.join(DATA_DIR, "staging")
        env["ARDUINO_USER_DIR"]      = os.path.join(DATA_DIR, "user")

        result = subprocess.run(
            [ARDUINO_CLI, "compile", "--fqbn", board,
             "--output-dir", output_dir, sketch_dir],
            capture_output=True, text=True, timeout=120, env=env
        )

        print(f"[COMPILE] rc={result.returncode}")
        print(f"[COMPILE] stderr={result.stderr[:300]}")

        if result.returncode != 0:
            error_msg = result.stderr or result.stdout or "Unknown compile error"
            return jsonify({"success": False, "error": clean_error(error_msg)}), 200

        hex_file = find_output_file(output_dir)
        if not hex_file:
            return jsonify({"success": False,
                            "error": "Compile succeeded but hex file not found"}), 200

        with open(hex_file, 'rb') as f:
            file_bytes = f.read()

        file_b64  = base64.b64encode(file_bytes).decode('utf-8')
        file_type = "hex" if hex_file.endswith(".hex") else "bin"

        return jsonify({
            "success":  True,
            "board":    board,
            "fileType": file_type,
            "fileSize": len(file_bytes),
            "fileName": f"{sketch_name}.{file_type}",
            "fileData": file_b64,
            "message":  f"Compiled successfully ({len(file_bytes)} bytes)"
        })

    except subprocess.TimeoutExpired:
        return jsonify({"success": False, "error": "Compile timeout (>2 min)"}), 200
    except FileNotFoundError:
        return jsonify({"success": False,
                        "error": f"arduino-cli not found at {ARDUINO_CLI}"}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        try:
            shutil.rmtree(sketch_dir, ignore_errors=True)
        except:
            pass


def find_output_file(output_dir):
    for ext in [".hex", ".bin", ".elf"]:
        for fname in os.listdir(output_dir):
            if fname.endswith(ext):
                return os.path.join(output_dir, fname)
    return None


def clean_error(raw):
    lines = raw.strip().split('\n')
    useful = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if 'arduino-cli' in line.lower() and 'error' not in line.lower():
            continue
        useful.append(line)
    return '\n'.join(useful[:20])


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"[SERVER] Starting on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
