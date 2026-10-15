from pathlib import Path

import requests


BASE = "http://127.0.0.1:8001"
REPO = Path(r"C:\Users\Aung Thu Zaw\Desktop\Project\Kpi_management_system")
QA_FIXTURE_PASSWORD = "TempPass!2026"
accounts = [
    ("sa.local@kpi.test", "TempSeedPass!2026", ".tmp_sa_token"),
    ("admin.a@example.com", "TempSeedPass!2026", ".tmp_admin_a_token"),
    ("l0.a@example.com", QA_FIXTURE_PASSWORD, ".tmp_token_l0_a_at_example_com"),
    ("l1.a@example.com", QA_FIXTURE_PASSWORD, ".tmp_token_l1_a_at_example_com"),
    ("l2.a@example.com", QA_FIXTURE_PASSWORD, ".tmp_token_l2_a_at_example_com"),
    ("deptb.user@example.com", QA_FIXTURE_PASSWORD, ".tmp_token_deptb_user_at_example_com"),
]

for email, password, token_file in accounts:
    response = requests.post(
        f"{BASE}/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    response.raise_for_status()
    (REPO / token_file).write_text(response.json()["access_token"], encoding="utf-8")
    print(f"refreshed {email}")
