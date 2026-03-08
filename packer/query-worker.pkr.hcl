packer {
  required_plugins {
    hcloud = {
      source  = "github.com/hetznercloud/hcloud"
      version = ">= 1.6.0"
    }
  }
}

variable "hcloud_token" {
  type      = string
  sensitive = true
}

variable "location" {
  type    = string
  default = "nbg1"
}

variable "server_type" {
  type    = string
  default = "cpx22"
}

variable "snapshot_name" {
  type    = string
  default = "kolkhis-query-worker"
}

variable "snapshot_labels" {
  type = map(string)
  default = {
    managed_by = "packer"
    role       = "query-worker"
  }
}

source "hcloud" "worker" {
  token       = var.hcloud_token
  image       = "ubuntu-24.04"
  location    = var.location
  server_type = var.server_type
  server_name = "packer-query-worker"

  snapshot_name   = var.snapshot_name
  snapshot_labels = var.snapshot_labels

  ssh_username = "root"
}

build {
  sources = ["source.hcloud.worker"]

  # Create target directories
  provisioner "shell" {
    inline = [
      "mkdir -p /opt/kolkhis-worker",
      "mkdir -p /etc/kolkhis-worker",
    ]
  }

  provisioner "file" {
    source      = "../worker/app.py"
    destination = "/opt/kolkhis-worker/app.py"
  }

  provisioner "file" {
    source      = "../worker/config.py"
    destination = "/opt/kolkhis-worker/config.py"
  }

  provisioner "file" {
    source      = "../worker/executor.py"
    destination = "/opt/kolkhis-worker/executor.py"
  }

  provisioner "file" {
    source      = "../worker/sessions.py"
    destination = "/opt/kolkhis-worker/sessions.py"
  }

  provisioner "file" {
    source      = "../worker/pyproject.toml"
    destination = "/opt/kolkhis-worker/pyproject.toml"
  }

  provisioner "file" {
    source      = "../worker/uv.lock"
    destination = "/opt/kolkhis-worker/uv.lock"
  }

  # Upload systemd service file
  provisioner "file" {
    source      = "files/kolkhis-worker.service"
    destination = "/etc/systemd/system/kolkhis-worker.service"
  }

  # Upload default env file
  provisioner "file" {
    source      = "files/kolkhis-worker.env"
    destination = "/etc/kolkhis-worker/env"
  }

  # Run setup script
  provisioner "shell" {
    script = "scripts/setup.sh"
  }
}
