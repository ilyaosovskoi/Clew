#!/usr/bin/env python3
"""
Test 5: Daemon Task Submission
Tests the clew-daemon HTTP API for background task execution.
"""

import sys
import os
import json
import time
import threading
import subprocess
import requests
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_daemon():
    """Test daemon server and task submission."""
    print("=" * 60)
    print("TEST 5: Daemon Task Submission")
    print("=" * 60)

    # Start daemon server in background
    daemon_proc = subprocess.Popen(
        [sys.executable, "-m", "clew.daemon", "serve", "--port", "18766", "--workers", "1"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )

    # Wait for server to start
    time.sleep(3)

    try:
        # Get auth token from config
        config_path = os.path.expanduser("~/.clew/daemon.json")
        if not os.path.exists(config_path):
            print("Daemon config not found, waiting for server to generate...")
            time.sleep(2)

        with open(config_path, 'r') as f:
            config = json.load(f)
        token = config.get("auth_token")
        print(f"Using token: {token[:20]}...")

        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

        # Submit a task
        task_data = {
            "prompt": "Create a simple Python function that doubles a number",
            "workspace": os.getcwd()
        }

        print("Submitting task...")
        resp = requests.post("http://localhost:18766/task", json=task_data, headers=headers, timeout=10)
        print(f"Submit response: {resp.status_code} - {resp.text}")

        if resp.status_code != 201:
            print("❌ TEST FAILED - task submission failed")
            return False

        task = resp.json()
        task_id = task["id"]
        print(f"Task ID: {task_id}")

        # Poll for completion
        for i in range(60):  # Wait up to 60 seconds
            time.sleep(1)
            resp = requests.get(f"http://localhost:18766/task/{task_id}", headers=headers, timeout=5)
            task_info = resp.json()
            state = task_info["state"]
            print(f"  Poll {i}: state={state}")

            if state == "completed":
                print(f"Result: {task_info.get('result', '')[:200]}")
                print("✅ TEST PASSED")
                return True
            elif state in ("failed", "cancelled"):
                print(f"Error: {task_info.get('error')}")
                print("❌ TEST FAILED - task failed")
                return False

        print("❌ TEST FAILED - timeout")
        return False

    finally:
        # Stop daemon
        daemon_proc.terminate()
        daemon_proc.wait(timeout=5)
        print("Daemon stopped")


if __name__ == "__main__":
    success = test_daemon()
    sys.exit(0 if success else 1)