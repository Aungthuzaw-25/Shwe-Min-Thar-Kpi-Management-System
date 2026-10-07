import os, requests, json
repo = r'C:\Users\Aung Thu Zaw\Desktop\Project\Kpi_management_system'
with open(os.path.join(repo,'.tmp_formula_v2_id')) as f: formula_id = f.read().strip()
roles = [
    ('Admin A', os.path.join(repo,'.tmp_admin_a_token')),
    ('L0 A', os.path.join(repo,'.tmp_token_l0_a_at_example_com')),
    ('L1 A', os.path.join(repo,'.tmp_token_l1_a_at_example_com')),
    ('L2 A', os.path.join(repo,'.tmp_token_l2_a_at_example_com')),
    ('DeptB User', os.path.join(repo,'.tmp_token_deptb_user_at_example_com')),
    ('Super Admin', os.path.join(repo,'.tmp_sa_token'))
]
results=[]
post_payload = {
  "name":"DeptB Formula - Create Attempt 2",
  "description":"Created during auth matrix test",
  "region_town":"HQ",
  "department_id":"5a25d02d-c4a2-4606-962f-31c25c87cc4e",
  "position_name":"QA Tester",
  "user_level":"L1",
  "categories":[
    {"name":"Quality","description":"Quality","weight":0.9,"max_score":5.0,"evaluation_type":"TARGET","deductions":[]},
    {"name":"အချိန်မှီမတင်ပြခြင်း","description":"Timeliness","weight":0.1,"max_score":5.0,"evaluation_type":"DEDUCTION","deductions":[{"name":"Late","measure_type":"count","rules":[{"operator":">=","value":1,"deduct_score":1.0}]}]}
  ]
}
put_payload = {"description":"Updated by actor during test v2"}
for role,path in roles:
    if not os.path.exists(path):
        results.append({'role':role,'note':'token missing'})
        continue
    token = open(path).read().strip()
    headers={'Authorization':f'Bearer {token}','Content-Type':'application/json'}
    r_post = requests.post('http://127.0.0.1:8001/api/v1/kpi/formulas', json=post_payload, headers=headers)
    results.append({'role':role,'method':'POST','status_code':r_post.status_code,'summary':r_post.text[:400]})
    r_put = requests.put(f'http://127.0.0.1:8001/api/v1/kpi/formulas/{formula_id}', json=put_payload, headers=headers)
    results.append({'role':role,'method':'PUT','status_code':r_put.status_code,'summary':r_put.text[:400]})

# Finally attempt DELETE as Super Admin only
sa_path = os.path.join(repo,'.tmp_sa_token')
if os.path.exists(sa_path):
    token = open(sa_path).read().strip()
    headers={'Authorization':f'Bearer {token}'}
    r_del = requests.delete(f'http://127.0.0.1:8001/api/v1/kpi/formulas/{formula_id}', headers=headers)
    results.append({'role':'Super Admin','method':'DELETE','status_code':r_del.status_code,'summary':r_del.text[:400]})

with open(os.path.join(repo,'.tmp_formula_crud_ordered_results.json'),'w') as f:
    json.dump(results,f,indent=2)
print('DONE')
