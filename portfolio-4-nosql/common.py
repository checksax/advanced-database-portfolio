"""Shared connection settings for the Portfolio 4 scripts."""

import os
from datetime import datetime

PG_DSN = os.environ.get(
    "HOSPITAL_DSN",
    "postgresql://hospital_admin:hospital_pass@localhost:5432/stgrace_hospital",
)
MONGO_URI = os.environ.get("HOSPITAL_MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = "stgrace_hospital"
AS_OF = datetime(2026, 11, 3, 9, 0)      # "today" in the simulated hospital (same as Portfolio 1)
