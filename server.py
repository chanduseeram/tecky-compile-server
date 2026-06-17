from flask import Flask, request, jsonify, send_file
import subprocess
import os
import base64
import uuid
import shutil
import tempfile

app = Flask(__name__)

# ── Config ──────────────────────────────────────────────────────
# Path to arduino-cli executable
# Windows: change to full path like "C:/arduino-cli/arduino-cli.exe"
# Mac/Linux: just "arduino-cli" if it's in PATH
ARDUINO_CLI = "arduino-cli"

# Temp folder for sketch files
BUILD_DIR = tempfile.gettempdir()

# ── Health check ─────────────────────────────────────────────────
@app.route('/ping', methods=['GET'])
def ping():
    return jsonify({"status": "ok", "message": "Tecky Compile Server running"})

# ── Compile endpoint ─────────────────────────────────────────────
@app.route('/compile', methods=['POST'])
def compile_code():
    data = request.get_json()

    # Validate input
    if not data:
        return jsonify({"success": False, "error": "No JSON body"}), 400
    if 'code' not in data:
        return jsonify({"success": False, "error": "Missing 'code' field"}), 400
    if 'board' not in data:
        return jsonify({"success": False, "error": "Missing 'board' field"}), 400

    code      = data['code']    # Arduino .ino source code string
    board     = data['board']   # e.g. "arduino:avr:uno"
    proj_name = data.get('projectName', 'RobotSketch')

    # Sanitize project name
    proj_name = ''.join(c for c in proj_name if c.isalnum() or c == '_')
    if not proj_name:
        proj_name = 'RobotSketch'

    # Create unique temp folder for this compile job
    job_id     = str(uuid.uuid4())[:8]
    sketch_dir = os.path.join(BUILD_DIR, f"{proj_name}_{job_id}")
    output_dir = os.path.join(sketch_dir, "build")

    try:
        # Create sketch folder — folder name must match .ino filename
        os.makedirs(sketch_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)

        # Write .ino file
        ino_path = os.path.join(sketch_dir, f"{proj_name}.ino")
        with open(ino_path, 'w', encoding='utf-8') as f:
            f.write(code)

        print(f"[COMPILE] Board: {board}")
        print(f"[COMPILE] Sketch: {ino_path}")

        # Run Arduino CLI compile
        result = subprocess.run(
            [
                ARDUINO_CLI, "compile",
                "--fqbn", board,
                "--output-dir", output_dir,
                sketch_dir
            ],
            capture_output=True,
            text=True,
            timeout=120  # 2 min max
        )

        print(f"[COMPILE] Return code: {result.returncode}")
        print(f"[COMPILE] STDOUT: {result.stdout}")
        print(f"[COMPILE] STDERR: {result.stderr}")

        if result.returncode != 0:
            # Compile failed — return error message
            error_msg = result.stderr or result.stdout or "Unknown compile error"
            return jsonify({
                "success": False,
                "error": clean_error(error_msg)
            }), 200  # 200 so mobile can read JSON body

        # Find compiled output file
        hex_file = find_output_file(output_dir, board)

        if not hex_file:
            return jsonify({
                "success": False,
                "error": "Compile succeeded but output file not found"
            }), 200

        # Read and base64-encode the hex/bin file
        with open(hex_file, 'rb') as f:
            file_bytes = f.read()

        file_b64      = base64.b64encode(file_bytes).decode('utf-8')
        file_type     = "hex" if hex_file.endswith(".hex") else "bin"
        file_size     = len(file_bytes)

        return jsonify({
            "success":   True,
            "board":     board,
            "fileType":  file_type,
            "fileSize":  file_size,
            "fileName":  f"{proj_name}.{file_type}",
            "fileData":  file_b64,
            "message":   f"Compiled successfully ({file_size} bytes)"
        })

    except subprocess.TimeoutExpired:
        return jsonify({
            "success": False,
            "error": "Compile timeout (>2 min). Check Arduino CLI installation."
        }), 200

    except FileNotFoundError:
        return jsonify({
            "success": False,
            "error": f"arduino-cli not found. Install it and make sure it's in PATH."
        }), 200

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

    finally:
        # Clean up temp files
        try:
            shutil.rmtree(sketch_dir, ignore_errors=True)
        except:
            pass


# ── Helper: find .hex or .bin in output dir ──────────────────────
def find_output_file(output_dir, board):
    # Arduino UNO → .hex
    # ESP32      → .bin
    preferred = [".hex", ".bin", ".elf"]

    for ext in preferred:
        for fname in os.listdir(output_dir):
            if fname.endswith(ext):
                return os.path.join(output_dir, fname)
    return None


# ── Helper: clean Arduino CLI error messages ─────────────────────
def clean_error(raw):
    lines = raw.strip().split('\n')
    # Filter out internal path noise, keep useful lines
    useful = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if 'arduino-cli' in line.lower() and 'error' not in line.lower():
            continue
        useful.append(line)
    return '\n'.join(useful[:20])  # max 20 lines


# ── Run server ───────────────────────────────────────────────────
if __name__ == '__main__':
    print("=" * 50)
    print("  Tecky Compile Server")
    print("  Running on http://0.0.0.0:5000")
    print("  Mobile app connect to: http://<YOUR_PC_IP>:5000")
    print("=" * 50)
    app.run(host='0.0.0.0', port=5000, debug=True)
