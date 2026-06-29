from flask import Flask, request, jsonify
import subprocess
import os
import base64
import uuid
import shutil
import tempfile

app = Flask(__name__)

# Use project-relative folders — persist from build to runtime on Render
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ARDUINO_CLI = os.path.join(BASE_DIR, "bin", "arduino-cli")
ARDUINO_CONFIG = os.path.join(BASE_DIR, "arduino_data", "arduino-cli.yaml")

if not os.path.exists(ARDUINO_CLI):
    ARDUINO_CLI = "arduino-cli"  # local dev fallback

BUILD_DIR = tempfile.gettempdir()

print(f"[SERVER] arduino-cli path: {ARDUINO_CLI}")
print(f"[SERVER] arduino-cli exists: {os.path.exists(ARDUINO_CLI)}")
print(f"[SERVER] config path: {ARDUINO_CONFIG}")
print(f"[SERVER] config exists: {os.path.exists(ARDUINO_CONFIG)}")


def get_cli_cmd(extra_args):
    """Build arduino-cli command with config file if available"""
    cmd = [ARDUINO_CLI]
    if os.path.exists(ARDUINO_CONFIG):
        cmd += ["--config-file", ARDUINO_CONFIG]
    cmd += extra_args
    return cmd


# ── Health ────────────────────────────────────────────────────────
@app.route('/ping', methods=['GET'])
def ping():
    return jsonify({"status": "ok", "message": "Tecky Compile Server running"})

@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        "status": "ok",
        "message": "Tecky Compile Server running",
        "arduino_cli": ARDUINO_CLI,
        "arduino_cli_exists": os.path.exists(ARDUINO_CLI),
        "config_exists": os.path.exists(ARDUINO_CONFIG)
    })

@app.route('/', methods=['GET'])
def root():
    return jsonify({"status": "ok", "message": "Tecky Compile Server running"})

# ── Debug ─────────────────────────────────────────────────────────
@app.route('/debug', methods=['GET'])
def debug():
    # Check core list
    try:
        result = subprocess.run(
            get_cli_cmd(["core", "list"]),
            capture_output=True, text=True, timeout=30
        )
        cores = result.stdout + result.stderr
    except Exception as e:
        cores = str(e)

    # Check bin folder
    bin_dir = os.path.join(BASE_DIR, "bin")
    try:
        bin_files = os.listdir(bin_dir)
    except:
        bin_files = "bin folder not found"

    # Check arduino_data folder
    data_dir = os.path.join(BASE_DIR, "arduino_data")
    try:
        data_files = os.listdir(data_dir)
    except:
        data_files = "arduino_data folder not found"

    # Check arduino_data/packages folder
    pkg_dir = os.path.join(BASE_DIR, "arduino_data", "packages")
    try:
        pkg_files = os.listdir(pkg_dir)
    except:
        pkg_files = "packages folder not found"

    return jsonify({
        "arduino_cli_path": ARDUINO_CLI,
        "arduino_cli_exists": os.path.exists(ARDUINO_CLI),
        "config_path": ARDUINO_CONFIG,
        "config_exists": os.path.exists(ARDUINO_CONFIG),
        "cores_installed": cores,
        "bin_folder": bin_files,
        "arduino_data_folder": data_files,
        "packages_folder": pkg_files
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

        # .ino filename MUST match folder name exactly
        ino_path = os.path.join(sketch_dir, f"{sketch_name}.ino")
        with open(ino_path, 'w', encoding='utf-8') as f:
            f.write(code)

        print(f"[COMPILE] CLI={ARDUINO_CLI}")
        print(f"[COMPILE] Board={board}")
        print(f"[COMPILE] Sketch={ino_path}")

        result = subprocess.run(
            get_cli_cmd(["compile", "--fqbn", board,
                         "--output-dir", output_dir, sketch_dir]),
            capture_output=True, text=True, timeout=120
        )

        print(f"[COMPILE] rc={result.returncode}")
        print(f"[COMPILE] stdout={result.stdout[:500]}")
        print(f"[COMPILE] stderr={result.stderr[:500]}")

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
