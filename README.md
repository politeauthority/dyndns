# DynDns
![Build/Test](https://github.com/politeauthority/dyndns/actions/workflows/build-test.yaml/badge.svg)

A DynDns service for Kubernetes. There a lots of ways to do dynamic DNS, this is how I decided to do
it.

## What it Does
This creates a Kubernetes CronJob in the namespace `dyndns`. Every 5 minutes it:

1. **Detects the WAN IP.** It asks public IP services over HTTPS and DNS. Two different operators
   have to agree, one of them over TLS, and the answer has to be a public IPv4 address.
2. **Compares against DNS.** It asks Namecheap's nameservers what each record serves. A wildcard is
   tested with a random name.
3. **Writes only what's wrong,** then waits to confirm the nameservers serve the new IP.
4. **Checks public DNS** on runs with no writes, including that the domain is still delegated to
   Namecheap.

The job keeps no state. A failed write gets retried on the next run because the record is still
wrong. It exits non-zero when DNS is wrong or can't be checked.

The job can only update records that already exist. Namecheap's dynamic DNS endpoint can't create
them, so add new ones in the Namecheap panel as `A + Dynamic DNS Record`.

ℹ️ Currently only supports Namecheap.

## Configuration
| Variable | Default | Purpose |
| --- | --- | --- |
| `DOMAIN_FILE` | `/config/domains.json` | Records to manage |
| `DRY_RUN` | `false` | Log decisions without writing, always exit 0 |
| `QUIGLEY_URL`, `QUIGLEY_USER`, `QUIGLEY_KEY` | unset | Post updates to Matrix through quigley-api. Skipped if any is unset. |
| `QUIGLEY_ROOM` | `default` | Room alias for updates |

## Tests
```bash
docker build --target test .
```

## Getting Started
 - In the Namecheap DNS console, you'll need to enable Dynamic DNS and get a Dynmaic DNS Password, a 32 character hash.
 - Create a copy of the generic env for your use in the `env` dir [kubernetes-manifests/env/generic](kubernetes-manifests/env/generic).
 - Create a json file like the one in this repo for each of the domains you want to manage DNS for. [examples/domains.json](example/domains.json)
 - Create a the `domain-config` secret.
    ```bash
    kubectl create secret generic domain-config --from-file=domains.json
    ```
    **OR** if you use sealed secrets.
    ```bash
    kubectl create secret generic domain-config \
        -o yaml \
        -n dyndns \
        --dry-run=client \
        --from-file=domains.json | \
        kubeseal --format yaml > kubernetes-manifests/env/<my-new-env>/sealed-secret-domains.yaml
    ```
  - If you use ArgoCD, fork this app and the example Argo app in [examples/argocd-dyndns.yaml](examples/argocd-dyndns.yaml) will get you moving.

## Next Up
 - Create a Grafana dashboard/ alert for DynDNS
 - Support more registrars.
