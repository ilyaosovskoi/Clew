#!/usr/bin/env python3
"""Basic sanity tests for the v2 agent package.

These tests verify that:
1. The `clew.agent` package imports cleanly.
2. Native fallback works (or native is loaded) — both paths.
3. InterjectionBuffer push/drain works.
4. CancelToken cancel/child works.
5. CircuitBreakerRegistry get/record works.
6. CompactionEngine code_compact / intra_compact / inter_compact work
   with a dummy sampler.
7. SubagentV2 built-ins are correctly defined.
8. EncryptedPromptStore round-trip works.

Run: pytest clew/agent/test_v2.py -v
Or:  python clew/agent/test_v2.py
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


class TestImports(unittest.TestCase):
    def test_import_main_package(self):
        import clew
        import clew.agent
        self.assertEqual(clew.__version__, "2.0.0")
        self.assertEqual(clew.agent.__version__, "2.0.0")

    def test_native_loader(self):
        from clew.agent.native import NATIVE_AVAILABLE, native_version, get_native_module
        # NATIVE_AVAILABLE is a bool either way.
        self.assertIsInstance(NATIVE_AVAILABLE, bool)
        # native_version returns str or None.
        v = native_version()
        self.assertTrue(v is None or isinstance(v, str))
        # get_native_module returns module or None.
        m = get_native_module()
        self.assertTrue(m is None or hasattr(m, "__name__"))


class TestInterjection(unittest.TestCase):
    def test_push_drain(self):
        from clew.agent import InterjectionBuffer
        buf = InterjectionBuffer()
        buf.push("hello")
        buf.push("world")
        drained = buf.drain()
        self.assertEqual(len(drained), 2)
        self.assertEqual(drained[0].raw_text, "hello")
        self.assertEqual(drained[1].raw_text, "world")
        # Drain again — should be empty.
        self.assertEqual(buf.drain_formatted(), None)

    def test_drain_formatted(self):
        from clew.agent import InterjectionBuffer
        buf = InterjectionBuffer()
        buf.push("first")
        buf.push("second")
        formatted = buf.drain_formatted()
        self.assertIsNotNone(formatted)
        self.assertIn("first", formatted)
        self.assertIn("second", formatted)
        self.assertIn("user_query", formatted)


class TestCancelToken(unittest.TestCase):
    def test_cancel(self):
        from clew.agent.actor import CancelToken
        t = CancelToken()
        self.assertFalse(t.is_cancelled())
        t.cancel("test reason")
        self.assertTrue(t.is_cancelled())
        self.assertEqual(t.reason, "test reason")
        # Subsequent cancel is a no-op (first reason wins).
        t.cancel("second reason")
        self.assertEqual(t.reason, "test reason")


class TestCircuitBreaker(unittest.TestCase):
    def test_breaker_opens(self):
        from clew.agent import CircuitBreakerRegistry
        # Force small thresholds.
        reg = CircuitBreakerRegistry(
            min_samples=4,
            error_rate_threshold=0.5,
            window_secs=60,
            open_duration_secs=15,
        )
        b = reg.get("test/breaker")
        # 2 success + 3 failure = 60% error rate over 5 samples
        for _ in range(2):
            b.record(ok=True)
        for _ in range(3):
            b.record(ok=False)
        self.assertTrue(b.is_open)

    def test_breaker_does_not_trip_below_min(self):
        from clew.agent import CircuitBreakerRegistry
        reg = CircuitBreakerRegistry(
            min_samples=10,
            error_rate_threshold=0.5,
            window_secs=60,
            open_duration_secs=15,
        )
        b = reg.get("test/breaker-min")
        for _ in range(5):
            b.record(ok=False)
        self.assertFalse(b.is_open)

    def test_registry_dedupes(self):
        from clew.agent import CircuitBreakerRegistry
        reg = CircuitBreakerRegistry()
        b1 = reg.get("openai/gpt-4o")
        b2 = reg.get("openai/gpt-4o")
        # Both calls should return equivalent breakers (same key, same metrics).
        self.assertEqual(b1.key, b2.key)


class TestCompaction(unittest.TestCase):
    def test_code_compact(self):
        from clew.agent import CompactionEngine, ConversationItem

        def dummy_sampler(prompt, items):
            return f"[SUMMARY of {len(items)} items]"

        engine = CompactionEngine(dummy_sampler)
        items = [ConversationItem(role="user", content=f"msg {i}", tokens=10) for i in range(5)]
        summary, fresh = engine.code_compact(items)
        self.assertIn("SUMMARY", summary)
        self.assertEqual(len(fresh), 1)
        self.assertEqual(fresh[0].role, "system")

    def test_intra_compact_keeps_tail(self):
        from clew.agent import CompactionEngine, ConversationItem

        def dummy_sampler(prompt, items):
            return f"[INTRA of {len(items)} items]"

        engine = CompactionEngine(dummy_sampler)
        items = [ConversationItem(role="user", content=f"msg {i}", tokens=10) for i in range(10)]
        _, new = engine.intra_compact(items, keep_recent=4)
        # 1 summary + 4 tail = 5
        self.assertEqual(len(new), 5)
        # Last 4 should be the original last 4 messages.
        for i, item in enumerate(new[1:]):
            self.assertIn(f"msg {6 + i}", item.content)

    def test_inter_compact_chunks(self):
        from clew.agent import CompactionEngine, ConversationItem

        def dummy_sampler(prompt, items):
            return f"[CHUNK of {len(items)}]"

        engine = CompactionEngine(dummy_sampler)
        items = [ConversationItem(role="user", content=f"msg {i}", tokens=10) for i in range(20)]
        _, new = engine.inter_compact(items, chunk_size=3, keep_recent=5)
        # 1 combined summary + 5 tail = 6
        self.assertEqual(len(new), 6)


class TestSubagentV2(unittest.TestCase):
    def test_builtins(self):
        from clew.agent import BUILTIN_SUBAGENTS
        names = [s.name for s in BUILTIN_SUBAGENTS]
        self.assertIn("explore", names)
        self.assertIn("plan", names)
        self.assertIn("general-purpose", names)

    def test_explore_has_no_write_tools(self):
        from clew.agent.subagent_v2 import EXPLORE_SUBAGENT
        self.assertNotIn("write_file", EXPLORE_SUBAGENT.tools)
        self.assertNotIn("str_replace", EXPLORE_SUBAGENT.tools)
        self.assertNotIn("run_code", EXPLORE_SUBAGENT.tools)
        self.assertNotIn("execute_command", EXPLORE_SUBAGENT.tools)
        # But it should have read-only tools.
        self.assertIn("read_file", EXPLORE_SUBAGENT.tools)
        self.assertIn("grep", EXPLORE_SUBAGENT.tools)

    def test_plan_has_no_write_tools(self):
        from clew.agent.subagent_v2 import PLAN_SUBAGENT
        self.assertNotIn("write_file", PLAN_SUBAGENT.tools)
        self.assertNotIn("str_replace", PLAN_SUBAGENT.tools)

    def test_general_purpose_has_all_tools(self):
        from clew.agent.subagent_v2 import GENERAL_PURPOSE_SUBAGENT
        self.assertIn("write_file", GENERAL_PURPOSE_SUBAGENT.tools)
        self.assertIn("str_replace", GENERAL_PURPOSE_SUBAGENT.tools)
        self.assertIn("run_code", GENERAL_PURPOSE_SUBAGENT.tools)


class TestEncryptedPrompt(unittest.TestCase):
    def test_round_trip(self):
        from clew.agent import EncryptedPromptStore
        store = EncryptedPromptStore(EncryptedPromptStore.derive_key("test-passphrase"))
        plaintext = "Hello, secret world! This is a system prompt.\nMulti-line."
        blob = store.encrypt(plaintext)
        # Blob starts with magic.
        self.assertTrue(blob.startswith(b"CLWP1"))
        decrypted = store.decrypt(blob)
        self.assertEqual(decrypted, plaintext)

    def test_wrong_key_fails(self):
        from clew.agent import EncryptedPromptStore, EncryptedPromptError
        store1 = EncryptedPromptStore(EncryptedPromptStore.derive_key("key1"))
        store2 = EncryptedPromptStore(EncryptedPromptStore.derive_key("key2"))
        blob = store1.encrypt("secret")
        # Decryption with wrong key should fail (with cryptography installed)
        # or return garbage (with XOR fallback). Either way it should NOT
        # return the original plaintext.
        try:
            result = store2.decrypt(blob)
            self.assertNotEqual(result, "secret")
        except EncryptedPromptError:
            pass  # expected when cryptography is installed


class TestSandbox(unittest.TestCase):
    def test_describe_state_initial(self):
        from clew.agent.sandbox import describe_state, current_sandbox_profile
        # If a previous test already applied a sandbox, just verify the API works.
        state = describe_state()
        self.assertIsInstance(state, str)
        profile = current_sandbox_profile()
        self.assertTrue(profile is None or isinstance(profile, str))


if __name__ == "__main__":
    unittest.main(verbosity=2)
