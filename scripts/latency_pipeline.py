import subprocess
import sys
import os
import time
from pathlib import Path

# CONFIGURATION
BACKEND_LOG = "backend_latency_logs.jsonl"
FRONTEND_LOG = "frontend_latency_logs.jsonl"
PARSE_SCRIPT = "scripts/parse_latency_logs.py"
# MCP server lives in mcp-server/ and listens on port 8001
MCP_SERVER_DIR = Path(__file__).parent.parent / "mcp-server"
MCP_SERVER_PORT = 8001

# 1. Start backend and redirect logs
def start_backend():
    print("[1] Starting MCP backend server on port", MCP_SERVER_PORT, "— logging to", BACKEND_LOG)
    backend_cmd = [
        sys.executable, '-m', 'uvicorn', 'app.main:app', '--port', str(MCP_SERVER_PORT)
    ]
    with open(BACKEND_LOG, 'w') as f:
        proc = subprocess.Popen(
            backend_cmd,
            stdout=f,
            stderr=subprocess.STDOUT,
            cwd=str(MCP_SERVER_DIR),
        )
    print("Backend started. PID:", proc.pid)
    return proc

# 2. (Manual) Run frontend and collect logs
#    User must add frontend logging code and save logs to FRONTEND_LOG

def wait_for_user_queries():
    print("[2] Please run representative queries in the frontend/webapp.")
    print("    Make sure frontend logging is enabled and logs are saved to:", FRONTEND_LOG)
    input("Press Enter when you have finished running test queries...")

# 3. Parse and aggregate logs
def parse_logs():
    print("[3] Parsing and aggregating logs...")
    if not Path(PARSE_SCRIPT).exists():
        print(f"ERROR: {PARSE_SCRIPT} not found!")
        return
    if not Path(BACKEND_LOG).exists() and not Path(FRONTEND_LOG).exists():
        print("ERROR: No logs found!")
        return
    # Merge logs if both exist
    merged_log = "latency_merged.jsonl"
    with open(merged_log, 'w') as out:
        for logf in [BACKEND_LOG, FRONTEND_LOG]:
            if Path(logf).exists():
                with open(logf) as f:
                    for line in f:
                        out.write(line)
    subprocess.run([sys.executable, PARSE_SCRIPT, merged_log])

# 4. Stop backend
def stop_backend(proc):
    print("[4] Stopping backend server...")
    proc.terminate()
    proc.wait()
    print("Backend stopped.")

if __name__ == "__main__":
    backend_proc = start_backend()
    try:
        wait_for_user_queries()
        parse_logs()
    finally:
        stop_backend(backend_proc)
