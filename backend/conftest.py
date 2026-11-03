import os
import sys
from pathlib import Path

# Make `app` importable and keep test data out of the real data/ folder.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("LV_DATA_DIR", "/tmp/lobsterview-test-data")
