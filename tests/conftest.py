# typeguard's import hook is the test-enclave layer of the typing rule (CLAUDE.md, definition of
# done, step 3): deeper and slower than beartype, so it runs only here. It must be installed before
# the package is first imported, which is why it lives in conftest rather than in a test module.
from typeguard import install_import_hook

install_import_hook("beethoven_research")
