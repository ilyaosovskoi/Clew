#!/usr/bin/env python3
"""
Test 3: Heavy Code Mode
Tests heavy-code section with multi-agent refactoring capabilities.
"""

import sys
import os
import tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from clew.agent_runtime import AgentRuntime, TaskType
from clew.providers import get_registry, ProviderConfig


def test_heavy_code():
    """Test heavy code mode."""
    print("=" * 60)
    print("TEST 3: Heavy Code Mode")
    print("=" * 60)

    # Setup registry
    registry = get_registry()
    registry.register_default()

    available = [p['id'] for p in registry.list_providers()]
    print(f"Available providers: {available}")

    provider_id = None
    for pid in ['ollama', 'lmstudio', 'groq', 'openrouter']:
        if pid in available:
            provider_id = pid
            break

    if not provider_id:
        print("❌ No provider available")
        return False

    print(f"Using provider: {provider_id}")

    # Configure provider
    if provider_id in ['ollama', 'lmstudio']:
        config = ProviderConfig(
            provider_id=provider_id,
            model="llama3.1" if provider_id == "ollama" else "",
            api_base="http://localhost:11434/v1" if provider_id == "ollama" else "http://localhost:1234/v1",
            temperature=0.2,
            max_tokens=1000,
        )
    else:
        config = ProviderConfig(
            provider_id=provider_id,
            model="llama-3.3-70b-versatile" if provider_id == "groq" else "anthropic/claude-3.5-sonnet",
            api_key=os.environ.get(f"{provider_id.upper()}_API_KEY", ""),
            temperature=0.2,
            max_tokens=1000,
        )

    registry.configure(provider_id, config)
    registry.set_active(provider_id)

    # Create temp workspace with a simple project
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a simple Python file to refactor
        src_file = os.path.join(tmpdir, "calculator.py")
        with open(src_file, 'w') as f:
            f.write("""
def add(a, b):
    return a + b

def subtract(a, b):
    return a - b

def multiply(a, b):
    return a * b

def divide(a, b):
    if b != 0:
        return a / b
    return None
""")

        # Create agent with heavy_code section
        agent = AgentRuntime(
            registry=registry,
            workspace=tmpdir,
            max_iterations=15,
            enable_planning=True,
            section="heavy_code",
        )
        agent.set_autonomy("never_ask")

        prompt = f"Refactor {src_file} to use a Calculator class with methods add, subtract, multiply, divide. Add type hints."
        print(f"\nPrompt: {prompt}")

        try:
            result = agent.run(prompt, task_type=TaskType.AGENTIC)
            print(f"Success: {result.success}")
            print(f"Output: {result.output[:500]}...")
            print(f"Error: {result.error}")
            print(f"Tools used: {len(result.tool_calls) if result.tool_calls else 0}")

            # Check if file was refactored
            if os.path.exists(src_file):
                with open(src_file, 'r') as f:
                    content = f.read()
                print(f"Refactored content:\n{content}")

                if result.success and "class Calculator" in content:
                    print("✅ TEST PASSED")
                    return True
                else:
                    print("❌ TEST FAILED - no class found")
                    return False
            else:
                print("❌ TEST FAILED - file missing")
                return False

        except Exception as e:
            print(f"❌ TEST FAILED with exception: {e}")
            import traceback
            traceback.print_exc()
            return False


if __name__ == "__main__":
    success = test_heavy_code()
    sys.exit(0 if success else 1)