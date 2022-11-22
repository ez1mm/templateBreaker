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

import random

from time import *
import batch_helper

#from deepdiff import DeepDiff

from bcolors import bcolors as bc

isExist = os.path.exists('Logs')
if not isExist:
    os.makedirs('Logs')
    

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


templates = {}
for o in orgs_whitelist:
    templates[o] = db.organizations.getOrganizationConfigTemplates(o)



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

def getPortId(portList, pid):
    if not type(pid) == str:
        pid = str(pid)
    for p in portList:
        if 'portId' in p:
            if p['portId'] == pid:
                return p
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

def cloneTemplate(db, source_oid, sourceTemplate, target_oid):
    raw = db.organizations.getOrganizationConfigTemplate(source_oid, sourceTemplate)
    raw.pop('id')
    raw['name'] = raw['name'] + " CLONED"
    print(raw)
    #newTemplate = None
    #try:
    newTemplate = db.organizations.createOrganizationConfigTemplate(target_oid,**raw)

    #except:
    #    print(f"{bc.FAIL} FAILED: Creating new Template.....{bc.ENDC}")
    #    return False
    
    newTemplateID = newTemplate['id']

    #GROUP POLICIES
    src_groupPolicies = db.networks.getNetworkGroupPolicies(sourceTemplate)
    dst_groupPolicies = []
    for gp in src_groupPolicies:
        if 'groupPolicyId' in gp: gp.pop('groupPolicyId')
        fixGP(gp)
        try:
            dst_groupPolicies.append(db.networks.createNetworkGroupPolicy(newTemplateID, **gp))
        except:
            pass
    src_groupPolicies = db.networks.getNetworkGroupPolicies(sourceTemplate)
    dst_groupPolicies = db.networks.getNetworkGroupPolicies(newTemplateID)

    #VLANS
    db.appliance.updateNetworkApplianceVlansSettings(newTemplateID, vlansEnabled = True)
    src_vlans = db.appliance.getNetworkApplianceVlans(sourceTemplate)
    dst_GPMAP = {}
    for v in src_vlans:
        v.pop('networkId')
        if 'groupPolicyId' in v:
            oldName = findGPName(src_groupPolicies,v['groupPolicyId'])
            newID = findGPID(dst_groupPolicies, oldName)
            v['groupPolicyId'] = newID
            dst_GPMAP[str(v['id'])] = copy.deepcopy(newID)
        try:
            result = db.appliance.createNetworkApplianceVlan(newTemplateID, **v)
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
        res = db.appliance.updateNetworkApplianceVlan(newTemplateID, **newVlan)
        #print(res)

    #remove the default vlan1 if the source doesn't have it
    if getID(src_vlans, 1) == None:
        target_vlans = db.appliance.getNetworkApplianceVlans(newTemplateID)
        if not getID(target_vlans,1) == None:
            db.appliance.deleteNetworkApplianceVlan(newTemplateID, 1)


    return True

def claimSerials(new_serial, target_netID):
    tmp = None
    global failed
    failed = []
    while True:
        try:
            db.networks.claimNetworkDevices(target_netID,serials=new_serial)
            break
        except meraki.APIError as me:
            print(me)
            tmp = me
            bad_serial = me.message['errors'][0].split("'")[1]
            print(f"Removing bad serial {bad_serial}")
            new_serial.remove(bad_serial)
            failed.append(bad_serial)
    print()
    print(f"Done. Total claimed licenses[{len(new_serial)}] and failed[{len(failed)}]")
    print(f"Claimed Serials[{new_serial}]")
    print(f"Failed Serials: {failed}")
    return

#clone/copy/move the device betwnee source and target
def copySettings(db, sourceNet, targetNet, target_template, destination_org):

    ## TIME TO BIND
    bindData = {}
    bindData['configTemplateId'] = target_template
    bindData['autoBind'] = False
    db.networks.bindNetwork(targetNet, **bindData)
    

