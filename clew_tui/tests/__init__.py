"""clew_tui.tests — Pilot-driven interaction tests for the TUI (G22b, issue #17).

This package contains tests that drive the TUI the way a person
actually does: press keys, see what happens on screen, assert the
right thing happened. They use Textual's ``App.run_test()`` which
returns a ``Pilot`` that can simulate real key presses, mouse clicks,
and let you inspect the resulting screen state.

The rest of the clew test suite checks STRUCTURE (bindings present,
correct widget classes, correct method signatures). These tests check
FUNCTION — that pressing a key actually causes the state change the
binding was supposed to trigger.

Run the whole interaction suite:

    pytest clew_tui/tests/ -m interaction

A single test file:

    pytest clew_tui/tests/test_palette_section.py -m interaction

The ``-m interaction`` marker filters out these tests from the normal
``pytest clew/`` run — they need Textual's test harness dependencies
which the rest of the suite doesn't.
"""
