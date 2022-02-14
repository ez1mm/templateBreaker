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
        print(f"Device {serial} has been removed from {dev['networkId']}")
    except:
        print(f"Couldn't remove device {serial}...probably not claimed?")
    return
    
#clone/copy/move the device betwnee source and target
def copySettings(db, sourceNet, targetNet):
    src_groupPolicies = db.networks.getNetworkGroupPolicies(sourceNet)
    dst_groupPolicies = []
    for gp in src_groupPolicies:
        if 'groupPolicyId' in gp: gp.pop('groupPolicyId')
        fixGP(gp)
        try:
            dst_groupPolicies.append(db.networks.createNetworkGroupPolicy(targetNet, **gp))
        except:
            pass
    
    src_groupPolicies = db.networks.getNetworkGroupPolicies(sourceNet)
    dst_groupPolicies = db.networks.getNetworkGroupPolicies(targetNet)


    db.appliance.updateNetworkApplianceVlansSettings(targetNet, vlansEnabled = True)
    src_vlans = db.appliance.getNetworkApplianceVlans(sourceNet)
    dst_GPMAP = {}
    for v in src_vlans:
        v.pop('networkId')
        if 'groupPolicyId' in v:
            oldName = findGPName(src_groupPolicies,v['groupPolicyId'])
            newID = findGPID(dst_groupPolicies, oldName)
            v['groupPolicyId'] = newID
            dst_GPMAP[str(v['id'])] = copy.deepcopy(newID)
        try:
            result = db.appliance.createNetworkApplianceVlan(targetNet, **v)
            #print(result)
        except:
            print(f"Can't create VLAN")
            print(v)
        
    for v in src_vlans:
        vlan = v['id']
        keepers = ['fixedIpAssignments', 'reservedIpRanges', 'dhcpHandling', 'dhcpLeaseTime', 'dnsNameservers', 'dhcpOptions', 'dhcpBootOptionsEnabled', 'vlanId']
        if not 'vlanId' in v: v['vlanId'] = vlan
        newVlan = {}
        for tmp in v:
            if tmp in keepers:
                newVlan[tmp] = v[tmp]
        if newVlan['vlanId'] in dst_GPMAP:
            newVlan['groupPolicyId'] = dst_GPMAP[newVlan['vlanId'] ]
        #print(newVlan)
        res = db.appliance.updateNetworkApplianceVlan(targetNet, **newVlan)
        #print(res)

    #remove the default vlan1 if the source doesn't have it
    if getID(src_vlans, 1) == None:
        target_vlans = db.appliance.getNetworkApplianceVlans(targetNet)
        if not getID(target_vlans,1) == None:
            db.appliance.deleteNetworkApplianceVlan(targetNet, 1)


    src_syslog = db.networks.getNetworkSyslogServers(sourceNet)
    db.networks.updateNetworkSyslogServers(targetNet,**src_syslog)
    l3fw = db.appliance.getNetworkApplianceFirewallL3FirewallRules(sourceNet)
    l7fw = db.appliance.getNetworkApplianceFirewallL7FirewallRules(sourceNet)
    db.appliance.updateNetworkApplianceFirewallL3FirewallRules(targetNet, **l3fw)
    db.appliance.updateNetworkApplianceFirewallL7FirewallRules(targetNet, **l7fw)

    network_obj = db.networks.getNetwork(sourceNet)
    if 'configTemplateId' in network_obj:
        tsrules = db.appliance.getNetworkApplianceTrafficShapingRules(network_obj['configTemplateId']) #THIS PULLS FROM TEMPLATE
    else:
        tsrules = db.appliance.getNetworkApplianceTrafficShapingRules(sourceNet) #NOT a network unbind, but a regular network, oh well keep going
    db.appliance.updateNetworkApplianceTrafficShapingRules(targetNet, **tsrules)

    site2siteVPN = db.appliance.getNetworkApplianceVpnSiteToSiteVpn(sourceNet)
    try:
        db.appliance.updateNetworkApplianceVpnSiteToSiteVpn(targetNet, **site2siteVPN)
    except:
        print(f"Error trying to write the site2site rules, make sure your HUB/Spoke configuration is correct and try again")

    #Make sure firmware matches
    FW_source = db.networks.getNetworkFirmwareUpgrades(sourceNet)['products']['appliance']['currentVersion']['id']
    FW_target = db.networks.getNetworkFirmwareUpgrades(targetNet)['products']['appliance']['currentVersion']['id']
    if FW_target != FW_source:
        print(f"{bc.FAIL}WARNING: {bc.OKGREEN}Firmware of source[{bc.WARNING}{FW_source}{bc.OKGREEN}] doesn't match target[{bc.WARNING}{FW_target}{bc.OKGREEN}].... fixing that....{bc.ENDC}")
        products={'appliance': {'nextUpgrade': {'toVersion': {'id': FW_source}}}}
        db.networks.updateNetworkFirmwareUpgrades(targetNet, products=products)

    #print(f"Ready to move the hardware? (YES to continue)")
    #if not input('>') == "YES":
    #    sys.exit()

    ### MOVE THE HARDWARE
    sourceNet_devs = db.networks.getNetworkDevices(sourceNet)
    sourceMX = ""
    validProducts = [ "MX", "Z3"]
    for sd in sourceNet_devs:
        if sd['model'][:2] in validProducts:
            sourceMX = sd['serial']
    
    #LOOP until firmware in target network matches source... so the MX won't re-download FW and reboot
    while not FW_target == FW_source:
        FW_raw = db.networks.getNetworkFirmwareUpgrades(targetNet)
        FW_target = FW_raw['products']['appliance']['currentVersion']['id']
        print(f"{bc.FAIL}WARNING: {bc.OKGREEN}Waiting for firmware upgrade to finish...currently running fwID[{bc.WARNING}{FW_target}{bc.OKGREEN}] instead of fwID[{bc.WARNING}{FW_source}{bc.OKGREEN}]{bc.ENDC}")
        sleep(30)
    

    #You need to query the templateID, not the sourceNetwork.
    tempNetID = returnTemplateID(db, sourceNet) 
    ports = db.appliance.getNetworkAppliancePorts(tempNetID)
    removed = False
    retries = 0
    while not removed:
        if retries > 5: 
            print(f"{bc.FAIL}ERROR: {bc.OKGREEN}Could not move device {bc.WARNING}{sourceMX}{bc.ENDC}")
            sys.exit()
        try:
            print(f"{bc.OKGREEN} -Attempting moving of {bc.WARNING}{sourceMX}{bc.OKGREEN} from {bc.WARNING}{sourceNet}{bc.ENDC}")
            db.networks.removeNetworkDevices(sourceNet, sourceMX)
            removed = True
        except:
            print(f"{bc.FAIL}ERROR: {bc.OKGREEN}Failed to remove...trying again{bc.ENDC}")
            retries += 1
    
    #RECLAIM HARDWARE
    db.networks.claimNetworkDevices(targetNet, serials=[sourceMX])

    #COPY PORTS OVER
    targetNet_ports = db.appliance.getNetworkAppliancePorts(targetNet)
    if not getID(targetNet_ports, 1) == None: #corner case where template/source doesn't have port1, automatically disable. If it exists and isn't disabled, actual value will be configured
        db.appliance.updateNetworkAppliancePort(targetNet, portId = 1, enabled = False )
    for p in ports:
        p['portId'] = p['number']
        try:
            if not p['enabled']:
                db.appliance.updateNetworkAppliancePort(targetNet, portId = p['portId'], enabled = False )
            else:
                db.appliance.updateNetworkAppliancePort(targetNet, **p)
        except:
            break
    

    #fwServices = db.appliance.getNetworkApplianceFirewallFirewalledServices(sourceNet)
    #db.appliance.updateNetworkApplianceFirewall

    #routes = db.appliance.getNetworkApplianceStaticRoutes(templateid)
    #db.appliance.update
    return #done with copySettings

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
def unbind(source):
    network_obj = None
    org_id = ""
    try:
        network_obj = db.networks.getNetwork(source)
        org_id = network_obj['organizationId']
    except:
        print(f"{bc.FAIL}ERROR: {bc.OKGREEN}Can't query networkID[{bc.WARNING}{source}{bc.OKGREEN}] check your NetID and try again{bc.ENDC}")
        return

    print(f"{bc.OKGREEN}Preparing to unbind Network [{bc.WARNING}{network_obj['name']}{bc.OKGREEN}] in [{bc.WARNING}{org_id}{bc.OKGREEN}]{bc.ENDC}")    

    loop = asyncio.get_event_loop()
    start_time = time()
    org_devices, org_networks, org_templates = loop.run_until_complete(getEverything())
    end_time = time()
    elapsed_time = round(end_time-start_time,2)
    print(f"Loaded Everything took [{elapsed_time}] seconds")
    print()
    input(f"{bc.WARNING}WARNING:{bc.OKGREEN}About to unbind a network from a template.... PRESS ENTER TO CONTINUE{bc.ENDC}")
    print()

    keepList = ['productTypes', 'name', 'timeZone', 'tags', 'notes']
    newNET = {}
    for k in keepList:
        newNET[k] = network_obj[k]
    newNET['name'] += " UNBOUND"
    newNET['tags'].append("UNBOUND")
    theUnbound = findName(db.organizations.getOrganizationNetworks(org_id), newNET['name'])
    if len(theUnbound) > 0:
        print(f"{bc.FAIL}ERROR: {bc.OKGREEN} Network Name [{bc.WARNING} {newNET['name']} {bc.OKGREEN}] already unbound? {bc.ENDC}")
        return
    newNet_result = None
    target_netid = None
    try:
        newNet_result = db.organizations.createOrganizationNetwork(org_id, **newNET)
        print(f"{bc.OKGREEN}Created new Network [{bc.WARNING} {newNET['name']} {bc.OKGREEN}] netID[{bc.WARNING} {newNet_result['id']} {bc.OKGREEN}] {bc.ENDC}")
        target_netid = newNet_result['id']
        #print(newNet_result)
    except meraki.APIError as e:
        print(e)
        print(f"{bc.FAIL}Failed to create a new network....{bc.ENDC}")

    start_time = time()
    copySettings(db, source, target_netid)
    end_time = time()
    elapsed_time = round(end_time - start_time,2)
    
    print()
    print(f"{bc.WARNING}SUCCESS -{bc.OKGREEN} network unbound successfully in [{bc.WARNING}{elapsed_time}{bc.OKGREEN}]seconds... you can see the network now...{bc.ENDC}")
    print()

    #change the URL to go directly to the hardware page
    statusURL = newNet_result['url'].replace('usage/list','nodes/new_wired_status/summary?timespan=86400')
    #click.launch(statusURL)
    print(statusURL)
    return

    


if __name__ == '__main__':    
    unbind()
