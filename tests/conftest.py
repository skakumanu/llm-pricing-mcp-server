"""Test configuration for pytest."""
import sys
import os
from pathlib import Path

# Add project root directory to Python path for imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Set a high rate limit for tests to exercise middleware without causing 429 responses
# from multiple requests coming from the same IP (127.0.0.1).
# This allows testing of the rate limiting code path while preventing false failures.
os.environ["RATE_LIMIT_PER_MINUTE"] = "10000"