### MOVE THE HARDWARE
    ### MOVE-MX
    sourceNet_devs = db.networks.getNetworkDevices(sourceNet)
    sourceMX = ""
    validProducts = [ "MX", "Z3"]
    for sd in sourceNet_devs:
        if sd['model'][:2] in validProducts:
            sourceMX = sd['serial']

    #You need to query the templateID, not the sourceNetwork.
    tempNetID = returnTemplateID(db, sourceNet) 
    ports = db.appliance.getNetworkAppliancePorts(tempNetID)

    ### Move MX-SVI

    src_vlans = db.appliance.getNetworkApplianceVlans(sourceNet)

    print()
    for v in src_vlans:
        vlan = v['id']
        keepers = ['subnet','applianceIp', 'fixedIpAssignments', 'reservedIpRanges', 'dhcpHandling', 'dhcpLeaseTime', 'dnsNameservers', 'dhcpOptions', 'dhcpBootOptionsEnabled', 'vlanId']
        if not 'vlanId' in v: v['vlanId'] = vlan
        newVlan = {}
        for tmp in v:
            if tmp in keepers:
                newVlan[tmp] = v[tmp]
        #if newVlan['vlanId'] in dst_GPMAP:
        #    newVlan['groupPolicyId'] = dst_GPMAP[newVlan['vlanId'] ]
        #print(newVlan)
        res = db.appliance.updateNetworkApplianceVlan(targetNet, **newVlan)
        print(f"Updating VLAN {vlan}")
        print(f"Before: {newVlan}")
        print(f"After: {res}")




    ### MOVE-MS
    source_switches = {}
    for sd in sourceNet_devs:
        if sd['model'][:2] == "MS":
            source_switches[sd['serial']] = copy.deepcopy(sd)
    source_switch_portconfig = {}
    for ss in source_switches:
        ss_ports = db.switch.getDeviceSwitchPorts(ss)
        source_switch_portconfig[ss] = ss_ports

 
    serials_claim = []
    for sd in sourceNet_devs:
        serials_claim.append(sd['serial'])
        print(f"{bc.OKGREEN} -Attempting moving of {bc.WARNING}{sd['model']} {bc.OKBLUE}{sd['serial']}{bc.OKGREEN} from {bc.WARNING}{sourceNet}{bc.ENDC}")
        tries = 0
        while True:
            try:
                print(db.networks.removeNetworkDevices(sourceNet, sd['serial']))
                break
            except Exception as e:
                print(e)
                print(f"ERROR removing serial {sd['serial']}")
                tries += 1
                if tries > 5: break
            
        
    claimSerials(serials_claim,targetNet)
    #print(db.networks.claimNetworkDevices(targetNet, serials=serials_claim))

    all_actions = list()
    for ss in source_switches:
        switch_ports = source_switch_portconfig[ss]
        for p in switch_ports:
            all_actions.append(db.batch.switch.updateDeviceSwitchPort(ss,**p))
    

    test_helper = batch_helper.BatchHelper(db, destination_org, all_actions, linear_new_batches=False, actions_per_new_batch=50)
    test_helper.prepare()
    test_helper.generate_preview()
    test_helper.execute()

    print(f'helper status is {test_helper.status}')

    batches_report = db.organizations.getOrganizationActionBatches(destination_org)
    new_batches_statuses = [{'id': batch['id'], 'status': batch['status']} for batch in batches_report if batch['id'] in test_helper.submitted_new_batches_ids]
    failed_batch_ids = [batch['id'] for batch in new_batches_statuses if batch['status']['failed']]
    print(f'Failed batch IDs are as follows: {failed_batch_ids}')

    return True #done with copySettings

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

def getGoodTemplateID(oid,garbage):
    garbage = garbage.strip()
    results = []
    foundNames = 0
    for o in templates:
        if not oid == '' and not oid == o: continue #if you specified a target org, only process that data otherwise look everywhere
        for t in templates[o]:
            if t['id'] == garbage:
                print(f"The TemplateID is good!!!")
                results.append((o,t['id'])) #save it as a tuple
            elif t['name'] == garbage:
                print(f"Found TemplateID by name! Its [{t['id']}] for {t['name']}")
                foundNames += 1
                results.append((o,t['id']))

    org_id = None
    template_id = None
    if len(results) == 1:
        org_id, template_id = results[0]
    elif len(results) > 1:
        if foundNames > 0:
            print(f"{bc.FAIL} Found too many templates with the same name. Try using the templateID instead...{bc.ENDC}")
            for r in results:
                oid,tid = r
                org_name = db.organizations.getOrganization(oid)['name']
                print(f"{bc.OKGREEN}Oranization[{bc.WARNING}{org_name}{bc.OKGREEN}] Org_ID[{bc.WARNING}{oid}{bc.OKGREEN}]  TemplateID[{bc.WARNING}{tid}{bc.OKGREEN}] ")
                
        

    return org_id, template_id
        

