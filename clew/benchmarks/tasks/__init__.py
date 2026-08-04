"""clew.benchmarks.tasks — task definitions for the agent-quality suite.

Each ``.py`` file in this package (or any sub-package) is a task
module. A task module must export::

    def build() -> TaskSpec: ...

The harness discovers task modules via :func:`clew.benchmarks.load_all_tasks`,
which walks this package with :func:`pkgutil.walk_packages`.

Sub-packages group tasks by section (``general``, ``heavy_code``,
``office``) — this is purely organisational; the section a task
actually runs in is determined by its ``TaskSpec.section`` field.
"""
