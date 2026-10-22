import requests, os, time

QA_FIXTURE_PASSWORD = ""
repo = r'C:\Users\Aung Thu Zaw\Desktop\Project\Kpi_management_system'
sa_token = open(os.path.join(repo,'.tmp_sa_token')).read().strip()
headers = {'Authorization':f'Bearer {sa_token}'}
users = [
    {"first_name":"L0","last_name":"DeptA","email":"l0.a@example.com","password":QA_FIXTURE_PASSWORD,"role_id":"d1d27e99-ada5-4e75-ac45-ad3cc9de3836","department_id":"29c02451-c65d-47f9-8f77-177217ce2d05","user_level":"L0"},
    {"first_name":"L1","last_name":"DeptA","email":"l1.a@example.com","password":QA_FIXTURE_PASSWORD,"role_id":"d1d27e99-ada5-4e75-ac45-ad3cc9de3836","department_id":"29c02451-c65d-47f9-8f77-177217ce2d05","user_level":"L1"},
    {"first_name":"L2","last_name":"DeptA","email":"l2.a@example.com","password":QA_FIXTURE_PASSWORD,"role_id":"d1d27e99-ada5-4e75-ac45-ad3cc9de3836","department_id":"29c02451-c65d-47f9-8f77-177217ce2d05","user_level":"L2"},
    {"first_name":"DeptB","last_name":"User","email":"deptb.user@example.com","password":QA_FIXTURE_PASSWORD,"role_id":"d1d27e99-ada5-4e75-ac45-ad3cc9de3836","department_id":"5a25d02d-c4a2-4606-962f-31c25c87cc4e","user_level":"L1"}
]
created = []
for u in users:
    payload = {
        "first_name":u['first_name'],"last_name":u['last_name'],"email":u['email'],"password":u['password'],"position_id":None,"region_town":"HQ","role_id":u['role_id'],"is_active":True,"department_id":u['department_id'],"user_level":u['user_level']
    }
    r = requests.post('http://127.0.0.1:8001/api/v1/users', json=payload, headers=headers)
    print('CREATE',u['email'],r.status_code)
    try:
        print(r.json())
    except Exception:
        print(r.text)
    created.append((u['email'],u['password']))
    time.sleep(0.5)

# login and save tokens
for email,pwd in created:
    r = requests.post('http://127.0.0.1:8001/api/v1/auth/login', json={'email':email,'password':pwd})
    print('LOGIN',email,r.status_code)
    if r.status_code==200:
        token = r.json().get('access_token')
        fname = email.replace('@','_at_').replace('.','_')
        with open(os.path.join(repo,f'.tmp_token_{fname}'),'w') as f:
            f.write(token)
        print('Saved token to',f'.tmp_token_{fname}')
    else:
        print('Login failed',r.text)
