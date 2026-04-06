# Tactical Edge Migration Testbed: Infrastructure Provisioning

This directory contains the Infrastructure as Code (IaC) configurations to provision the local experimental baseline.

The Terraform scripts contained herein automatically deploy three isolated identical Ubuntu edge nodes to simulate a distributed Tactical Edge Environment (TEE):
- `edge-node-1` — source / control-plane
- `edge-node-2` — destination worker
- `edge-host-1` — relay / client / proxy node

## The Rationale: Why Multipass over Docker-in-Docker (KinD)?

When evaluating stateful service migration, the choice of local infrastructure dictates the validity of the gathered metrics. While tools like Kubernetes-in-Docker (KinD) or Minikube are industry standards for general orchestration testing, **Multipass (via KVM/QEMU)** was specifically selected for this research methodology for three critical reasons:

1. **Strict Kernel and Namespace Isolation for CRIU:** Container migration relies heavily on Checkpoint/Restore In Userspace (CRIU). CRIU is highly sensitive to nested namespaces and complex cgroup hierarchies. Running Kubernetes nodes as Docker containers (Docker-in-Docker) creates nested environments where CRIU frequently fails to resolve the correct process boundaries. Multipass provisions true lightweight Virtual Machines (VMs) with dedicated, unshared Linux kernels, ensuring a clean namespace hierarchy for baseline container checkpoints.

2. **Precise Network Emulation for Tactical Environments:** A core metric of this thesis is evaluating migration under constrained network conditions (high latency, low bandwidth, intermittent connectivity). Each Multipass VM has a distinct virtual Network Interface Card (vNIC). This allows us to inject precise latency and packet loss directly at the VM interface level using Linux Traffic Control (`tc`) and `netem`, bypassing the unpredictable routing behavior of complex container overlay networks.

3. **Realistic Resource Utilization Metrics:** To accurately measure the CPU and memory spikes during the `dump` and `restore` phases of container and WebAssembly (WASM) migrations, strict hardware boundaries are required. VMs provide rigid resource allocation (e.g., exactly 2 vCPUs and 2GB RAM per node), preventing host-system background noise from skewing the runtime overhead metrics.

## Prerequisites

To replicate this environment, the host machine must have the following installed:
* **Terraform** (v1.0 or newer)
* **Multipass** (v1.13 or newer)


## Usage Instructions

### 1. Initialize the Environment
Initialize Terraform to download the required Canonical Multipass provider plugin.
```bash
terraform init
```
### 2. Provision the Edge Nodes
Apply the configuration to spin up the three virtual nodes.

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
sudo systemctl status node-bootstrap.service
sudo tail -f /var/log/node-bootstrap.log
criu --version
```

### 5. Teardown
To cleanly destroy the experimental baseline and release host resources:
```bash
terraform destroy
```

### 6. Stop/Resume VMs (Without Destroy)
If you want to pause the lab and continue later with the same instances:

```bash
multipass stop edge-node-1 edge-node-2 edge-host-1
```

Resume later:

```bash
multipass start edge-node-1 edge-node-2 edge-host-1
multipass list
```

Quick readiness check after start:

```bash
for n in edge-node-1 edge-node-2 edge-host-1; do
	echo "=== $n ==="
	multipass exec $n -- bash -c '
		systemctl is-active node-bootstrap || true
		criu --version 2>/dev/null | head -1 || true
		sudo podman --version
	'
done
```

### 7. Reset Nodes for a Fresh Migration Run
If you want to keep the VMs but rerun migration from scratch, clear runtime state and checkpoint artifacts:

```bash
for n in edge-node-1 edge-node-2 edge-host-1; do
	echo "=== reset $n ==="
	multipass exec $n -- bash -c '
		sudo podman rm -f counter 2>/dev/null || true
		sudo podman rm -fa 2>/dev/null || true
		sudo podman system reset -f
		sudo rm -f /tmp/counter-checkpoint.tar.zst /home/ubuntu/counter-checkpoint.tar.zst
	'
done

rm -f "$PWD/../counter-checkpoint.tar.zst" 2>/dev/null || true
rm -f "$PWD/../../counter-checkpoint.tar.zst" 2>/dev/null || true
```

This preserves the infrastructure and provisioning work while giving you a clean migration baseline.


### Troubleshooting

#### Error at Terraform Apply
If you got a timeout while launching a node, check whether the instance actually came up and whether the in-guest bootstrap completed:
```bash
multipass list
multipass exec edge-node-1 -- cloud-init status --long
multipass exec edge-node-1 -- sudo systemctl status node-bootstrap.service
multipass exec edge-node-1 -- sudo tail -n 100 /var/log/node-bootstrap.log
```

If a partial instance was created and you want to retry cleanly, run:

```bash
multipass delete edge-node-1 edge-node-2 edge-host-1
multipass purge

terraform apply
```

#### Criu check error
When you enter the node and run `criu check`, if you encounter an error, first confirm the CRIU bootstrap finished successfully. Run:
```bash
sudo systemctl status node-bootstrap.service
sudo tail -f /var/log/node-bootstrap.log
```
to inspect the installation logs inside the VM. If the service completed, verify the binary is present with `criu --version` before retrying `criu check`.



