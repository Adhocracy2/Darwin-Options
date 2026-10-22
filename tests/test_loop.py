"""The trader loop stops cleanly on SIGTERM (what systemd sends during a deploy)."""
import os
import signal
import subprocess
import sys
import time


def test_sigterm_stops_after_current_cycle(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(f"mode: sim\ndata: synthetic\ndb_path: {tmp_path}/t.db\nlog_path: {tmp_path}/t.log\n")
    proc = subprocess.Popen([sys.executable, "-m", "darwin_options.cli", "-c", str(cfg), "loop", "--interval", "300"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    log = tmp_path / "t.log"
    deadline = time.time() + 60
    while time.time() < deadline and "cycle:" not in (log.read_text() if log.exists() else ""):
        time.sleep(0.2)
    assert "cycle:" in log.read_text(), "first cycle never finished"
    t0 = time.time()
    os.kill(proc.pid, signal.SIGTERM)
    proc.wait(timeout=20)
    assert time.time() - t0 < 10  # didn't wait out the 300s sleep
    assert proc.returncode == 0 and "trader stopped cleanly" in log.read_text()
