from flask import Flask, request, jsonify
import subprocess
import os
import base64
import uuid
import shutil
import tempfile

app = Flask(__name__)

ARDUINO_CLI = "arduino-cli"
BUILD_DIR = tempfile.gettempdir()

# ── One-time setup: install arduino:avr platform on server start ──
def setup_arduino_cli():
    print("[SETUP] Updating arduino-cli index...")
    subprocess.run([ARDUINO_CLI, "core", "update-index"], capture_output=True, timeout=60)

    print("[SETUP] Installing arduino:avr platform...")
    result = subprocess.run(
        [ARDUINO_CLI, "core", "install", "arduino:avr"],
        capture_output=True, text=True, timeout=300
    )
    print(f"[SETUP] arduino:avr install: {result.returncode}")
    print(f"[SETUP] {result.stdout}")
    if result.returncode != 0:
        print(f"[SETUP] WARN: {result.stderr}")

    print("[SETUP] Installing esp32 platform...")
    subprocess.run(
        [ARDUINO_CLI, "core", "install", "esp32:esp32",
         "--additional-urls",
         "https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json"],
        capture_output=True, timeout=300
    )
    print("[SETUP] Done.")

# Run setup at startup
try:
    setup_arduino_cli()
except Exception as e:
    print(f"[SETUP] Setup error (non-fatal): {e}")

# ── Health / ping ─────────────────────────────────────────────────
@app.route('/ping',   methods=['GET'])
def ping():
    return jsonify({"status": "ok", "message": "Tecky Compile Server running"})

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok", "message": "Tecky Compile Server running"})

@app.route('/', methods=['GET'])
def root():
    return jsonify({"status": "ok", "message": "Tecky Compile Server running"})

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
    # FIX: sketch_name = folder name; .ino filename MUST match folder name
    sketch_name = f"{proj_name}_{job_id}"
    sketch_dir  = os.path.join(BUILD_DIR, sketch_name)
    output_dir  = os.path.join(sketch_dir, "build")

    try:
        os.makedirs(sketch_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)

        # .ino filename matches folder name exactly
        ino_path = os.path.join(sketch_dir, f"{sketch_name}.ino")
        with open(ino_path, 'w', encoding='utf-8') as f:
            f.write(code)

        print(f"[COMPILE] Board={board} Sketch={ino_path}")

        result = subprocess.run(
            [ARDUINO_CLI, "compile",
             "--fqbn", board,
             "--output-dir", output_dir,
             sketch_dir],
            capture_output=True, text=True, timeout=120
        )

        print(f"[COMPILE] rc={result.returncode}")
        print(f"[COMPILE] stdout={result.stdout}")
        print(f"[COMPILE] stderr={result.stderr}")

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
        return jsonify({"success": False,
                        "error": "Compile timeout (>2 min)"}), 200
    except FileNotFoundError:
        return jsonify({"success": False,
                        "error": "arduino-cli not found on server"}), 200
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
    print(f"Starting on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
