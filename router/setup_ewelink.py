#!/usr/bin/env python3
"""User-run setup: secrets travel in SSH stdin, never argv, source or chat."""
import getpass
import json
from pathlib import Path
import shlex
import subprocess
import sys
import webbrowser

SSH=['ssh','-T','-i',str(Path.home()/'.ssh/clockmill_router_dc'),'-o','ConnectTimeout=8','root@192.168.86.39']
ROOT='/usr/local/usr/lib/modbus-monitor'
APPID='1iOPckIsR1BXHdiMChLsab6UzD5uOd1S'
TARGETS=[('inverter-1000','1000 watt inverter'),('garage-inverter','Garage inverter'),('inverter-600','600 watt inverter'),('charger-inverter','Charger inverter'),('fridge','Fridge')]

def remote(source,payload=None):
    script="import sys,json;sys.path.insert(0,"+repr(ROOT)+");"+source
    command='/usr/local/usr/bin/python3 -c '+shlex.quote(script)
    result=subprocess.run(SSH+[command],input=json.dumps(payload) if payload is not None else '',text=True,capture_output=True)
    if result.returncode:
        # Never echo tracebacks/remote state containing a credential or auth URL.
        raise RuntimeError('Router setup failed. Existing credentials and history were preserved. Ask Gareth to check the integration.')
    return result.stdout.strip()

def main():
    print("Dad's Modbus eWeLink setup")
    print('Copy APP SECRET from Hubless Hub for Dad in the eWeLink developer console.')
    print('The key is hidden while typing and sent only to the router over SSH.')
    secret=getpass.getpass('App Secret (blank keeps an installed key): ').strip()
    remote('''from pathlib import Path;from ewelink import atomic_json,private_json,CALLBACK;from datetime import datetime
p=Path('/etc/modbus-ewelink.json');incoming=json.load(sys.stdin)
try:config=private_json(p)
except FileNotFoundError:config={}
assert not config.get('appid') or config['appid']==incoming['appid'],'Unexpected existing app'
if incoming['secret']:config['secret']=incoming['secret']
assert config.get('secret'),'Missing secret'
config.update(appid=incoming['appid'],redirect_url=CALLBACK,app_expires_at=datetime.fromisoformat('2027-10-26T09:49:48.140+00:00').timestamp())
if p.exists():atomic_json(str(p)+'.setup-backup',private_json(p))
atomic_json(p,config)
''',dict(appid=APPID,secret=secret))
    secret=None
    url=remote("from ewelink import Ewelink;print(Ewelink(None,None).authorize())")
    if not url.startswith('https://c2ccdn.coolkit.cc/oauth/index.html?'):raise RuntimeError('Unexpected authorisation destination')
    print("A browser will open. Sign in with the eWeLink account that owns Dad's devices and review its access request.")
    if not webbrowser.open(url):
        print('Open this temporary link in your browser:');print(url)
    input('After eWeLink returns to the Modbus hub, press Return here: ')
    inventory=json.loads(remote("from ewelink import Ewelink;import modbus_usb;print(json.dumps(Ewelink(modbus_usb.storage_directory,modbus_usb.check).inventory()))".replace('import modbus_usb;',"sys.path.insert(0,'/usr/local/usr/lib/clockmill');import modbus_usb;")))
    if len(inventory)<5:raise RuntimeError('Fewer than five supported devices were returned. Check the account and device permissions before selecting them.')
    print('\nChoose the matching eWeLink device for each card:')
    for index,item in enumerate(inventory,1):print(str(index)+'. '+str(item.get('name','Unnamed device')))
    mapping={};used=set()
    for slug,name in TARGETS:
        while True:
            selection=input(name+' — enter its device number: ').strip()
            if selection.isdecimal() and 1<=int(selection)<=len(inventory) and int(selection) not in used:break
            print('Choose an unused device number from the list.')
        item=inventory[int(selection)-1];used.add(int(selection))
        mapping[slug]={'id':item['id'],'item_type':item['item_type'],'uiid':item.get('uiid')}
    remote("from ewelink import private_json,atomic_json;p='/etc/modbus-ewelink.json';c=private_json(p);c['devices']=json.load(sys.stdin);atomic_json(p,c)",mapping)
    remote("sys.path.insert(0,'/usr/local/usr/lib/clockmill');import modbus_usb;from ewelink import Ewelink;a=Ewelink(modbus_usb.storage_directory,modbus_usb.check);a.collect()")
    print('Device selection saved. The first readings are stored. Open https://modbus.clockmillbarn.uk/#ewelink')
    print('Unavailable measurements stay blank. No device-control commands are sent.')

if __name__=='__main__':
    try:main()
    except (RuntimeError,ValueError,KeyboardInterrupt) as error:
        print('\nSetup stopped: '+str(error),file=sys.stderr);sys.exit(1)
