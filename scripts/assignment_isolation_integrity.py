import json
import subprocess
from pathlib import Path

import requests


BASE = "http://127.0.0.1:8001"
REPO = Path(r"C:\Users\Aung Thu Zaw\Desktop\Project\Kpi_management_system")
DEPT_B = "5a25d02d-c4a2-4606-962f-31c25c87cc4e"
DEPT_B_USER = "0c983ef6-1b74-4c99-85ad-21eda789a399"
DEPT_B_POSITION = "683d810e-de4f-4169-ab36-92bd3647a2ae"
FORMULA = "3b9761a7-4f64-4d54-a49c-d848cbf7cc28"
PERIOD = "05065734-342f-4a8d-8f01-f6803dd20c77"


def read_token(filename):
    return (REPO / filename).read_text().strip()


def count_assignments():
    result = subprocess.run(
        [
            "docker",
            "exec",
            "kpi-roleqa-postgres",
            "psql",
            "-At",
            "-U",
            "kpi_qa",
            "-d",
            "kpi_qa",
            "-c",
            "SELECT count(*) FROM kpi_assignments WHERE is_deleted = false;",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return int(result.stdout.strip())


roles = [
    ("Super Admin", ".tmp_sa_token", "86ffd106-3a7c-41c2-9783-5503dfc38d98"),
    ("Admin A", ".tmp_admin_a_token", "803424c9-f0c6-4c05-af2b-2b2407262167"),
    ("L0 A", ".tmp_token_l0_a_at_example_com", "68ec7a09-c383-4d1f-9172-e80bcd37657c"),
    ("L1 A", ".tmp_token_l1_a_at_example_com", "9b456b81-b596-4556-942d-f76598131b1f"),
    ("L2 A", ".tmp_token_l2_a_at_example_com", "ff5b04be-060c-4be2-b8d0-72b88eb5ad0b"),
    ("Department B user", ".tmp_token_deptb_user_at_example_com", DEPT_B_USER),
]

results = []
for role, token_file, own_user_id in roles:
    headers = {"Authorization": f"Bearer {read_token(token_file)}"}
    own = requests.get(
        f"{BASE}/api/v1/kpi/assignments/user/{own_user_id}",
        headers=headers,
    )
    results.append(
        {
            "role": role,
            "method": "GET",
            "endpoint": f"/api/v1/kpi/assignments/user/{own_user_id}",
            "status_code": own.status_code,
            "response": own.text[:500],
            "contains_department_b": (
                own.status_code == 200
                and any(item.get("department_id") == DEPT_B for item in own.json())
            ),
        }
    )

payload = {
    "department_id": DEPT_B,
    "position_id": DEPT_B_POSITION,
    "user_id": DEPT_B_USER,
    "kpi_formula_id": FORMULA,
    "kpi_period_id": PERIOD,
}
before = count_assignments()
denied = requests.post(
    f"{BASE}/api/v1/kpi/assignments/assign",
    headers={"Authorization": f"Bearer {read_token('.tmp_admin_a_token')}"},
    json=payload,
)
after = count_assignments()
results.append(
    {
        "role": "Admin A",
        "method": "POST",
        "endpoint": "/api/v1/kpi/assignments/assign",
        "status_code": denied.status_code,
        "response": denied.text[:500],
        "count_before": before,
        "count_after": after,
        "state_unchanged": before == after,
    }
)

output = {"results": results, "assignment_count_after_checks": after}
(REPO / ".tmp_assignment_isolation_integrity.json").write_text(
    json.dumps(output, indent=2), encoding="utf-8"
)
print(json.dumps(output, indent=2))
