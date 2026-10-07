import os, requests, json
repo = r'C:\Users\Aung Thu Zaw\Desktop\Project\Kpi_management_system'
formula_id = 'eb7ff69f-a379-49f0-847c-690e823c33ad'
roles = {
    'Super Admin': os.path.join(repo,'.tmp_sa_token'),
    'Admin A': os.path.join(repo,'.tmp_admin_a_token'),
    'L0 A': os.path.join(repo,'.tmp_token_l0_a_at_example_com'),
    'L1 A': os.path.join(repo,'.tmp_token_l1_a_at_example_com'),
    'L2 A': os.path.join(repo,'.tmp_token_l2_a_at_example_com'),
    'DeptB User': os.path.join(repo,'.tmp_token_deptb_user_at_example_com')
}
results = []
# POST attempt payload to create in Department B
post_payload = {
  "name":"DeptB Formula - Create Attempt",
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
# PUT payload to update description
put_payload = {"description":"Updated by actor during test"}

for role, path in roles.items():
    if not os.path.exists(path):
        results.append({'role':role,'note':'token missing'})
        continue
    token = open(path).read().strip()
    headers = {'Authorization':f'Bearer {token}','Content-Type':'application/json'}
    # POST
    r_post = requests.post('http://127.0.0.1:8001/api/v1/kpi/formulas', json=post_payload, headers=headers)
    results.append({'role':role,'method':'POST','url':'/api/v1/kpi/formulas','status_code':r_post.status_code,'summary':r_post.text[:400]})
    # PUT
    r_put = requests.put(f'http://127.0.0.1:8001/api/v1/kpi/formulas/{formula_id}', json=put_payload, headers=headers)
    results.append({'role':role,'method':'PUT','url':f'/api/v1/kpi/formulas/{formula_id}','status_code':r_put.status_code,'summary':r_put.text[:400]})
    # DELETE
    r_del = requests.delete(f'http://127.0.0.1:8001/api/v1/kpi/formulas/{formula_id}', headers=headers)
    results.append({'role':role,'method':'DELETE','url':f'/api/v1/kpi/formulas/{formula_id}','status_code':r_del.status_code,'summary':r_del.text[:400]})

with open(os.path.join(repo,'.tmp_formula_crud_results.json'),'w') as f:
    json.dump(results,f,indent=2)
print('DONE')
