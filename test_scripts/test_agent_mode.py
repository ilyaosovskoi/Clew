#!/usr/bin/env python3
"""
Test 2: Agent Mode with Tools
Tests agent mode with file operations (read_file, write_file, etc.)
"""

import sys
import os
import tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from clew.agent_runtime import AgentRuntime, TaskType
from clew.providers import get_registry, ProviderConfig


def test_agent_mode():
    """Test agent mode with tools."""
    print("=" * 60)
    print("TEST 2: Agent Mode with Tools")
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
            max_tokens=500,
        )
    else:
        config = ProviderConfig(
            provider_id=provider_id,
            model="llama-3.3-70b-versatile" if provider_id == "groq" else "anthropic/claude-3.5-sonnet",
            api_key=os.environ.get(f"{provider_id.upper()}_API_KEY", ""),
            temperature=0.2,
            max_tokens=500,
        )

    registry.configure(provider_id, config)
    registry.set_active(provider_id)

    # Create temp workspace
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = os.path.join(tmpdir, "test.txt")

        # Create agent
        agent = AgentRuntime(
            registry=registry,
            workspace=tmpdir,
            max_iterations=5,
            enable_planning=True,
        )
        agent.set_autonomy("never_ask")

        # Test: create a file
        prompt = f"Create a file at {test_file} with content 'Hello from agent!'"
        print(f"\nPrompt: {prompt}")

        try:
            result = agent.run(prompt, task_type=TaskType.AGENTIC)
            print(f"Success: {result.success}")
            print(f"Output: {result.output}")
            print(f"Error: {result.error}")
            print(f"Tools used: {result.tool_calls}")

            # Check file was created
            if os.path.exists(test_file):
                with open(test_file, 'r') as f:
                    content = f.read()
                print(f"File content: {content}")

                if result.success and "Hello from agent" in content:
                    print("✅ TEST PASSED")
                    return True
                else:
                    print("❌ TEST FAILED - file content mismatch")
                    return False
            else:
                print("❌ TEST FAILED - file not created")
                return False

        except Exception as e:
            print(f"❌ TEST FAILED with exception: {e}")
            import traceback
            traceback.print_exc()
            return False


if __name__ == "__main__":
    success = test_agent_mode()
    sys.exit(0 if success else 1)