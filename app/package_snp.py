"""Attach the provider's original Storm capability archive."""
from pathlib import Path
import sys
Path(sys.argv[2]).write_bytes(Path(sys.argv[1]).read_bytes()+Path(sys.argv[3]).read_bytes())
