#!/bin/bash
# Launch the Shared Brain Control Panel on http://localhost:8899
# Paths are derived from this script's location; the interpreter comes from
# bin/mem0-python (see that file for the resolution order).
set -eu
HERE=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
exec "$HERE/../bin/mem0-python" "$HERE/server.py"
