"""
    Use Dig to check domain IP addresses.

"""

import subprocess

domains = {
	"domains": [
        {
			"domain": "really-poor.info",
			"host": "*",
		},
		{
			"domain": "really-poor.info",
			"host": "@",
		},
		{
			"domain": "squid-ink.us",
			"host": "@",
		},
		{
			"domain": "squid-ink.us",
			"host": "*",
		},
		{
			"domain": "alix.lol",
			"host": "@",
		},
		{
			"domain": "alix.lol",
			"host": "*",
		}
	]
}

def dig_domains() -> dict:
    """Check to see what dig has to say about all the domains we're managing, and make sure that
    global DNS servers agree with what we believe our IP address to be.
    """
    results = {
        "domains": []
    }
    for domain in domains["domains"]:
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
    
    return results


if __name__ == "__main__":
    dig_domains()