"""tests/ is a package so `python -m unittest discover -s .` finds the suite.

That is how crapper and mutator run unittest, from the repo root, and unittest
discovery only enters packages: without this file they ran 0 tests and scored
an empty run (infra-ik4p.23). CI runs `discover -s tests`, where the test
modules are top-level. They import their siblings by bare name (`import
fleetlib`, `import yamlv3`), so this directory is put on sys.path for the
package mode too.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
