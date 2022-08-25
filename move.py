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
def copySettings(db, sourceNet, targetNet, target_template):

    #quick logic check here 
    dst_vlans = db.appliance.getNetworkApplianceVlans(target_template)
    target_VLANIDS = []
    for v in dst_vlans:
        if not v['id'] in target_VLANIDS: target_VLANIDS.append(v['id'])
    target_VLANIDS.sort()

    src_vlans = db.appliance.getNetworkApplianceVlans(sourceNet)
    source_VLANIDS = []
    for v in src_vlans:
        if not v['id'] in source_VLANIDS: source_VLANIDS.append(v['id'])
    source_VLANIDS.sort()

    if not target_VLANIDS == source_VLANIDS:
        print(f"{bc.FAIL}ERROR: {bc.OKGREEN} Target VLAN/Subnetting doesn't match source. Needs to have the same amount of vlans with the same VLAN-IDs... {bc.OKGREEN}]{bc.ENDC}")
        print(f"{bc.OKGREEN} SOURCE VLANS[{bc.WARNING}{source_VLANIDS}{bc.OKGREEN}] TARGET_VLANS[{bc.WARNING}{target_VLANIDS}{bc.OKGREEN}]{bc.ENDC}")
        return False
    else:
        print(f"{bc.OKGREEN} SOURCE VLANS[{bc.WARNING}{source_VLANIDS}{bc.OKGREEN}] match Target Template[{bc.WARNING}{target_VLANIDS}{bc.OKGREEN}]{bc.ENDC}")
        


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
    #src_vlans = db.appliance.getNetworkApplianceVlans(sourceNet)
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
    
    #This section handles only DHCP stuff
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
            db.appliance.deleteNetworkApplianceVlan(targetNet, str(1))



    networkSettings = db.networks.getNetworkSettings(sourceNet)
    networkSettings.pop('remoteStatusPageEnabled')
    networkSettings.pop('fips')
    networkSettings.pop('secureConnect')
    db.networks.updateNetworkSettings(targetNet, **networkSettings)

    #content filter barfs on the blockedURL categories, need to filter out to just the ID
    contentfilter = db.appliance.getNetworkApplianceContentFiltering(sourceNet)
    blockedUrls = []
    for cf in contentfilter['blockedUrlCategories']:
        blockedUrls.append(cf['id'])
    contentfilter['blockedUrlCategories'] = blockedUrls
    db.appliance.updateNetworkApplianceContentFiltering(targetNet, **contentfilter)
    
    snmp = db.networks.getNetworkSnmp(sourceNet)
    db.networks.updateNetworkSnmp(targetNet, **snmp)

    trafficAnalysis = db.networks.getNetworkTrafficAnalysis(sourceNet)
    db.networks.updateNetworkTrafficAnalysis(targetNet, **trafficAnalysis)

    alerts = db.networks.getNetworkAlertsSettings(sourceNet)
    db.networks.updateNetworkAlertsSettings(targetNet, **alerts)

    src_syslog = db.networks.getNetworkSyslogServers(sourceNet)
    db.networks.updateNetworkSyslogServers(targetNet,**src_syslog)
    l3fw = db.appliance.getNetworkApplianceFirewallL3FirewallRules(sourceNet)
    l7fw = db.appliance.getNetworkApplianceFirewallL7FirewallRules(sourceNet)
    db.appliance.updateNetworkApplianceFirewallL3FirewallRules(targetNet, **l3fw)
    db.appliance.updateNetworkApplianceFirewallL7FirewallRules(targetNet, **l7fw)

    #for templated Networks, this needs to be templateID not sourceNET
  
    network_obj = db.networks.getNetwork(sourceNet)
    if 'configTemplateId' in network_obj:
        tsrules = db.appliance.getNetworkApplianceTrafficShapingRules(network_obj['configTemplateId']) #THIS PULLS FROM TEMPLATE
        intrusion = db.appliance.getNetworkApplianceSecurityIntrusion(network_obj['configTemplateId'])
        malware = db.appliance.getNetworkApplianceSecurityMalware(network_obj['configTemplateId'])

    else:
        tsrules = db.appliance.getNetworkApplianceTrafficShapingRules(sourceNet) #NOT a network unbind, but a regular network, oh well keep going
        intrusion = db.appliance.getNetworkApplianceSecurityIntrusion(sourceNet)
        malware = db.appliance.getNetworkApplianceSecurityMalware(sourceNet)

    db.appliance.updateNetworkApplianceTrafficShapingRules(targetNet, **tsrules)
    try:
        db.appliance.updateNetworkApplianceSecurityIntrusion(targetNet, **intrusion)
        db.appliance.updateNetworkApplianceSecurityMalware(targetNet, **malware)
    except:
        pass
    
    
    ''' 
    #not needed for a template->template move
    site2siteVPN = db.appliance.getNetworkApplianceVpnSiteToSiteVpn(sourceNet)
    try:
        db.appliance.updateNetworkApplianceVpnSiteToSiteVpn(targetNet, **site2siteVPN)
    except:
        print(f"Error trying to write the site2site rules, make sure your HUB/Spoke configuration is correct and try again")
   

    #Make sure firmware matches (i mean..... you don't have to... you create an empty network that matches the current template, and then bind it to the new template before you move hardware, so it'll be changed to the target firmware of the template anyway
    upgrade_prods = db.networks.getNetworkFirmwareUpgrades(sourceNet)['products']
    for p in upgrade_prods: #wired / wireless / appliance

        FW_source = db.networks.getNetworkFirmwareUpgrades(sourceNet)['products'][p]['currentVersion']['id']
        FW_target = db.networks.getNetworkFirmwareUpgrades(targetNet)['products'][p]['currentVersion']['id']
        if FW_target != FW_source:
            print(f"{bc.FAIL}WARNING: {bc.OKGREEN}{p.upper()} Firmware of source[{bc.WARNING}{FW_source}{bc.OKGREEN}] doesn't match target[{bc.WARNING}{FW_target}{bc.OKGREEN}].... fixing that....{bc.ENDC}")
            products={p: {'nextUpgrade': {'toVersion': {'id': FW_source}}}}
            if FW_source < FW_target:
                db.networks.updateNetworkFirmwareUpgrades(targetNet, products=products)
            else:
                db.networks.createNetworkFirmwareUpgradesRollback(targetNet, products=products)
    '''

    #print(f"Ready to move the hardware? (YES to continue)")
    #if not input('>') == "YES":
    #    sys.exit()

    ## TIME TO BIND
    bindData = {}
    bindData['configTemplateId'] = target_template
    bindData['autoBind'] = False
    db.networks.bindNetwork(targetNet, **bindData)
    
    for v in src_vlans:
        vlan = v['id']
        #keepers = ['fixedIpAssignments', 'reservedIpRanges', 'dhcpHandling', 'dhcpLeaseTime', 'dnsNameservers', 'dhcpOptions', 'dhcpBootOptionsEnabled', 'vlanId']
        keepers = ['name', 'applianceIp', 'subnet', 'vlanId']
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


