#!/usr/bin/env python3
"""
Test 1: Basic Chat Mode
Tests single-turn chat without tools.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from clew.agent_runtime import AgentRuntime
from clew.providers import get_registry, ProviderConfig


def test_basic_chat():
    """Test basic chat functionality."""
    print("=" * 60)
    print("TEST 1: Basic Chat Mode")
    print("=" * 60)

    # Setup registry
    registry = get_registry()
    registry.register_default()

    # Find available provider
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
            max_tokens=100,
        )
    else:
        config = ProviderConfig(
            provider_id=provider_id,
            model="llama-3.3-70b-versatile" if provider_id == "groq" else "anthropic/claude-3.5-sonnet",
            api_key=os.environ.get(f"{provider_id.upper()}_API_KEY", ""),
            temperature=0.2,
            max_tokens=100,
        )

    registry.configure(provider_id, config)
    registry.set_active(provider_id)

    # Create agent
    agent = AgentRuntime(
        registry=registry,
        workspace=os.getcwd(),
        max_iterations=3,
        enable_planning=False,
    )

    # Test chat
    prompt = "Say 'hello world' in exactly 3 words."
    print(f"\nPrompt: {prompt}")

    try:
        result = agent.chat(prompt)
        print(f"Success: {result.success}")
        print(f"Output: {result.output}")
        print(f"Error: {result.error}")

        if result.success and "hello world" in result.output.lower():
            print("✅ TEST PASSED")
            return True
        else:
            print("❌ TEST FAILED - unexpected output")
            return False
    except Exception as e:
        print(f"❌ TEST FAILED with exception: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = test_basic_chat()
    sys.exit(0 if success else 1)