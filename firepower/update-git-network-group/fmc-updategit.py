#!/usr/bin/python3
# update git IPs in FMC

import requests
import json
import sys
import yaml
from pprint import pprint as pp
from ipaddress import ip_network,ip_address
from jinja2 import Environment

fmc_server = ""
fmc_verify = False

def get_fmc_token(fmcuser, fmcpassword):
    fmc_headers = {'Content-Type': 'application/json'}
    api_auth_path = "/api/fmc_platform/v1/auth/generatetoken"
    r = requests.post(fmc_server + api_auth_path, headers=fmc_headers, auth=requests.auth.HTTPBasicAuth(fmcuser,fmcpassword), verify=fmc_verify)
    auth_headers = r.headers
    auth_token = auth_headers.get('X-auth-access-token', default=None)
    if auth_token == None:
        print("auth_token not found. Exiting...")
        sys.exit()
    return auth_token

def fmc_find_networkgroup(auth_token, obj_name):
    fmc_headers = {'Content-Type': 'application/json', 'X-auth-access-token' : auth_token}
    api_path = "/api/fmc_config/v1/domain/e276abec-e0f2-11e3-8169-6d9ed49b625f/object/networkgroups?limit=200"    # param
    url = fmc_server + api_path
    if (url[-1] == '/'):
        url = url[:-1]
    
    # REST call with SSL verification turned off: 
    with requests.get(url, headers=fmc_headers, verify=fmc_verify) as r:
        r.raise_for_status()
        resp = r.text
        json_resp = json.loads(resp)
        # print(json.dumps(json_resp,sort_keys=True,indent=4, separators=(',', ': ')))

    for netgrp in json_resp["items"]:
        if netgrp["name"].lower() == obj_name.lower():
            return netgrp["id"]
    
    return None

def fmc_update_networkgroup_IPs(auth_token, obj_id, new_ips):
    attr_to_copy = ["id","name","description","overridable"]

    fmc_headers = {'Content-Type': 'application/json', 'X-auth-access-token' : auth_token}
    api_path = "/api/fmc_config/v1/domain/e276abec-e0f2-11e3-8169-6d9ed49b625f/object/networkgroups/" + obj_id    # param
    url = fmc_server + api_path
    if (url[-1] == '/'):
        url = url[:-1]
    
    # get existing object group
    with requests.get(url, headers=fmc_headers, verify=fmc_verify) as r:
        r.raise_for_status()
        resp = r.text
        json_resp = json.loads(resp)
        # print(json.dumps(json_resp,sort_keys=True,indent=4, separators=(',', ': ')))

    
    old_ips = {ip_network(literal["value"]) for literal in json_resp["literals"]}
    
    if (new_ips == old_ips):
        print ("No change between FMC-configured IPs and new IPs.")
        return False

    print("Old IPs:")
    for ipnet in old_ips:
        print("    ", ipnet)

    print("New IPs")
    for ipnet in new_ips:
        print("    ", ipnet)

    print()
    while True:
        answer = input("Do you want to replace the existing object group members with the new IPs? [yes/no]: ")
        if answer.upper() == "YES":
            break
        elif answer.upper() == "NO":
            return False
        else:
            print("Enter yes or no.")

    new_literals  = []
    for i in new_ips:
        if i.version == 4 and i.prefixlen == 32:
            new_literals.append({"type":"Host","value":str(i.network_address)})
        else:
            new_literals.append({"type":"Network","value":str(i)})

    put_data = { j : json_resp[j] for j in json_resp if j in attr_to_copy }
    put_data["literals"] = new_literals

    # print(json.dumps(put_data,sort_keys=True,indent=4, separators=(',', ': ')))

    with requests.put(url, data=json.dumps(put_data), headers=fmc_headers, verify=fmc_verify) as r:
        r.raise_for_status()
        resp = r.text
        print("\nOBJECT UPDATE SUCCESSFUL")
        print("New object group configuration:")
        json_resp = json.loads(resp)
        print(json.dumps(json_resp,sort_keys=True,indent=4, separators=(',', ': ')))
        print("\n")

    return True

def main():
    global fmc_server
    global fmc_verify
    # get FP credentials

    with open("fmc-config.yml") as f:
        fmc_config = yaml.safe_load(f)
        fmc_netgroup = fmc_config["git-group"]
        fmc_server = fmc_config["server"]
        fmc_credentials = fmc_config["CredentialFile"]
        if "CertificateAuthority" in fmc_config:
            fmc_verify = fmc_config["CertificateAuthority"]
            
    with open(fmc_credentials) as f:
        fmc_creds = yaml.safe_load(f)
        fmcuser = fmc_creds["username"]
        fmcpassword = fmc_creds["password"]

    fmctoken = get_fmc_token(fmcuser,fmcpassword)

    # find UUID of GIT object group
    netgroup_id = fmc_find_networkgroup(fmctoken, fmc_netgroup)
    if netgroup_id == None:
        print("ERROR: Group", fmc_netgroup, "not found",file=sys.stderr) 
        sys.exit(1)
    else:
        print("Found object group ", fmc_netgroup, ". UUID: ", netgroup_id, sep='')

    
    # get git IPs
    headers = {'Accept' : 'application/vnd.github.v3+json'}
    url = "https://api.github.com/meta"

    with requests.get(url, headers = headers) as r:
        r.raise_for_status()
        j = r.json()

    git_ips = {ip_network(i) for i in j['git'] }
    print ("Retrieved GIT networks. Total network count:", len(git_ips), "\n")

    if (fmc_update_networkgroup_IPs(fmctoken, netgroup_id, git_ips)):
        print("Object group", fmc_netgroup, "updated with new IPs.")
    else:
        print("No changes made.")

if __name__ == "__main__":
    main()