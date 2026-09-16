"""
Shared mutable singletons used across controllers.

Controllers MUST access these as `dependencies.db_manager` / `dependencies.limiter`
(module-attribute lookup at call time), never `from backend.dependencies import db_manager`.
Tests reassign `backend.dependencies.db_manager` wholesale (see
tests/test_endpoints_integration.py::setup_test_db); a captured name binding would not
see that reassignment.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")
from ratelimiter.client.ratelimiter_client import RateLimiterClient

from backend.databases.database import DatabaseManager

db_manager = DatabaseManager()
limiter = RateLimiterClient("http://localhost:8002")
