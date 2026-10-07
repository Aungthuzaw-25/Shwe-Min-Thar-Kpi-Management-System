import requests, os
repo = r'C:\Users\Aung Thu Zaw\Desktop\Project\Kpi_management_system'
login = {'email':'admin.a@example.com','password':'TempSeedPass!2026'}
r = requests.post('http://127.0.0.1:8001/api/v1/auth/login', json=login)
print(r.status_code)
print(r.text)
if r.status_code==200:
    token = r.json().get('access_token')
    with open(os.path.join(repo,'.tmp_admin_a_token'),'w') as f:
        f.write(token)
    print('Saved token')
