#!/usr/bin/env python3

import yaml
import logging
import re
import xml.etree.ElementTree as ET
import sys
from pprint import pprint as pp
from collections import namedtuple
from datetime import date
import time

from zeep import Client
from zeep import Plugin
from zeep import xsd
from zeep.cache import SqliteCache
from zeep.transports import Transport
from zeep.exceptions import Fault
from zeep.plugins import HistoryPlugin
import requests
from requests import Session
from requests.auth import HTTPBasicAuth
from urllib3 import disable_warnings
from urllib3.exceptions import InsecureRequestWarning
from lxml import etree
from getpass import getpass 


def npaNxxToRoutePattern(npa,nxx):
    r""" Create route pattern in form of \+1.NpaNxxXXXX from npa/nxx tuple """
    return  f"\\+1.{npa}{nxx}XXXX"

def routePatternToNpaNxx(pattern):
    r""" Convert route pattern in form of \+1.NpaNxxXXXX to (npa,nxx) tuple """
    rpRegex = re.compile(r"\\\+1\.(?P<npa>[2-9][0-9][0-9])(?P<nxx>[2-9][0-9][0-9])XXXX")
    match = re.fullmatch(rpRegex,pattern)
    if match:
        return match.groupdict()["npa"],match.groupdict()["nxx"]
    else:
        return None

def getLocalNpaNxx(npa,nxx):
    """ Get all NPA/NXX that are locally callable to the passed npa/nxx from localcallingguide.com. """
    r = requests.get(f"https://www.localcallingguide.com/xmllocalprefix.php?npa={npa}&nxx={nxx}")
    root = ET.fromstring(r.text)
    prefixes = root.findall("./lca-data/prefix")
    localNpaNxx =  {(x.find("./npa").text,x.find("./nxx").text) for x in prefixes}
    return localNpaNxx

def main():
    with open("cucm-new-e164-patterns.yml") as f:
        config = yaml.safe_load(f)
    ccmUrl = config['CCM']['URL']
    print("Enter credentials to connect to CUCM ", ccmUrl) 
   

    # Test the AXL URL to make sure it's ok
    while True:
        username = input("Username: ")
        password = getpass("Password: ")

        session = Session()
        
        session.verify = config['CCM']['CertificateAuthority']
        # or to disable SSL verification
        # ssl.verify = False
        
        session.auth = HTTPBasicAuth(username, password)
        r = session.get(ccmUrl)

        if r.status_code == 200:
            break
        elif r.status_code == 401:
            print("Received HTTP/401 Unauthorized. Reenter credentials")
        else:
            sys.exit("HTTP not succesful. Response code " + str(r.status_code))

    # zeep setup stuff
    wsdl = config['CCM']['WSDL']
    binding = "{http://www.cisco.com/AXLAPIService/}AXLAPIBinding"

    transport = Transport(cache=SqliteCache(), session=session, timeout=20)
    history = HistoryPlugin()
    client = Client(wsdl=wsdl, transport=transport, plugins=[history])
    service = client.create_service(binding, ccmUrl)

    def show_history():
        for item in [history.last_sent, history.last_received]:
            print(etree.tostring(item["envelope"], encoding="unicode", pretty_print=True))

    # Retrieve existing E.164 route patterns from CUCM and store in tuple list
    try:
        resp = service.executeSQLQuery(sql="".join(["SELECT dnorpattern FROM numplan,routepartition ",
                    "WHERE numplan.fkroutepartition = routepartition.pkid AND routepartition.name = '", config['CCM']['LocalPartition'] +
                    "' AND numplan.tkpatternusage = 5"]))
        # print(resp['return'])
        # routePatterns = [row[0].text for row in resp['return']['row']]

        # store route patterns as set of tuples
        cucmPatterns = {routePatternToNpaNxx(rp[0].text) for rp in resp['return']['row']}
        print(f"Retrieved {len(cucmPatterns)} route patterns from CUCM")
        # print(cucmPatterns)

        NpaNxxList = []
        totalPatterns = 0
        Location = namedtuple('Location', ['name', 'uuid', 'npanxx'])
        for loc in config['Locations']:
            print("\nRetrieving local NPA/NXX for ", loc['Name'])
            localPatterns = getLocalNpaNxx(loc['NPA'],loc['NXX'])
            print(f"Retrived {len(localPatterns)} NPA/NPXX")
            npaNxxToInsert = localPatterns - cucmPatterns
            totalPatterns += len(npaNxxToInsert)
            print(f"{len(npaNxxToInsert)} new NPA/NXX to insert for {loc['Name']}")
            NpaNxxList.append(Location(loc['Name'],loc['RefUUID'],npaNxxToInsert))
            if npaNxxToInsert:
                pp(npaNxxToInsert)

        if totalPatterns == 0:
            print("No new patterns to insert.")
            sys.exit()
        else:
            print("Total of ", totalPatterns, " to insert.\n")

        while True:
            answer = input("\nDo you want to insert these patterns into CUCM? (Type yes or no)  ")
            if answer.upper() == "YES":
                break
            elif answer.upper() == "NO":
                sys.exit()
            else:
                print("Enter yes or no")

        today = date.isoformat(date.today())
        patternsInserted = 0
        for loc in NpaNxxList:
            print("Retrieving reference pattern for ", loc.name)
            resp = service.getRoutePattern(uuid=loc.uuid)
            refRP = resp['return']['routePattern']
            print("Retrieved pattern ", refRP['pattern'])
            print("Adding route patterns for ", loc.name)

            factory=client.type_factory("ns0")

            for npanxx in loc.npanxx:
                routePattern = npaNxxToRoutePattern(*npanxx)
                factoryRP = factory.XRoutePattern(pattern = routePattern)
                elementsToCopy = ["blockEnable", "calledPartyTransformationMask", "callingPartyTransformationMask", "useCallingPartyPhoneMask", "callingPartyPrefixDigits",
                    "digitDiscardInstructionName", "networkLocation", "callingLinePresentationBit", "callingNamePresentationBit", "connectedLinePresentationBit", 
                    "connectedNamePresentationBit", "provideOutsideDialtone", "callingPartyNumberingPlan", "callingPartyNumberType", "calledPartyNumberingPlan", "calledPartyNumberType",
                    "destination" ]
                newRP = {k : refRP[k] for k in factoryRP if k in elementsToCopy }
                newRP["pattern"] = routePattern
                newRP["description"] = f"{loc.name} local route - {today}"
                newRP["routePartitionName"] = refRP["routePartitionName"]["_value_1"]
                resp = service.addRoutePattern(newRP)
                print("Added route pattern ", routePattern)
                patternsInserted += 1
                time.sleep(0.1)

    except Fault:
        print("\n AXL ERROR")
        show_history()
        raise

    print (f"Completed addition of {patternsInserted} new route patterns.")


if __name__ == "__main__":
    main()

