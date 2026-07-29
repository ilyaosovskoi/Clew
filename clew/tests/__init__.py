"""Clew v2.0.2 tests package — covers G5, G6, M2, M3 + TUI slash commands.

Run all tests:
    python -m pytest clew/tests/ -v
Or individually:
    python -m pytest clew/tests/test_g5_agent_identity.py -v
    python -m pytest clew/tests/test_g6_handoff.py -v
    python -m pytest clew/tests/test_m2_cost_router.py -v
    python -m pytest clew/tests/test_m3_spend_dashboard.py -v
    python -m pytest clew/tests/test_tui_commands.py -v

Each test file is also runnable directly:
    python clew/tests/test_g5_agent_identity.py
"""
