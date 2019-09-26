import sys
import json
import lxml.etree as ET
from argparse import ArgumentParser
from ncclient import manager
from ncclient.operations import RPCError
from ipaddress import ip_address
from jinja2 import Environment

get_ntp_payload = """
<filter xmlns="urn:ietf:params:xml:ns:netconf:base:1.0">
  <native xmlns="http://cisco.com/ns/yang/Cisco-IOS-XE-native" xmlns:ios-ntp="http://cisco.com/ns/yang/Cisco-IOS-XE-ntp">
    <ntp>
      <ios-ntp:server/>
    </ntp>
  </native>
</filter>
"""

put_ntp_jinja = """
    <config xmlns="urn:ietf:params:xml:ns:netconf:base:1.0" xmlns:xc="urn:ietf:params:xml:ns:netconf:base:1.0">
      <native xmlns="http://cisco.com/ns/yang/Cisco-IOS-XE-native" xmlns:ios-ntp="http://cisco.com/ns/yang/Cisco-IOS-XE-ntp">
        <ntp>
          <ios-ntp:server xc:operation="replace">
{% for ip in ntp_ips %}
            <ios-ntp:server-list>
              <ios-ntp:ip-address>{{ ip }}</ios-ntp:ip-address>
            </ios-ntp:server-list>
{% endfor %}
          </ios-ntp:server>
        </ntp>
      </native>
    </config>
"""



if __name__ == '__main__':

    parser = ArgumentParser(description='Usage:')

    # script arguments
    parser.add_argument('-a', '--host', type=str, required=True,
                        help="Device IP address or Hostname")
    parser.add_argument('-u', '--username', type=str, required=True,
                        help="Device Username (netconf agent username)")
    parser.add_argument('-p', '--password', type=str, required=True,
                        help="Device Password (netconf agent password)")
    parser.add_argument('--port', type=int, default=830,
                        help="Netconf agent port")
    args = parser.parse_args()

    # connect to netconf agent
    with manager.connect(host=args.host,
                         port=args.port,
                         username=args.username,
                         password=args.password,
                         timeout=90,
                         hostkey_verify=False,
                         ) as m:

        with open("ntpservers.json") as f:
            j = json.load(f)
            new_ntp_servers = {n for n in j["ntp_servers"]}

        
        # get ntp servers
        try:
            response = m.get_config(source='running', filter=get_ntp_payload).xml
            data = ET.fromstring(response.encode("utf-8"))
        except RPCError as e:
            data = e._raw

        # print(ET.tostring(data, pretty_print=True, encoding="unicode"))

        nsmap = {None : "http://cisco.com/ns/yang/Cisco-IOS-XE-ntp"}

        old_ntp_elements = data.findall(".//ip-address", namespaces = nsmap)
        old_ntp_servers = {p.text for p in old_ntp_elements}
        print("Existing ntp servers: ", old_ntp_servers)
        print("New ntp servers: ",new_ntp_servers)

        if old_ntp_servers == new_ntp_servers:
            print("Nothing to do: NTP servers are the same")
            sys.exit()

        # create update template
        j2_env = Environment(trim_blocks=True, autoescape=True)
        template = j2_env.from_string(put_ntp_jinja)
        new_ntppayload = template.render(ntp_ips = new_ntp_servers)
        print("\nSending config:",new_ntppayload,sep="\n")

        # execute netconf operation
        try:
            response = m.edit_config(target='running', config=new_ntppayload).xml
            data = ET.fromstring(response.encode("utf-8"))
        except RPCError as e:
            data = e._raw

        # beautify output
        print("\nReceived reply:")
        print(ET.tostring(data, pretty_print=True, encoding="unicode"))
