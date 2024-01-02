"""
    DynDNS

"""
import json
import logging
import os
import random
import subprocess
import sys

import arrow
import redis
import requests

from modules import quigley_notify

LOG_LEVEL=logging.DEBUG
log_format = logging.Formatter('[%(asctime)s] [%(levelname)s] - %(message)s')
log = logging.getLogger(__name__)                                  
log.setLevel(LOG_LEVEL)                                                                                       
handler = logging.StreamHandler(sys.stdout)                             
handler.setLevel(LOG_LEVEL)                                        
handler.setFormatter(log_format)                                        
log.addHandler(handler)   
REDIS_HOST = os.environ.get("REDIS_HOST")
REDIS_DB = os.environ.get("REDIS_DB")
REDIS_PASS = os.environ.get("REDIS_PASS")
DOMAIN_FILE = os.environ.get("DOMAIN_FILE")
NOTIFY_URL = os.environ.get("NOTIFY_URL")
NOTIFY_PASS = os.environ.get("NOTIFY_PASS")
FORCE_UPDATE = os.environ.get("FORCE_UPDATE", False)


class DynDns:

    def __init__(self):
        self.force = False
        self.send_notifications = True
        self.domains = []
        self.connect_to_redis()
        self.current_ip = None

    def run(self, args):
        print("Starting DynDNS")
        self.get_domain_config()
        if len(args) > 1:
            if args[1] == "-f" or args[1] == "--force":
                self.force = True
            elif FORCE_UPDATE and FORCE_UPDATE == "true":
                self.force = True

        last_ip_change_date = self.get_last_ip_change_date()
        wan_ip_age = self.get_wan_ip_age(last_ip_change_date)
        wan_ip_dns_status = self.get_wan_ip_dns_status()
        self.cached_wan_ip = self.get_cached_ip()
        self.current_ip = self.get_current_ip()

        self.dig_domains()

        if not self.force:
            # If the WAN ip hasn't changed we're done and can exit.
            if wan_ip_dns_status == 'success' and self.cached_wan_ip == self.current_ip:
                print('IP %s has not changed, finishing.' % (self.current_ip))
                exit(0)

        print("Updating dyns")
        print('Current IP: %s' % (self.current_ip))

        # Update Redis with the new WAN ip
        self.r.set("wan_ip", self.current_ip)
        self.r.set('wan_ip_change_date', str(arrow.now().datetime))
        self.r.set("wan_ip_dns_status", 'success')
        self.set_dns_ip(self.current_ip)
        print("Successfully updated DNS")
        self.notify()
        exit(0)

    def get_domain_config(self):
        """Load the domain file into a class var."""
        if not os.path.exists(DOMAIN_FILE):
            print("Cannot find domain config file: %s" % DOMAIN_FILE)
            exit(1)

        the_file = open(DOMAIN_FILE)
        try:
            domain_json = json.loads(the_file.read())
        except json.decoder.JSONDecodeError as e:
            print("Could not parse file: %s. Got error: %s" % (DOMAIN_FILE, e))
            exit(1)
        self.domains = domain_json["domains"]
        return True

    def check_change_in_domain_config(self):
        """Check if the domain config has changed."""
        self.r.get("domain-config")

    def connect_to_redis(self):
        print("Connecting to Redis Host: %s" % REDIS_HOST)
        try:
            self.r = redis.Redis(host=REDIS_HOST, port=6379, db=REDIS_DB)
        except Exception as e:
            print(
                "Cannot connect to redis host: %s REDIS_HOST on DB: %s. %s" % (
                    REDIS_HOST,
                    REDIS_DB
            ))
            exit(1)

    def get_last_ip_change_date(self) -> arrow.arrow.Arrow:
        """Get the cached ip address from redis, that will be assumed to be the current ip address. """
        wan_ip_change_date = self.r.get('wan_ip_change_date')
        if wan_ip_change_date:
            wan_ip_change_date = arrow.get(wan_ip_change_date.decode())
        else:
            print("Couldn't find last IP change date")
        return wan_ip_change_date

    def get_wan_ip_age(self, last_ip_change_date: arrow.arrow.Arrow):
        if not last_ip_change_date:
            return ''

        return arrow.now() - last_ip_change_date

    def get_wan_ip_dns_status(self):
        """Get the cached ip address from redis, that will be assumed to be the current ip address. """
        wan_ip_dns_status = self.r.get('wan_ip_dns_status')
        if wan_ip_dns_status:
            wan_ip_dns_status = wan_ip_dns_status.decode()
        else:
            wan_ip_dns_status = ''
        return wan_ip_dns_status

    def get_cached_ip(self):
        """Get the cached ip address from redis, that will be assumed to be the current ip address. """
        cached_wan_ip = self.r.get('wan_ip')
        if cached_wan_ip:
            cached_wan_ip = cached_wan_ip.decode()
        return cached_wan_ip

    def get_current_ip(self):
        """Get the current WAN IP off the host running this script. """
        wan_ip_apis = [
            "https://ip.seeip.org/jsonip?",
            "https://api.ipify.org/?format=json",
            "https://api.my-ip.io/ip.json",
            "http://icanhazip.com"
            ]
        api_number = random.randint(0, len(wan_ip_apis))
        current_wan_ip = ""

        while current_wan_ip == "":
            api_number = random.randint(0, len(wan_ip_apis) - 1)
            current_wan_ip = self.attempt_to_get_ip(wan_ip_apis[api_number])
            del wan_ip_apis[api_number]
        return current_wan_ip

    def attempt_to_get_ip(self, api_url: str):
        print('Getting IP from: %s' % api_url)
        if api_url == "http://icanhazip.com":
            return self._get_wan_ip_from_icanhazip(api_url)
        else:
            return self._get_wan_ip_from_generic_api(api_url)

    def notify(self) -> bool:
        """Send a notification of the updated IP."""
        if not self.send_notifications:
            return True
        if self.force:
            msg = f"<b>DynDNS:</b> Updated DNS because of a force operation. Set IP to "
            msg += f"<code>{self.current_ip}</code>."
        else:
            msg = f"<b>DynDNS:</b> IP changed to changed from <code>{self.cached_wan_ip}</code> to "
            msg += f"<code>{self.current_ip}</code>. Updated {len(self.domains)} DNS recoreds."
        return quigley_notify.send_notification(msg)

    def _get_wan_ip_from_generic_api(self, url):
        response = self._make_request(url)
        if not response:
            return ''
        response_j = response.json()
        return response_j['ip']

    def _get_wan_ip_from_icanhazip(self, url):
        response = requests.get(url)
        current_ip = response.text.replace("\n", "")
        return current_ip

    def _make_request(self, url):
        try:
            response = requests.get(url)
        except requests.exceptions.ConnectionError:
            print('Connection failed to: %s' % url)
            return ''

        if str(response.status_code)[0] != '2':
            print('Bad Response of "%s" from %s' % (response.status_code, url))
            return ''

        return response

    def set_dns_ip(self, ip: str) -> bool:
        """Update NameCheap DNS records with the ip address supplied. """
        nc_park = "https://dynamicdns.park-your-domain.com/update?host=%s&domain=%s&password=%s&ip=%s"
        status = True
        for domain in self.domains:
            url = nc_park % (domain['host'], domain['domain'], domain['password'], ip)
            response = requests.get(url)
            if response.status_code != 200:
                print('Error setting DNS ip')
                status = False
            else:
                print('Success updating DNS: %s.%s' % (
                    domain['host'],
                    domain['domain']))

        return status

    def dig_domains(self) -> dict:
        """Check to see what dig has to say about all the domains we're managing, and make sure that
        global DNS servers agree with what we believe our IP address to be.
        """
        print("Running dig DNS checks for current domains")
        results = {
            "domains": []
        }
        for domain in self.domains:
            if domain["domain"] == "really-poor.info" and domain["host"] == "@":
                continue
            subdomain = ""
            if domain["host"] == "*":
                subdomain = "test."
            domain_to_test = "%s%s" % (subdomain, domain["domain"])
            cmd = ["dig", "+short", domain_to_test]
            result = subprocess.check_output(cmd)
            ip_address = result.decode().replace("\n", "")
            domain["ip_address"] = ip_address
            results["domains"].append(domain)
            print("%s - %s" % (ip_address, domain_to_test))
            if ip_address != self.current_ip:
                self.force = True
                print("IP %s does not match current IP:%s. Setting force to true." %  (
                    domain["domain"],
                    domain["host"]))
        return True

if __name__ == "__main__":
    DynDns().run(sys.argv)

# End File: dyndns/src/dyndns.py
    