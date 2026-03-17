# Tactical Edge Migration Testbed: Infrastructure Provisioning

This directory contains the Infrastructure as Code (IaC) configurations to provision the local experimental baseline.

The Terraform scripts contained herein automatically deploy two isolated, identical Ubuntu edge nodes to simulate a distributed Tactical Edge Environment (TEE).

## The Rationale: Why Multipass over Docker-in-Docker (KinD)?

When evaluating stateful service migration, the choice of local infrastructure dictates the validity of the gathered metrics. While tools like Kubernetes-in-Docker (KinD) or Minikube are industry standards for general orchestration testing, **Multipass (via KVM/QEMU)** was specifically selected for this research methodology for three critical reasons:

1. **Strict Kernel and Namespace Isolation for CRIU:** Container migration relies heavily on Checkpoint/Restore In Userspace (CRIU). CRIU is highly sensitive to nested namespaces and complex cgroup hierarchies. Running Kubernetes nodes as Docker containers (Docker-in-Docker) creates nested environments where CRIU frequently fails to resolve the correct process boundaries. Multipass provisions true lightweight Virtual Machines (VMs) with dedicated, unshared Linux kernels, ensuring a clean namespace hierarchy for baseline container checkpoints.

2. **Precise Network Emulation for Tactical Environments:** A core metric of this thesis is evaluating migration under constrained network conditions (high latency, low bandwidth, intermittent connectivity). Each Multipass VM has a distinct virtual Network Interface Card (vNIC). This allows us to inject precise latency and packet loss directly at the VM interface level using Linux Traffic Control (`tc`) and `netem`, bypassing the unpredictable routing behavior of complex container overlay networks.

3. **Realistic Resource Utilization Metrics:** To accurately measure the CPU and memory spikes during the `dump` and `restore` phases of container and WebAssembly (WASM) migrations, strict hardware boundaries are required. VMs provide rigid resource allocation (e.g., exactly 2 vCPUs and 2GB RAM per node), preventing host-system background noise from skewing the runtime overhead metrics.

## Prerequisites

To replicate this environment, the host machine must have the following installed:
* **Terraform** (v1.0 or newer)
* **Multipass** (v1.13 or newer)

## Experiment Scope References

After infrastructure provisioning, continue with the migration experiment guides:

* Container baseline (Kubernetes + CRIU): `../../Container/K8_MIGRATION_SETUP.md`
* Wasm migration track (Oakestra): `../../README.md` (project-level methodology and objectives)

## Usage Instructions

### 1. Initialize the Environment
Initialize Terraform to download the required Canonical Multipass provider plugin.
```bash
terraform init
```
### 2. Provision the Edge Nodes
Apply the configuration to spin up the two virtual edge nodes (edge-node-1 and edge-node-2).

```bash
terraform apply
```

_(Type yes when prompted or add --auto-approve. VM creation finishes first; CRIU installation continues in the background inside each node and can take several additional minutes depending on host performance.)_

### 3. Verify the Deployment
Once provisioned, use the Multipass CLI to verify the nodes are running and retrieve their IPv4 addresses.

```bash
multipass list
```
### 4. Accessing the Nodes
To open a secure shell into a specific node to run manual CRIU tests or apply tc network constraints:
```bash
multipass shell edge-node-1
```

Before running CRIU commands, verify the bootstrap service has finished:

```bash
systemctl status criu-bootstrap.service
tail -f /var/log/criu-bootstrap.log
criu --version
```

### 5. Teardown
To cleanly destroy the experimental baseline and release host resources:
```bash
terraform destroy
```


### Troubleshooting

#### Error at Terraform Apply
If you got a timeout while launching a node, check whether the instance actually came up and whether the in-guest bootstrap completed:
```bash
multipass list
multipass exec edge-node-1 -- cloud-init status --long
multipass exec edge-node-1 -- systemctl status criu-bootstrap.service
multipass exec edge-node-1 -- tail -n 100 /var/log/criu-bootstrap.log
```

If a partial instance was created and you want to retry cleanly, run:

```bash
multipass delete edge-node-1 edge-node-2
multipass purge

terraform apply
```

#### Criu check error
When you enter the node and run `criu check`, if you encounter an error, first confirm the CRIU bootstrap finished successfully. Run:
```bash
systemctl status criu-bootstrap.service
tail -f /var/log/criu-bootstrap.log
```
to inspect the installation logs inside the VM. If the service completed, verify the binary is present with `criu --version` before retrying `criu check`.


## References:
https://canonical.com/multipass
https://github.com/todoroff/terraform-provider-multipass
https://developer.hashicorp.com/terraform/tutorials/aws-get-started/install-cli
https://kubernetes.io/blog/2026/01/21/introducing-checkpoint-restore-wg/
https://criu.org/Installation