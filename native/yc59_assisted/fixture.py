"""Isolated synthetic-only diagnostic. No credentials, YC modules or network."""
import getpass
import json
import os
from pathlib import Path
import sys
import uuid

report=Path(sys.argv[1])
def publish(name,value):
    target=report/name
    temp=target.with_name(target.name+'.'+uuid.uuid4().hex+'.tmp')
    with open(temp,'x',encoding='utf-8') as f:
        json.dump(value,f,sort_keys=True,ensure_ascii=True);f.flush();os.fsync(f.fileno())
    os.rename(temp,target)

print('YC59_CONSOLE_DIAGNOSTIC_'+report.name,flush=True)
publish('fixture-start.json',{'stdin_tty':sys.stdin.isatty(),'stdout_tty':sys.stdout.isatty(),'stderr_tty':sys.stderr.isatty(),'python_version':sys.version.split()[0],'synthetic_only':True})
print('Автоматическая проверка вымышленных данных. Ничего не вводите.',flush=True)
expected=['SYNTHETIC-CAPITALS-314-AbZ','b1g-CLOUD-01','b1g-FOLDER-02','fv4-VM-03','fv4-DISK-04','fd8-SNAPSHOT-05']
labels=['token','cloud','folder','vm','disk','snapshot']
value=getpass.getpass('IAM-токен владельца (ввод скрыт): ')
good=value==expected[0]
publish('fixture-value-0.json',{'field':'token','exact':good,'length':len(value),'synthetic_only':True})
del value
for n,label in enumerate(['Облако','Каталог','ВМ','Диск','Снимок'],1):
    value=input(label+' — ID: ')
    exact=value==expected[n];good=good and exact
    publish('fixture-value-'+str(n)+'.json',{'field':labels[n],'exact':exact,'length':len(value),'synthetic_only':True})
    del value
publish('fixture-result.json',{'synthetic_only':True,'all_values_exact':good,'cloud_invocations':0})
print('SHA-256 запроса: '+'a'*64)
print('Для согласия введите «РАЗРЕШИТЬ ОДИН РАЗ 12345678-1234-1234-1234-123456789abc». Любой другой ответ — отказ: ')
print('YC59_FIXTURE_OK' if good else 'YC59_FIXTURE_FAILED',flush=True)
raise SystemExit(0 if good else 2)
