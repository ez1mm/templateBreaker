#!/usr/bin/python3

import copy
import os

#import pickle
import get_keys as g
#import random
#import sys
#import datetime
import click

#Async Stuff
import meraki
import asyncio
from meraki import aio
import tqdm.asyncio

from time import *

from deepdiff import DeepDiff

from bcolors import bcolors as bc

db = meraki.DashboardAPI(
            api_key=g.get_api_key(), 
            base_url='https://api.meraki.com/api/v1/', 
            output_log=True,
            log_file_prefix=os.path.basename(__file__)[:-3],
            log_path='Logs/',
            print_console=False)

orgs_whitelist = []
file_whitelist = 'org_whitelist.txt'
if os.path.exists(file_whitelist):
    f = open(file_whitelist)
    wl_orgs = f.readlines()
    for o in wl_orgs:
        if len(o.strip()) > 0:
            orgs_whitelist.append(o.strip())


### TOOLS SECTION

#finds all the something in a list of something else
def findName(list_of_things, target_name):
    res = []
    for o in list_of_things:
        if target_name in o['name']:
            res.append(o)
    return res

#return thing id=ID in list_of_things
def getID(list_of_things, id):
    for o in list_of_things:
        if 'id' in o and o['id'] == id:
            return o
        elif 'number' in o and o['number'] == id:
            return o
    return


def figureOutName(allnets, netID):
    for an in allnets:
        if netID == an['id']: 
            res = an['name'].replace('DG','')
            if '-' in res:
                res = res.split('-')[0].strip()
            print(f"Network[{netID}] returning name [{res}] raw[{an['name']}]")
            try:
                return str(int(res))
            except:
                return 0

#return the templateID of this network, otherwise return netID
def returnTemplateID(db, netID):
    net = db.networks.getNetwork(netID)
    if 'configTemplateId' in net:
        return net['configTemplateId']
    return netID

#returns all networks belonging to a specific template
def findTemplate(nets, templateID):
    result = []
    for n in nets:
        if "configTemplateId" in n and n['configTemplateId'] == templateID:
            result.append(n)
    return result

def countModels(stuff, target):
    result = 0
    for s in stuff:
        if target in s['model']:
            result += 1
    return result


def isSerial(s):
    if len(s) == 14 and s[4] == '-' and s[9] == '-':
        return True
    return False

def isProd(s):
    prod = [ 'MX', 'MS', 'MG', 'MR', 'MV']
    if s.upper() in prod: return True
    return False

def isNetID(s):
    if len(s) == 20 and s[1]== '_':
        return True
    return False

def getDevice(devs, target):
    result = []
    
    for d in devs:
        if isSerial(target):
            if d['serial'] == target:
                return d
        elif isProd(target):
            if target.upper() == d['model'][:2]:
                result.append(d)
        elif isNetID(target):
            if target == d['networkId']:
                result.append(d)
        elif target.lower() in d['name'].lower():
            result.append(d)
    return result

def fixGP(GP):
    if 'l3FirewallRules' in GP['firewallAndTrafficShaping']:
        for l3 in GP['firewallAndTrafficShaping']['l3FirewallRules']:
            l3['srcCidr'] = 'Any'
            l3['srcPort'] = 'Any'
    return GP

def findGPName(gpList, gpID):
    #print(type(gpID))
    for g in gpList:
        if 'groupPolicyId' in g and str(gpID) in g['groupPolicyId']:
            return g['name']
    print(f"Couldn't find GPID[{gpID}]")
    return

def findGPID(gpList, gpName):
    for g in gpList:
        if gpName in g['name']:
            return g['groupPolicyId']
    print(f"Couldn't find GPName[{gpName}]")
    return

#returns current networkID of device
def currentNet(db, serial):
    try:
        dev = db.devices.getDevice(serial)
        return dev['networkId']
    except:
        return

#find device and remove it from the network
def removeDevice(db, serial):
    try:
        dev = db.devices.getDevice(serial) #will error if it's not anywhere
        if len(dev) == 0:
            return    
        db.networks.removeNetworkDevices(dev['networkId'],serial)
        print(f"{bc.OKGREEN}Device [{bc.WARNING}{serial}{bc.OKGREEN}] has been removed from [{bc.WARNING}{dev['networkId']}{bc.OKGREEN}]{bc.ENDC}")
    except:
        print(f"{bc.FAIL}ERROR: {bc.OKGREEN}Couldn't remove device [{bc.WARNING}{serial}{bc.OKGREEN}]...probably not claimed?{bc.ENDC}")
    return
    
### /TOOLS SECTION

### ASYNC SECTION

