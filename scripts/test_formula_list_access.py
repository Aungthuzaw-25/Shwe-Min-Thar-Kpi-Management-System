import json
import os

import requests

__test__ = False

repo = r'C:\Users\Aung Thu Zaw\Desktop\Project\Kpi_management_system'
base_url = os.getenv('QA_BASE_URL', 'http://127.0.0.1:18000').rstrip('/')
formula_id = '4164f8db-6e3f-4fbc-88fc-d5449cda4688'
roles = {
    'Super Admin': 'QA_TOKEN_SUPERADMIN',
    'Admin': 'QA_TOKEN_ADMIN',
    'L0': 'QA_TOKEN_L0',
    'L1': 'QA_TOKEN_L1',
    'L2': 'QA_TOKEN_L2',
}
results = []
for role, token_env in roles.items():
    token = os.getenv(token_env, '').strip()
    if not token:
        results.append({'role': role, 'status': 'NOT AVAILABLE', 'note': 'token missing'})
        continue
    headers = {'Authorization': f'Bearer {token}'}
    r = requests.get(f'{base_url}/api/v1/kpi/formulas', headers=headers, timeout=20)
    found = False
    try:
        data = r.json()
        for f in data:
            if f.get('id') == formula_id:
                found = True
                break
    except Exception:
        data = r.text
    results.append({
        'role': role,
        'method': 'GET',
        'url': '/api/v1/kpi/formulas',
        'status_code': r.status_code,
        'found': found,
        'summary': str(data)[:400],
    })

with open(os.path.join(repo, '.tmp_formula_list_access_results.json'), 'w', encoding='utf-8') as f:
    json.dump(results, f, indent=2)
print(f'DONE base_url={base_url} roles_checked={len(results)}')
