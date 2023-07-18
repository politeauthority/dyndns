# DynDns
![Build](https://github.com/politeauthority/dyndns/actions/workflows/build.yaml/badge.svg)

A DynDns Service on Kubernetes. This creates a kubernetes cronjob in the namespace `dyndns`, which checks
your current WAN ip address, and sets DNS records accordingly. Currently we only support Namecheap.


## Getting Started
 - In the Namecheap DNS console, you'll need to enable Dynamic DNS and get a Dynmaic DNS Password, a 32 character hash.
 - Create a json file like the one in this repo for each of the domains you want to manage DNS for. [domains.json](domains.json)
 - Create a the `domain-config` secret.
```bash
kubectl create secret generic domain-config --from-file=domains.json
```
OR if you use sealed secrets.
```bash
kubectl create secret generic controller-manager \
    -o yaml \
    -n dyndns \
    --dry-run=client \
    --from-file=domains.json| \
    kubeseal --format yaml > sealed-secret-domains.yaml
```