async def getOrg_Networks(aio, org_id):
    result = await aio.organizations.getOrganizationNetworks(org_id,perPage=1000, total_pages='all')
    return org_id, "networks", result

async def getOrg_Devices(aio, org_id):
    result = await aio.organizations.getOrganizationDevices(org_id,perPage=1000, total_pages='all')
    return org_id, "devices", result

async def getOrg_Templates(aio, org_id):
    result = await aio.organizations.getOrganizationConfigTemplates(org_id)
    return org_id, "templates", result

async def getEverything():
    async with meraki.aio.AsyncDashboardAPI(
                api_key=g.get_api_key(),
                base_url="https://api.meraki.com/api/v1",
                output_log=True,
                log_file_prefix=os.path.basename(__file__)[:-3],
                log_path='Logs/',
                maximum_concurrent_requests=10,
                maximum_retries= 100,
                wait_on_rate_limit=True,
                print_console=False,
                
        ) as aio:
            orgs_raw = await aio.organizations.getOrganizations()
            orgs = {}
            for o in orgs_raw:
                if len(orgs_whitelist) == 0:
                    if o['api']['enabled']:
                        orgs[o['id']] = o
                elif o['id'] in orgs_whitelist:
                    orgs[o['id']] = o
            
            org_networks = {}
            org_devices = {}
            org_templates = {}
            getTasks = []
            for o in orgs:
                getTasks.append(getOrg_Networks(aio, o))
                getTasks.append(getOrg_Devices(aio, o))
                getTasks.append(getOrg_Templates(aio, o))

            for task in tqdm.tqdm(asyncio.as_completed(getTasks), total=len(getTasks), colour='green'):
                oid, action, result = await task
                if action == "devices":
                    org_devices[oid] = result
                elif action == "networks":
                    org_networks[oid] = result
                elif action == "templates":
                    org_templates[oid] = result

            
            print("DONE")
            return org_devices, org_networks, org_templates
    return
            

### /ASYNC SECTION   

@click.command()
@click.argument('source', default = '')
def rebind(source):
    network_obj = None
    org_id = ""
    try:
        network_obj = db.networks.getNetwork(source)
        org_id = network_obj['organizationId']
    except:
        print(f"{bc.FAIL}ERROR: {bc.OKGREEN}Can't query networkID[{bc.WARNING}{source}{bc.OKGREEN}] check your NetID and try again{bc.ENDC}")
        return

    print(f"{bc.OKGREEN}Preparing to rebind Network [{bc.WARNING}{network_obj['name']}{bc.OKGREEN}] in [{bc.WARNING}{org_id}{bc.OKGREEN}]{bc.ENDC}")    

    loop = asyncio.get_event_loop()
    start_time = time()
    org_devices, org_networks, org_templates = loop.run_until_complete(getEverything())
    end_time = time()
    elapsed_time = round(end_time-start_time,2)
    print(f"Loaded Everything took [{elapsed_time}] seconds")
    print()


    input(f"{bc.WARNING}WARNING:{bc.OKGREEN}About to rebind a network that was unBound.... PRESS ENTER TO CONTINUE{bc.ENDC}")
    print()

    keepList = ['productTypes', 'name', 'timeZone', 'tags', 'notes']
    newNET = {}
    for k in keepList:
        newNET[k] = network_obj[k]
    
    newNET['name'] += " UNBOUND"
    #theUnbound = findName(db.organizations.getOrganizationNetworks(org_id), newNET['name'])
    theUnbound = findName(org_networks[org_id], newNET['name'])
    
    if len(theUnbound) == 0:
        print(f"{bc.OKGREEN} No unbound networks found for [{bc.WARNING}{network_obj['name']}{bc.OKGREEN}]{bc.ENDC}")
        return
    
    UB_net = theUnbound[0] #assume the one and only one in list
    UB_devices = db.networks.getNetworkDevices(UB_net['id'])
    appliances = ['MX', 'Z3']
    serials = []
    for dev in UB_devices:
        if dev['model'] in appliances:
            removeDevice(db,dev['serial'])
            serials.append(dev['serial'])
    db.networks.claimNetworkDevices(source,serials=serials)

    UB_devices = db.networks.getNetworkDevices(UB_net['id'])
    if len(UB_devices) == 0:
        db.networks.deleteNetwork(UB_net['id'])
        print(f"{bc.WARNING}SUCCESS{bc.OKGREEN} - UnBound Network has been deleted and hardware returned to the original network{bc.ENDC}")
    else:
        print(f"{bc.FAIL}ERROR: {bc.OKGREEN}Network [{bc.WARNING}{UB_net['name']}{bc.OKGREEN}] isn't empty..... not deleting{bc.ENDC}")
    
    return

    


if __name__ == '__main__':    
    rebind()