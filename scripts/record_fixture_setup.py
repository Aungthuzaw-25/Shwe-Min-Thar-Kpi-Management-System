import json
from pathlib import Path

import requests


BASE = "http://127.0.0.1:8001"
REPO = Path(r"C:\Users\Aung Thu Zaw\Desktop\Project\Kpi_management_system")
FORMULA_ID = "3b9761a7-4f64-4d54-a49c-d848cbf7cc28"
USER_ID = "0c983ef6-1b74-4c99-85ad-21eda789a399"


def login():
    response = requests.post(
        f"{BASE}/api/v1/auth/login",
        json={"email": "sa.local@kpi.test", "password": ""},
    )
    response.raise_for_status()
    token = response.json()["access_token"]
    (REPO / ".tmp_sa_token").write_text(token, encoding="utf-8")
    return {"Authorization": f"Bearer {token}"}


headers = login()
workflow = []
for action in ("submit", "approve", "activate"):
    response = requests.post(
        f"{BASE}/api/v1/kpi/formulas/{FORMULA_ID}/{action}",
        headers=headers,
    )
    workflow.append({"action": action, "status_code": response.status_code, "response": response.text[:500]})
    response.raise_for_status()

calculation = requests.post(
    f"{BASE}/api/v1/kpi/calculate",
    headers=headers,
    json={
        "user_ids": [USER_ID],
        "start_date": "2026-09-01T00:00:00Z",
        "end_date": "2026-09-30T23:59:59Z",
        "run_in_background": False,
    },
)
calculation.raise_for_status()

output = {"workflow": workflow, "calculation": calculation.json()}
(REPO / ".tmp_record_fixture_setup.json").write_text(
    json.dumps(output, indent=2), encoding="utf-8"
)
print(json.dumps(output, indent=2))
