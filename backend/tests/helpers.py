from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
STAFF = {"X-Staff-Token": "secret"}


def file_case(c=client, files=None, **over):
    data = {"title": "ATM dispute", "filer_name": "Amar Nath", "description": "Two ATM debits of Rs 10,000",
            "case_type": "consumer", "claim_amount": "20000"}
    data.update(over)
    if files is None:
        files = [("files", ("slip.txt", b"withdrawal slip", "text/plain"))]
    return c.post("/cases", data=data, files=files)
