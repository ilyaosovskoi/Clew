#!/usr/bin/env python3
"""
Test 4: CLI Headless Mode
Tests the clew.cli module running headless.
"""

import sys
import os
import subprocess
import tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_cli_headless():
    """Test CLI headless mode."""
    print("=" * 60)
    print("TEST 4: CLI Headless Mode")
    print("=" * 60)

    # Create temp workspace with a simple file
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = os.path.join(tmpdir, "hello.py")
        with open(test_file, 'w') as f:
            f.write("def greet():\n    return 'hello'\n")

        # Run clew-cli
        cmd = [
            sys.executable, "-m", "clew.cli",
            "run", f"Add a farewell function to {test_file} that returns 'goodbye'",
            "--workspace", tmpdir,
            "--provider", "ollama",  # Use local provider
            "--autonomy", "never_ask",
            "--max-iterations", "5"
        ]

        print(f"Running: {' '.join(cmd)}")

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            print(f"Return code: {result.returncode}")
            print(f"STDOUT:\n{result.stdout}")
            if result.stderr:
                print(f"STDERR:\n{result.stderr}")

            # Check if file was modified
            if os.path.exists(test_file):
                with open(test_file, 'r') as f:
                    content = f.read()
                print(f"File content:\n{content}")

                if result.returncode == 0 and "farewell" in content.lower() and "goodbye" in content.lower():
                    print("✅ TEST PASSED")
                    return True
                else:
                    print("❌ TEST FAILED - file not modified correctly")
                    return False
            else:
                print("❌ TEST FAILED - file missing")
                return False

        except subprocess.TimeoutExpired:
            print("❌ TEST FAILED - timeout")
            return False
        except Exception as e:
            print(f"❌ TEST FAILED with exception: {e}")
            import traceback
            traceback.print_exc()
            return False


if __name__ == "__main__":
    success = test_cli_headless()
    sys.exit(0 if success else 1)