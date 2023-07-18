# dyndns
DynDns Service on Kubernetes


## Create the Secret
```bash
kubectl create secret generic domain-config --from-file=domains.json
```