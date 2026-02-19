"""Test configuration for pytest."""
import sys
import os
from pathlib import Path

# Add project root directory to Python path for imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Disable rate limiting for tests to prevent 429 responses from multiple requests
# coming from same IP (127.0.0.1)
os.environ["RATE_LIMIT_PER_MINUTE"] = "0"