@click.command()
@click.argument('source', default = '')
@click.argument('template', default = '')
@click.argument('destination_org', default = '')
def move(source, template, destination_org):

    #loop = asyncio.get_event_loop()
    start_time = time()
    org_devices, org_networks, org_templates = asyncio.run(getEverything())
    end_time = time()
    elapsed_time = round(end_time-start_time,2)
    print(f"Loaded Everything took [{elapsed_time}] seconds")
    print()
    

    if source == '':
        print(f'Please enter Source Network (to move between templates), use Network_ID or Name as valid input')
        source = input(">")
    
    if not isNetID(source):
        print(f"Looks like a named network... searching....")
        print(source)
        for t_oid in org_networks:
            t_nets = org_networks[t_oid]
            for t_nid in t_nets:
                if t_nid['name'].lower() == source.lower():
                    source = t_nid['id']

    if not isNetID(source):
        print(f"Still not found..... sorry")
        exit()
    
    if template == '':
        print(f'Please enter Destination Template Name or TemplateID')
        template = input(">")

    network_obj = None
    org_id = ""

    if destination_org == '':
        print(f"Enter destination org here, cross-org moves require input here:")
        destination_org = input(">")
        if not destination_org in orgs_whitelist:
            orgs = db.organizations.getOrganizations()
            print(f"Searching for Destination_Org Name[{destination_org}]")
            res = findName(orgs,destination_org)

            if len(res) == 1: #should be true at this point unless there's no results
                destination_org = res[0]['id']
                print(f"Setting Destination_Org to {destination_org}")
            
            while len(res) > 1: #basically like an if statement until it's not-true
                found_it = False
                for tmp in res:
                    if tmp['name'] == destination_org:
                        destination_org = tmp['id']
                        print(f"Found it! {tmp}")
                        found_it = True
                if found_it: break
                print(res)
                print()
                print(f"Too many results, try entering a more specific ORG name")
                destination_org = input('>')
                res = findName(orgs,destination_org)  
            
    destination_org, templateID = getGoodTemplateID(destination_org, template) #if destination_org is <blank> at this point,then the returning call will populate the actual destination_orgid
    if not templateID == None: 
        template = templateID
    else:
        print()
        print(f"{bc.FAIL}ERROR:{bc.OKGREEN} Cannot find the templateID. Check your input and try again...{bc.ENDC}")
        return

    try:
        network_obj = db.networks.getNetwork(source)
        org_id = network_obj['organizationId'] #this sets the "source" ID
    except Exception as e:
        print(e)

    if network_obj == None: 
        print(f"ERROR: Network not found")

    print()

   
    template_obj = None
    try:
        for o in templates:
            for t in templates[o]:
                if t['id'] == template:
                    target_oid = o
        template_obj = db.organizations.getOrganizationConfigTemplate(target_oid, template)
    except:
        print(f"{bc.FAIL}ERROR: {bc.OKGREEN}Can't get Template[{bc.WARNING}{template}{bc.OKGREEN}] check your templateID and try again{bc.ENDC}")
        return
                    

    print(f"{bc.OKGREEN}Preparing to unbind Network [{bc.WARNING}{network_obj['name']}{bc.OKGREEN}] in [{bc.WARNING}{target_oid}{bc.OKGREEN}]{bc.ENDC}")    

  
    #input(f"{bc.WARNING}WARNING:{bc.OKGREEN}About to unbind a network from a template.... PRESS ENTER TO CONTINUE{bc.ENDC}")
    #print()

    keepList = ['productTypes', 'name', 'timeZone', 'tags', 'notes']
    newNET = {}
    for k in keepList:
        newNET[k] = network_obj[k]
    newNET['name'] += " MOVED"
    if not "MOVED" in newNET['tags']:
        newNET['tags'].append("MOVED")
    theUnbound = findName(db.organizations.getOrganizationNetworks(org_id), newNET['name'])
    if len(theUnbound) > 0:
        print(f"{bc.FAIL}ERROR: {bc.OKGREEN} Network Name [{bc.WARNING} {newNET['name']} {bc.OKGREEN}] already moved? {bc.ENDC}")
        db.networks.deleteNetwork(theUnbound[0]['id'])
        print(f"Deleted Network {theUnbound[0]}")
        return
    newNet_result = None
    target_netid = None
    try:
        newNet_result = db.organizations.createOrganizationNetwork(destination_org, **newNET)
        print(f"{bc.OKGREEN}Created new Network [{bc.WARNING} {newNET['name']} {bc.OKGREEN}] netID[{bc.WARNING} {newNet_result['id']} {bc.OKGREEN}] {bc.ENDC}")
        target_netid = newNet_result['id']
        #print(newNet_result)
    except meraki.APIError as e:
        print(e)
        print(f"{bc.FAIL}Failed to create a new network....{bc.ENDC}")

    start_time = time()
    total_sourceDevices = len(db.networks.getNetworkDevices(source))
    copyResult = copySettings(db, source, target_netid, template, destination_org)
    end_time = time()
    elapsed_time = round(end_time - start_time,2)
    
    #/verify the names still match, if your moving between orgs, it'll probaly lose that
    keepers = [ 'address', 'tags', 'notes', 'name']
    devs = []
    while len(devs) != total_sourceDevices:
        devs = db.networks.getNetworkDevices(target_netid)
        print(f"Device Count:  Current[{len(devs)}] Expected[{total_sourceDevices}]")
        if len(devs) != total_sourceDevices:
            print(f"Waiting for devs to show up in target network.....please wait a few minutes if your moving cross-org")
            sleep(30)
        
    for d in devs:
        changes = {}
        sourceDevice = getDevice(org_devices[org_id],d['serial']) #pull the original device objects and verify that the everything matches, otherwise write changes. Needed for cross-org
        targetDevice_obj = db.devices.getDevice(d['serial'])
        for k in keepers:
            if k in sourceDevice:
                if not k in targetDevice_obj:
                    changes[k] = sourceDevice[k]
    
        if len(changes) > 0: #need to update device
            res = db.devices.updateDevice(d['serial'],**changes)
            print(f"Updated Device[{d['serial']}] result[{res}]")
            print()
    #/end verify 
    
    if copyResult: #if the network was successfully copied, then rename/delete the old one
        oldName = db.networks.getNetwork(source)['name']
        tempNAME = oldName + " MOVED ID " + str(random.randint(11,10000))
        db.networks.updateNetwork(source, name=tempNAME)
        db.networks.updateNetwork(target_netid, name=oldName)
        db.networks.updateNetwork(source, name=newNET['name'])
        if len(db.networks.getNetworkDevices(source)) == 0:
            print(f"Old Network is empty, deleting netID[{source}]")
            db.networks.deleteNetwork(source)
        
        #db.networks.deleteNetwork(source)
        print()
        print(f"{bc.WARNING}SUCCESS -{bc.OKGREEN} network moved successfully in [{bc.WARNING}{elapsed_time}{bc.OKGREEN}]seconds... new networkID[{bc.WARNING}{target_netid}{bc.OKGREEN}] {bc.ENDC}")
        print()
        #change the URL to go directly to the hardware page
        statusURL = newNet_result['url'].replace('usage/list','nodes/new_wired_status/summary?timespan=86400')
        #click.launch(statusURL)
        print(statusURL)
    else: #if it didn't copy properly, delete the new-network we created
        print()
        print(f"{bc.FAIL}FAILED -{bc.OKGREEN} network could not be moved {bc.ENDC}")
        #db.networks.deleteNetwork(target_netid)
        print()

  
    return


if __name__ == '__main__':    
    move()
