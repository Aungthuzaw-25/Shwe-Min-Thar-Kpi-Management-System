import json, os, sys
import requests
repo = r'C:\Users\Aung Thu Zaw\Desktop\Project\Kpi_management_system'
token_path = os.path.join(repo, '.tmp_sa_token')
with open(token_path,'r') as f:
    token = f.read().strip()
admin_payload = {
  "first_name":"Admin",
  "last_name":"DeptA",
  "email":"admin.a@kpi.test",
  "password":"",
  "position_id": None,
  "region_town":"HQ",
  "role_id":"e1b10294-3e9b-4c5d-90d0-29ce03457b16",
  "is_active":True,
  "department_id":"29c02451-c65d-47f9-8f77-177217ce2d05",
  "user_level":"L2"
}

r = requests.post('http://127.0.0.1:8001/api/v1/users', json=admin_payload, headers={'Authorization':f'Bearer {token}'})
print(r.status_code)
print(r.text)
