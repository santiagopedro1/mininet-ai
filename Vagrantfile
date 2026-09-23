Vagrant.configure("2") do |config|
  config.vm.box = "bento/ubuntu-26.04"
  config.vm.box_version = "202606.01.0"
  config.vm.hostname = "mininet-ai"

  config.vm.provider "virtualbox" do |vb|
    vb.memory = 4096
    vb.cpus = 2
  end

  config.vm.provision "shell", privileged: true, inline: <<~'SHELL'
    set -eux

    export DEBIAN_FRONTEND=noninteractive

    apt-get update
    apt-get install -y \
      iproute2 \
      mininet \
      openvswitch-switch \
      openvswitch-testcontroller \
      python3-pip \
      python3-venv

    systemctl enable --now openvswitch-switch

    command -v mn
    command -v ovs-vsctl
    command -v ovs-ofctl
    command -v ip
    command -v tc

    systemctl is-active --quiet openvswitch-switch
    ovs-vsctl --timeout=5 show >/dev/null
  SHELL
end