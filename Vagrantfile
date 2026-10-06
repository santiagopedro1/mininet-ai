Vagrant.configure("2") do |config|
  config.vm.box = "bento/fedora-43"
  config.vm.box_version = "202511.16.0"
  config.vm.box_architecture = :auto
  config.vm.hostname = "mininet-ai"

  config.vm.provider "virtualbox" do |vb|
    vb.memory = 4096
    vb.cpus = 2
  end

  config.vm.provision "shell", privileged: true, inline: <<~'SHELL'
    set -euxo pipefail

    readonly UV_VERSION="0.12.18"
    readonly VM_ENVIRONMENT="/home/vagrant/.venvs/mininet-ai"

    dnf install -y \
      ca-certificates \
      curl \
      iproute \
      iproute-tc \
      mininet \
      openvswitch \
      openvswitch-testcontroller \
      iperf \
      iperf3 \
      tcpdump \
      traceroute \
      ethtool \
      bind-utils \
      iputils \
      mtr \
      nmap-ncat

    if [ ! -x /usr/local/bin/uv ] || \
        [ "$(/usr/local/bin/uv --version | awk '{print $2}')" != "${UV_VERSION}" ]; then
      curl -LsSf "https://astral.sh/uv/${UV_VERSION}/install.sh" | \
        env UV_UNMANAGED_INSTALL=/usr/local/bin sh
    fi

    install -d -m 0755 -o vagrant -g vagrant \
      /home/vagrant/.cache \
      /home/vagrant/.cache/uv \
      /home/vagrant/.venvs

    # Keep application Python independent of distro Python and Mininet tooling.
    if [ ! -x "${VM_ENVIRONMENT}/bin/python" ]; then
      runuser -u vagrant -- env HOME=/home/vagrant \
        /usr/local/bin/uv venv --python 3.14 --managed-python "${VM_ENVIRONMENT}"
    fi

    runuser -u vagrant -- env \
      HOME=/home/vagrant \
      PATH="${VM_ENVIRONMENT}/bin:/usr/local/bin:/usr/bin:/bin" \
      UV_CACHE_DIR=/home/vagrant/.cache/uv \
      UV_PROJECT_ENVIRONMENT="${VM_ENVIRONMENT}" \
      VIRTUAL_ENV="${VM_ENVIRONMENT}" \
      /usr/local/bin/uv sync --active --frozen --project /vagrant

    systemctl enable --now openvswitch

    for tool in mn mnexec ovs-vsctl ovs-ofctl ovs-testcontroller ip tc \
        iperf iperf3 tcpdump traceroute ethtool dig ss ping mtr nc; do
      command -v "${tool}"
    done

    systemctl is-active --quiet openvswitch
    ovs-vsctl --timeout=5 show >/dev/null

    runuser -u vagrant -- env \
      HOME=/home/vagrant \
      PATH="${VM_ENVIRONMENT}/bin:/usr/local/bin:/usr/bin:/bin" \
      UV_CACHE_DIR=/home/vagrant/.cache/uv \
      UV_PROJECT_ENVIRONMENT="${VM_ENVIRONMENT}" \
      VIRTUAL_ENV="${VM_ENVIRONMENT}" \
      /usr/local/bin/uv run --active --frozen --project /vagrant \
        python -c "import sys, mininet, mininet_ai; assert sys.version_info >= (3, 14)"
  SHELL
end
