import json
import os
from pathlib import Path

import requests


BASE = "http://127.0.0.1:8001"
REPO = Path(r"C:\Users\Aung Thu Zaw\Desktop\Project\Kpi_management_system")
DEPT_A = "29c02451-c65d-47f9-8f77-177217ce2d05"
DEPT_B = "5a25d02d-c4a2-4606-962f-31c25c87cc4e"
DEPT_B_USER = "0c983ef6-1b74-4c99-85ad-21eda789a399"
PERIOD = "05065734-342f-4a8d-8f01-f6803dd20c77"


def token(name):
    return (REPO / name).read_text().strip()


def auth(name):
    return {"Authorization": f"Bearer {token(name)}"}


sa_headers = auth(".tmp_sa_token")
position_response = requests.post(
    f"{BASE}/api/v1/positions/",
    headers=sa_headers,
    json={"name": "QA Engineer - Dept B", "department_id": DEPT_B, "is_active": True},
)
if position_response.status_code not in (201, 400):
    raise RuntimeError(f"position setup failed: {position_response.status_code} {position_response.text}")

positions = requests.get(
    f"{BASE}/api/v1/positions/department/{DEPT_B}",
    headers=sa_headers,
)
positions.raise_for_status()
position = next(item for item in positions.json() if item["name"] == "QA Engineer - Dept B")
position_id = position["id"]

user_update = requests.put(
    f"{BASE}/api/v1/users/{DEPT_B_USER}",
    headers=sa_headers,
    json={"position_id": position_id},
)
user_update.raise_for_status()

formulas = requests.get(f"{BASE}/api/v1/kpi/formulas", headers=sa_headers)
formulas.raise_for_status()
formula = next(
    item
    for item in formulas.json()
    if item["department_id"] == DEPT_B and item["is_active"]
)
formula_id = formula["id"]

assignment_payload = {
    "department_id": DEPT_B,
    "position_id": position_id,
    "user_id": DEPT_B_USER,
    "kpi_formula_id": formula_id,
    "kpi_period_id": PERIOD,
}

created = requests.post(
    f"{BASE}/api/v1/kpi/assignments/assign",
    headers=sa_headers,
    json=assignment_payload,
)
if created.status_code == 400 and "Duplicate assignment" in created.text:
    existing = requests.get(
        f"{BASE}/api/v1/kpi/assignments/user/{DEPT_B_USER}",
        headers=sa_headers,
    )
    existing.raise_for_status()
    assignment = next(
        item
        for item in existing.json()
        if item["kpi_formula_id"] == formula_id and item["kpi_period_id"] == PERIOD
    )
else:
    created.raise_for_status()
    assignment = created.json()

assignment_id = assignment["id"]
roles = [
    ("Super Admin", ".tmp_sa_token", DEPT_B_USER),
    ("Admin A", ".tmp_admin_a_token", DEPT_B_USER),
    ("L0 A", ".tmp_token_l0_a_at_example_com", DEPT_B_USER),
    ("L1 A", ".tmp_token_l1_a_at_example_com", DEPT_B_USER),
    ("L2 A", ".tmp_token_l2_a_at_example_com", DEPT_B_USER),
    ("Department B user", ".tmp_token_deptb_user_at_example_com", DEPT_B_USER),
]

results = []
for role, token_file, target_user in roles:
    headers = auth(token_file)
    collection_url = f"{BASE}/api/v1/kpi/assignments/user/{target_user}"
    collection = requests.get(collection_url, headers=headers)
    results.append(
        {
            "role": role,
            "method": "GET",
            "endpoint": "/api/v1/kpi/assignments/user/{dept_b_user_id}",
            "body": None,
            "target": f"Department B user {target_user}",
            "status_code": collection.status_code,
            "response": collection.text[:500],
            "contains_fixture": (
                collection.status_code == 200
                and any(item.get("id") == assignment_id for item in collection.json())
            ),
        }
    )

    create = requests.post(
        f"{BASE}/api/v1/kpi/assignments/assign",
        headers=headers,
        json=assignment_payload,
    )
    results.append(
        {
            "role": role,
            "method": "POST",
            "endpoint": "/api/v1/kpi/assignments/assign",
            "body": assignment_payload,
            "target": f"Department B assignment {assignment_id}",
            "status_code": create.status_code,
            "response": create.text[:500],
        }
    )

    bulk_payload = {
        "department_id": DEPT_B,
        "position_id": position_id,
        "user_ids": [DEPT_B_USER],
        "kpi_formula_id": formula_id,
        "kpi_period_id": PERIOD,
    }
    bulk = requests.post(
        f"{BASE}/api/v1/kpi/assignments/bulk-assign",
        headers=headers,
        json=bulk_payload,
    )
    results.append(
        {
            "role": role,
            "method": "POST",
            "endpoint": "/api/v1/kpi/assignments/bulk-assign",
            "body": bulk_payload,
            "target": f"Department B assignment {assignment_id}",
            "status_code": bulk.status_code,
            "response": bulk.text[:500],
        }
    )

for role, token_file, target_user in roles:
    headers = auth(token_file)
    # Object-ID substitution: the only detail-shaped route is the per-user
    # collection, so the valid Department B user ID is substituted directly.
    route_probe = requests.get(
        f"{BASE}/api/v1/kpi/assignments/{assignment_id}",
        headers=headers,
    )
    results.append(
        {
            "role": role,
            "method": "GET",
            "endpoint": f"/api/v1/kpi/assignments/{assignment_id}",
            "body": None,
            "target": f"Assignment object {assignment_id}",
            "status_code": route_probe.status_code,
            "response": route_probe.text[:500],
            "route_present": route_probe.status_code != 404,
        }
    )

output = {
    "fixture": {
        "department_b": DEPT_B,
        "user_id": DEPT_B_USER,
        "position_id": position_id,
        "formula_id": formula_id,
        "period_id": PERIOD,
        "assignment_id": assignment_id,
        "assignment": assignment,
    },
    "results": results,
}
(REPO / ".tmp_assignment_matrix_results.json").write_text(
    json.dumps(output, indent=2, default=str),
    encoding="utf-8",
)
print(json.dumps(output["fixture"], indent=2, default=str))
