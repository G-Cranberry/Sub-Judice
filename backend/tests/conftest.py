import os
import tempfile

_tmp = tempfile.mkdtemp()
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp}/test.db")  # export DATABASE_URL=postgresql://... to run the suite on Postgres
os.environ["UPLOAD_DIR"] = f"{_tmp}/uploads"
os.environ["STAFF_TOKEN"] = "secret"
os.environ["PROTECT_READS"] = "0"  # v0.2-style tests; tests/test_auth.py switches it on per test
os.environ["MAX_UPLOAD_MB"] = "1"  # so the size-limit test doesn't need a huge file
