import json
import subprocess
from pathlib import Path

import requests


BASE = "http://127.0.0.1:8001"
REPO = Path(r"C:\Users\Aung Thu Zaw\Desktop\Project\Kpi_management_system")
DEPT_A = "29c02451-c65d-47f9-8f77-177217ce2d05"
DEPT_B = "5a25d02d-c4a2-4606-962f-31c25c87cc4e"
SUPER_ADMIN_TOKEN_FILE = ".tmp_sa_token"

roles = [
    ("Super Admin", SUPER_ADMIN_TOKEN_FILE),
    ("Admin A", ".tmp_admin_a_token"),
    ("L0 A", ".tmp_token_l0_a_at_example_com"),
    ("L1 A", ".tmp_token_l1_a_at_example_com"),
    ("L2 A", ".tmp_token_l2_a_at_example_com"),
    ("Department B user", ".tmp_token_deptb_user_at_example_com"),
]


def token(filename):
    return (REPO / filename).read_text().strip()


def auth(filename):
    return {"Authorization": f"Bearer {token(filename)}"}


def db_state(target_id=None):
    where = f"WHERE id='{target_id}'" if target_id else ""
    sql = (
        f"SELECT count(*) FROM targets WHERE is_deleted=false; "
        f"SELECT count(*) FROM targets {where}; "
        f"SELECT coalesce(string_agg(concat(id::text, '|', coalesce(department_id::text,''), '|', "
        f"target_value::text, '|', metric_type::text, '|', target_month, '|', status::text, '|', "
        f"is_deleted::text), ';' ORDER BY id::text), '') FROM targets {where};"
    )
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
            sql,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    lines = result.stdout.splitlines()
    return {
        "active_count": int(lines[0]),
        "matching_count": int(lines[1]),
        "rows": lines[2] if len(lines) > 2 else "",
    }


fixture_payload = {
    "name": "Department B Synthetic Target",
    "department_id": DEPT_B,
    "target_value": 100.0,
    "metric_type": "COUNT",
    "target_month": "2026-09",
}

setup = requests.post(
    f"{BASE}/api/v1/targets/",
    headers=auth(SUPER_ADMIN_TOKEN_FILE),
    json=fixture_payload,
)
setup.raise_for_status()
fixture = setup.json()
target_id = fixture["id"]

results = []

for role, token_file in roles:
    response = requests.get(f"{BASE}/api/v1/targets/", headers=auth(token_file))
    body = response.text[:1200]
    returned = response.json() if response.status_code == 200 else []
    appears = any(item.get("id") == target_id for item in returned)
    results.append(
        {
            "role": role,
            "method": "GET",
            "endpoint": "/api/v1/targets/",
            "body": None,
            "target": f"Department B target {target_id}",
            "status_code": response.status_code,
            "response": body,
            "returned_count": len(returned),
            "target_appears": appears,
        }
    )

for role, token_file in roles:
    response = requests.get(
        f"{BASE}/api/v1/targets/",
        headers=auth(token_file),
        params={"status": "ACTIVE", "skip": 0, "limit": 100},
    )
    returned = response.json() if response.status_code == 200 else []
    results.append(
        {
            "role": role,
            "method": "GET",
            "endpoint": "/api/v1/targets/?status=ACTIVE&skip=0&limit=100",
            "body": None,
            "target": f"Department B target {target_id}",
            "status_code": response.status_code,
            "response": response.text[:1200],
            "returned_count": len(returned),
            "target_appears": any(item.get("id") == target_id for item in returned),
        }
    )

for role, token_file in roles[1:]:
    before = db_state()
    payload = dict(fixture_payload)
    payload["name"] = f"Unauthorized {role} Target Attempt"
    response = requests.post(
        f"{BASE}/api/v1/targets/",
        headers=auth(token_file),
        json=payload,
    )
    after = db_state()
    results.append(
        {
            "role": role,
            "method": "POST",
            "endpoint": "/api/v1/targets/",
            "body": payload,
            "target": "Department B",
            "status_code": response.status_code,
            "response": response.text[:700],
            "before": before,
            "after": after,
            "state_unchanged": before == after,
        }
    )

sa_create = requests.post(
    f"{BASE}/api/v1/targets/",
    headers=auth(SUPER_ADMIN_TOKEN_FILE),
    json={
        **fixture_payload,
        "name": "Department B Synthetic Delete Target",
        "target_value": 101.0,
    },
)
sa_create.raise_for_status()
delete_target_id = sa_create.json()["id"]

for role, token_file in roles[1:]:
    before = db_state(delete_target_id)
    response = requests.delete(
        f"{BASE}/api/v1/targets/{delete_target_id}",
        headers=auth(token_file),
    )
    after = db_state(delete_target_id)
    results.append(
        {
            "role": role,
            "method": "DELETE",
            "endpoint": f"/api/v1/targets/{delete_target_id}",
            "body": None,
            "target": delete_target_id,
            "status_code": response.status_code,
            "response": response.text[:700],
            "before": before,
            "after": after,
            "state_unchanged": before == after,
        }
    )

sa_delete_before = db_state(delete_target_id)
sa_delete = requests.delete(
    f"{BASE}/api/v1/targets/{delete_target_id}",
    headers=auth(SUPER_ADMIN_TOKEN_FILE),
)
sa_delete_after = db_state(delete_target_id)
results.append(
    {
        "role": "Super Admin",
        "method": "DELETE",
        "endpoint": f"/api/v1/targets/{delete_target_id}",
        "body": None,
        "target": delete_target_id,
        "status_code": sa_delete.status_code,
        "response": sa_delete.text[:700],
        "before": sa_delete_before,
        "after": sa_delete_after,
        "authorized_soft_delete": (
            sa_delete.status_code == 204
            and sa_delete_before["matching_count"] == 1
            and sa_delete_after["matching_count"] == 1
            and "|t" in sa_delete_after["rows"]
        ),
    }
)

detail_results = []
for role, token_file in roles:
    response = requests.get(
        f"{BASE}/api/v1/targets/{target_id}",
        headers=auth(token_file),
    )
    detail_results.append(
        {
            "role": role,
            "method": "GET",
            "endpoint": f"/api/v1/targets/{target_id}",
            "status_code": response.status_code,
            "response": response.text[:500],
            "route_present": response.status_code != 404,
        }
    )

output = {
    "fixture": fixture,
    "fixture_id": target_id,
    "delete_fixture_id": delete_target_id,
    "results": results,
    "detail_results": detail_results,
    "final_fixture_state": db_state(target_id),
    "final_delete_fixture_state": db_state(delete_target_id),
}
(REPO / ".tmp_target_matrix_results.json").write_text(
    json.dumps(output, indent=2), encoding="utf-8"
)
print(json.dumps(output, indent=2))
