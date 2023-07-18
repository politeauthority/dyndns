# DynDns
![Build/Test](https://github.com/politeauthority/dyndns/actions/workflows/build-test.yaml/badge.svg)

A DynDns Service on Kubernetes. There a lots of ways to do dynamic DNS, this is how I decided to do
it.

## What it Does
This creates a kubernetes cronjob in the namespace `dyndns`. The cronjob checks your current WAN ip
address, if it has changed it pushes up the changed ip address to Namecheap. Specify any number of
domains.
ℹ️ Currently only supports Namecheap.

## Getting Started
 - In the Namecheap DNS console, you'll need to enable Dynamic DNS and get a Dynmaic DNS Password, a 32 character hash.
 - Create a copy of the generic env for your use in the `env` dir. [kubernetes-manifests/env/generic](kubernetes-manifests/env/generic)
 - Update the [kubernetes-manifests/env/generic/configmap-patch.yaml](kubernetes-manifests/env/generic/configmap-patch.yaml) with connection details for the Redis instance you are bringing in.
 - Create a json file like the one in this repo for each of the domains you want to manage DNS for. [examples/domains.json](example/domains.json)
 - Create a the `domain-config` secret.
    ```bash
    kubectl create secret generic domain-config --from-file=domains.json
    ```
    **OR** if you use sealed secrets.
    ```bash
    kubectl create secret generic controller-manager \
        -o yaml \
        -n dyndns \
        --dry-run=client \
        --from-file=domains.json | \
        kubeseal --format yaml > kubernetes-manifests/env/<my-new-env>/sealed-secret-domains.yaml
    ```
  