### MOVE THE HARDWARE
    ### MOVE-MX
    sourceNet_devs = db.networks.getNetworkDevices(sourceNet)
    sourceMX = ""
    validProducts = [ "MX", "Z3"]
    for sd in sourceNet_devs:
        if sd['model'][:2] in validProducts:
            sourceMX = sd['serial']
    
    '''
    #This isn't needed for a template move, since we're not trying to preserve firmware versions....
    #LOOP until firmware in target network matches source... so the MX won't re-download FW and reboot
    while not FW_target == FW_source:
        FW_raw = db.networks.getNetworkFirmwareUpgrades(targetNet)
        FW_target = FW_raw['products']['appliance']['currentVersion']['id']
        print(f"{bc.FAIL}WARNING: {bc.OKGREEN}Waiting for firmware upgrade to finish...currently running fwID[{bc.WARNING}{FW_target}{bc.OKGREEN}] instead of fwID[{bc.WARNING}{FW_source}{bc.OKGREEN}]{bc.ENDC}")
        sleep(5)
    '''

    #You need to query the templateID, not the sourceNetwork.
    tempNetID = returnTemplateID(db, sourceNet) 
    ports = db.appliance.getNetworkAppliancePorts(tempNetID)

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
    
    orgid = network_obj['organizationId']

    test_helper = batch_helper.BatchHelper(db, orgid, all_actions, linear_new_batches=False, actions_per_new_batch=50)
    test_helper.prepare()
    test_helper.generate_preview()
    test_helper.execute()

    print(f'helper status is {test_helper.status}')

    batches_report = db.organizations.getOrganizationActionBatches(orgid)
    new_batches_statuses = [{'id': batch['id'], 'status': batch['status']} for batch in batches_report if batch['id'] in test_helper.submitted_new_batches_ids]
    failed_batch_ids = [batch['id'] for batch in new_batches_statuses if batch['status']['failed']]
    print(f'Failed batch IDs are as follows: {failed_batch_ids}')


    #NOW DO NETFLOW, will error out if you don't add hardware first
    netflow = db.networks.getNetworkNetflow(sourceNet)
    try: #need to do this in a try/except because a Z3 will throw an error (or network without license)
        db.networks.updateNetworkNetflow(targetNet, **netflow)
    except:
        print("No Netflow settings....")

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
    #return 
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

def getGoodTemplateID(garbage):
    garbage = garbage.strip()
    results = []
    foundNames = 0
    for o in templates:
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
def move(source, template):

    
    network_obj = None
    org_id = ""

    org_id, templateID = getGoodTemplateID(template)
    if not templateID == None: 
        template = templateID
    else:
        print()
        print(f"{bc.FAIL}ERROR:{bc.OKGREEN} Cannot find the templateID. Check your input and try again...{bc.ENDC}")
        return

    try:
        network_obj = db.networks.getNetwork(source)
        org_id = network_obj['organizationId']
    except Exception as e:
        print(e)

    if network_obj == None: 
        print(f"ERROR: Network not found")

    print()

   
    target_oid = ''
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
                    

    print(f"{bc.OKGREEN}Preparing to unbind Network [{bc.WARNING}{network_obj['name']}{bc.OKGREEN}] in [{bc.WARNING}{org_id}{bc.OKGREEN}]{bc.ENDC}")    

    loop = asyncio.get_event_loop()
    start_time = time()
    org_devices, org_networks, org_templates = loop.run_until_complete(getEverything())
    end_time = time()
    elapsed_time = round(end_time-start_time,2)
    print(f"Loaded Everything took [{elapsed_time}] seconds")
    print()
    
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
        newNet_result = db.organizations.createOrganizationNetwork(org_id, **newNET)
        print(f"{bc.OKGREEN}Created new Network [{bc.WARNING} {newNET['name']} {bc.OKGREEN}] netID[{bc.WARNING} {newNet_result['id']} {bc.OKGREEN}] {bc.ENDC}")
        target_netid = newNet_result['id']
        #print(newNet_result)
    except meraki.APIError as e:
        print(e)
        print(f"{bc.FAIL}Failed to create a new network....{bc.ENDC}")

    start_time = time()
    copyResult = copySettings(db, source, target_netid, template)
    end_time = time()
    elapsed_time = round(end_time - start_time,2)
    
    
    
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
