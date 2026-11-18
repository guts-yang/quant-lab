.\.venv\Scripts\python.exe -c "
import pandas as pd
df = pd.read_parquet('data/cache/stock_basic.parquet')
codes = '''
002463.SZ 002916.SZ 001389.SZ 002384.SZ 600183.SH 603186.SH 603256.SH 002475.SZ
002837.SZ 603912.SH 002364.SZ 002335.SZ 002518.SZ 002851.SZ 002639.SZ
601869.SH 600487.SH 600522.SH
003031.SZ
002156.SZ 600584.SH 002185.SZ 002436.SZ
002371.SZ 600641.SH 603690.SH 603061.SH 002409.SZ 603650.SH 603078.SH 605358.SH 603986.SH
601138.SH 000977.SZ 603019.SH 603296.SH 000938.SZ 000628.SZ 000034.SZ
002281.SZ 000988.SZ 603083.SH 600498.SH
000063.SZ 002396.SZ 603118.SH
603881.SH 600845.SH 600602.SH 603887.SH
002261.SZ
002230.SZ 002415.SZ 002236.SZ 002970.SZ 002920.SZ 600570.SH
'''.split()
print('N', len(codes), 'UNIQUE', len(set(codes)))
main_ok = {'600','601','603','605','000','001','002','003'}
bad=[]
for c in codes:
    row = df[df.ts_code==c]
    if row.empty:
        bad.append(c+' MISSING')
        continue
    r = row.iloc[0]
    pref = c[:3]
    ok = r.list_status=='L' and pref in main_ok and c.endswith(('.SH','.SZ'))
    flag = 'OK' if ok else 'BAD'
    if not ok: bad.append(c)
    print(f'{flag}\t{r.ts_code}\t{r.name}\t{r.list_status}\t{r.industry}')
print('BADS', bad)
"
