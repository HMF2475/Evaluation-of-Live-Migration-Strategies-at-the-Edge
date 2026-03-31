terraform {
  required_providers {
    multipass = {
      source  = "todoroff/multipass"
      version = "~> 1.1.0"
    }
  }
}

provider "multipass" {
  command_timeout = 1000 
}

resource "multipass_instance" "node1" {
  name           = "edge-node-1"
  image          = "noble"         
  cpus           = 2
  memory         = "2G"
  disk           = "10G"
  cloud_init_file = "${path.module}/cloud-init.yaml" 
}

resource "multipass_instance" "node2" {
  name           = "edge-node-2"
  image          = "noble"        
  cpus           = 2
  memory         = "2G"
  disk           = "10G"
  cloud_init_file = "cloud-init.yaml" 

  depends_on = [multipass_instance.node1]
}