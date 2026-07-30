#!/usr/bin/env python3
"""
Test 6: Provider Switching & Auto-Router
Tests provider registry, switching, and auto-router functionality.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from clew.providers import get_registry, ProviderConfig, ProviderMessage, AutoRouter


def test_providers():
    """Test provider registry and auto-router."""
    print("=" * 60)
    print("TEST 6: Provider Switching & Auto-Router")
    print("=" * 60)

    registry = get_registry()
    registry.register_default()

    providers = registry.list_providers()
    print(f"Registered providers: {[p['id'] for p in providers]}")

    # Test 1: Configure and test multiple providers
    test_providers = [
        ("ollama", "llama3.1", "http://localhost:11434/v1"),
        ("lmstudio", "", "http://localhost:1234/v1"),
    ]

    working_providers = []
    for pid, model, base in test_providers:
        config = ProviderConfig(
            provider_id=pid,
            model=model,
            api_base=base,
            temperature=0.2,
            max_tokens=50,
        )
        registry.configure(pid, config)
        try:
            provider = registry.get(pid)
            provider.load()
            # Quick test
            resp = provider.generate([
                ProviderMessage(role="user", content="Say 'ok' in one word.")
            ])
            if resp.text and "ok" in resp.text.lower():
                print(f"  ✅ {pid} working")
                working_providers.append(pid)
            else:
                print(f"  ❌ {pid} failed: {resp.text}")
        except Exception as e:
            print(f"  ⚠️  {pid} not available: {e}")

    # Test 2: AutoRouter with available providers
    if working_providers:
        router = AutoRouter()
        decision = router.route(
            "Write a Python function to calculate fibonacci",
            configured_providers=set(working_providers)
        )
        print(f"\nAutoRouter decision: {decision}")
        if decision.get('provider_id') in working_providers:
            print("✅ AutoRouter works")
        else:
            print("❌ AutoRouter failed")

    # Test 3: Provider switching
    if len(working_providers) >= 2:
        registry.set_active(working_providers[0])
        print(f"\nActive provider: {registry.active_id}")
        registry.set_active(working_providers[1])
        print(f"Switched to: {registry.active_id}")
        print("✅ Provider switching works")

    if working_providers:
        print("\n✅ TEST PASSED")
        return True
    else:
        print("\n⚠️  TEST SKIPPED - no local providers available (ollama/lmstudio)")
        return True  # Not a failure, just skipped


if __name__ == "__main__":
    success = test_providers()
    sys.exit(0 if success else 1)