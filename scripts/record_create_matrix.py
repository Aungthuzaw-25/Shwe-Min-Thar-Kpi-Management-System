import json
import subprocess
from pathlib import Path

import requests


BASE = "http://127.0.0.1:8001"
REPO = Path(r"C:\Users\Aung Thu Zaw\Desktop\Project\Kpi_management_system")
RECORD_ID = "7f1d5c4a-1d14-4b84-9f8f-6e8d2a9e1c11"
DEPT_B_USER = "0c983ef6-1b74-4c99-85ad-21eda789a399"

roles = [
    ("Admin A", ".tmp_admin_a_token"),
    ("L0 A", ".tmp_token_l0_a_at_example_com"),
    ("L1 A", ".tmp_token_l1_a_at_example_com"),
    ("L2 A", ".tmp_token_l2_a_at_example_com"),
    ("Department B user", ".tmp_token_deptb_user_at_example_com"),
]


def state():
    sql = (
        "SELECT count(*) FROM kpi_records WHERE is_deleted=false; "
        f"SELECT id::text,raw_score,weighted_score,percentage,status_bucket::text,is_frozen,is_overridden "
        f"FROM kpi_records WHERE id='{RECORD_ID}';"
    )
    result = subprocess.run(
        ["docker", "exec", "kpi-roleqa-postgres", "psql", "-At", "-U", "kpi_qa", "-d", "kpi_qa", "-c", sql],
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line]


payload = {
    "user_ids": [DEPT_B_USER],
    "start_date": "2026-09-01T00:00:00Z",
    "end_date": "2026-09-30T23:59:59Z",
    "run_in_background": False,
}
results = []
for role, token_file in roles:
    before = state()
    response = requests.post(
        f"{BASE}/api/v1/kpi/calculate",
        headers={"Authorization": f"Bearer {(REPO / token_file).read_text().strip()}"},
        json=payload,
    )
    after = state()
    results.append(
        {
            "role": role,
            "method": "POST",
            "endpoint": "/api/v1/kpi/calculate",
            "body": payload,
            "target": f"Department B user {DEPT_B_USER}",
            "status_code": response.status_code,
            "response": response.text[:500],
            "before": before,
            "after": after,
            "state_unchanged": before == after,
        }
    )

(REPO / ".tmp_record_create_matrix.json").write_text(
    json.dumps(results, indent=2), encoding="utf-8"
)
print(json.dumps(results, indent=2))
