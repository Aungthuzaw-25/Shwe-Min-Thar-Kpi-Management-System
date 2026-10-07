import json
import subprocess
from pathlib import Path

import requests


BASE = "http://127.0.0.1:8001"
REPO = Path(r"C:\Users\Aung Thu Zaw\Desktop\Project\Kpi_management_system")
RECORD_ID = "7f1d5c4a-1d14-4b84-9f8f-6e8d2a9e1c11"
DEPT_B_USER = "0c983ef6-1b74-4c99-85ad-21eda789a399"
DEPT_B = "5a25d02d-c4a2-4606-962f-31c25c87cc4e"

roles = [
    ("Super Admin", ".tmp_sa_token", "86ffd106-3a7c-41c2-9783-5503dfc38d98"),
    ("Admin A", ".tmp_admin_a_token", "803424c9-f0c6-4c05-af2b-2b2407262167"),
    ("L0 A", ".tmp_token_l0_a_at_example_com", "68ec7a09-c383-4d1f-9172-e80bcd37657c"),
    ("L1 A", ".tmp_token_l1_a_at_example_com", "9b456b81-b596-4556-942d-f76598131b1f"),
    ("L2 A", ".tmp_token_l2_a_at_example_com", "ff5b04be-060c-4be2-b8d0-72b88eb5ad0b"),
    ("Department B user", ".tmp_token_deptb_user_at_example_com", DEPT_B_USER),
]


def headers(token_file):
    return {"Authorization": f"Bearer {(REPO / token_file).read_text().strip()}"}


def db_state():
    sql = (
        "SELECT id::text,raw_score,weighted_score,percentage,status_bucket::text,"
        "is_frozen,is_overridden,coalesce(override_reason,''),is_deleted "
        f"FROM kpi_records WHERE id='{RECORD_ID}'; "
        f"SELECT count(*) FROM kpi_history WHERE kpi_record_id='{RECORD_ID}'; "
        f"SELECT count(*) FROM audit_logs WHERE table_name='kpi_records' AND record_id='{RECORD_ID}';"
    )
    result = subprocess.run(
        [
            "docker", "exec", "kpi-roleqa-postgres", "psql", "-At",
            "-U", "kpi_qa", "-d", "kpi_qa", "-c", sql,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    lines = [line for line in result.stdout.splitlines() if line]
    record = lines[0].split("|") if lines else []
    return {
        "record": record,
        "history_count": int(lines[1]) if len(lines) > 1 else None,
        "audit_count": int(lines[2]) if len(lines) > 2 else None,
    }


results = []

for role, token_file, user_id in roles:
    collection = requests.get(
        f"{BASE}/api/v1/kpi/records/monthly/{DEPT_B_USER}",
        headers=headers(token_file),
    )
    body = collection.text[:1000]
    contains = (
        collection.status_code == 200
        and any(item.get("id") == RECORD_ID for item in collection.json())
    )
    results.append(
        {
            "role": role,
            "method": "GET",
            "endpoint": "/api/v1/kpi/records/monthly/{dept_b_user_id}",
            "body": None,
            "target": f"Department B record {RECORD_ID}",
            "status_code": collection.status_code,
            "response": body,
            "contains_record": contains,
        }
    )

    own_collection = requests.get(
        f"{BASE}/api/v1/kpi/records/monthly/{user_id}",
        headers=headers(token_file),
    )
    own_contains = (
        own_collection.status_code == 200
        and any(item.get("id") == RECORD_ID for item in own_collection.json())
    )
    results.append(
        {
            "role": role,
            "method": "GET",
            "endpoint": f"/api/v1/kpi/records/monthly/{user_id}",
            "body": None,
            "target": "Caller-owned collection",
            "status_code": own_collection.status_code,
            "response": own_collection.text[:1000],
            "contains_dept_b_record": own_contains,
        }
    )

    detail = requests.get(
        f"{BASE}/api/v1/kpi/records/{RECORD_ID}",
        headers=headers(token_file),
    )
    results.append(
        {
            "role": role,
            "method": "GET",
            "endpoint": f"/api/v1/kpi/records/{RECORD_ID}",
            "body": None,
            "target": RECORD_ID,
            "status_code": detail.status_code,
            "response": detail.text[:500],
            "route_present": detail.status_code != 404,
        }
    )

mutation_payloads = {
    "freeze": {"is_frozen": True},
    "override": {"new_score": 4.0, "reason": "Authorization matrix valid mutation"},
    "deduct": {"deduct_points": 0.5, "reason": "Authorization matrix valid deduction"},
}

for role, token_file, _ in roles:
    for action, payload in mutation_payloads.items():
        before = db_state()
        response = requests.put(
            f"{BASE}/api/v1/kpi/records/{RECORD_ID}/{action}",
            headers=headers(token_file),
            json=payload,
        )
        after = db_state()
        results.append(
            {
                "role": role,
                "method": "PUT",
                "endpoint": f"/api/v1/kpi/records/{{record_id}}/{action}",
                "body": payload,
                "target": RECORD_ID,
                "status_code": response.status_code,
                "response": response.text[:700],
                "before": before,
                "after": after,
                "state_unchanged": before == after,
            }
        )

for role, token_file, _ in roles:
    before = db_state()
    response = requests.delete(
        f"{BASE}/api/v1/kpi/records/{RECORD_ID}",
        headers=headers(token_file),
    )
    after = db_state()
    results.append(
        {
            "role": role,
            "method": "DELETE",
            "endpoint": f"/api/v1/kpi/records/{RECORD_ID}",
            "body": None,
            "target": RECORD_ID,
            "status_code": response.status_code,
            "response": response.text[:500],
            "route_present": response.status_code != 404,
            "before": before,
            "after": after,
            "state_unchanged": before == after,
        }
    )

# Authorized lifecycle/mutation checks. Restore the fixture to its original
# score and unfrozen state after each successful operation.
sa_headers = headers(".tmp_sa_token")
authorized = []
for action, payload in (
    ("freeze", {"is_frozen": True}),
    ("freeze", {"is_frozen": False}),
    ("override", {"new_score": 4.0, "reason": "Authorized record test"}),
    ("override", {"new_score": 5.0, "reason": "Restore synthetic fixture"}),
    ("deduct", {"deduct_points": 0.5, "reason": "Authorized deduction test"}),
    ("override", {"new_score": 5.0, "reason": "Restore synthetic fixture"}),
):
    before = db_state()
    response = requests.put(
        f"{BASE}/api/v1/kpi/records/{RECORD_ID}/{action}",
        headers=sa_headers,
        json=payload,
    )
    after = db_state()
    authorized.append(
        {
            "role": "Super Admin",
            "method": "PUT",
            "endpoint": f"/api/v1/kpi/records/{{record_id}}/{action}",
            "body": payload,
            "status_code": response.status_code,
            "response": response.text[:700],
            "before": before,
            "after": after,
        }
    )

output = {"record_id": RECORD_ID, "results": results, "authorized": authorized, "final_state": db_state()}
(REPO / ".tmp_record_matrix_results.json").write_text(
    json.dumps(output, indent=2), encoding="utf-8"
)
print(json.dumps(output, indent=2))
