import requests, os
repo = r'C:\Users\Aung Thu Zaw\Desktop\Project\Kpi_management_system'
sa_token = open(os.path.join(repo,'.tmp_sa_token')).read().strip()
headers={'Authorization':f'Bearer {sa_token}'}
payload={
  "name":"Dept B Formula - IT v2",
  "description":"Dept B test formula v2",
  "region_town":"Head Office",
  "department_id":"5a25d02d-c4a2-4606-962f-31c25c87cc4e",
  "position_name":"QA Engineer",
  "user_level":"L1",
  "categories":[
    {"name":"Quality","description":"Quality metrics","weight":0.9,"max_score":5.0,"evaluation_type":"TARGET","deductions":[]},
    {"name":"အချိန်မှီမတင်ပြခြင်း","description":"Timeliness","weight":0.1,"max_score":5.0,"evaluation_type":"DEDUCTION","deductions":[{"name":"Late submission","measure_type":"count","rules":[{"operator":">=","value":1,"deduct_score":2.0}]}]}
  ]
}
r = requests.post('http://127.0.0.1:8001/api/v1/kpi/formulas', json=payload, headers=headers)
print(r.status_code)
print(r.text)
if r.status_code==201:
    data=r.json()
    with open(os.path.join(repo,'.tmp_formula_v2_id'),'w') as f:
        f.write(data.get('id'))
    print('Saved id')
