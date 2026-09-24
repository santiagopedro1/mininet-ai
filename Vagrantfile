Vagrant.configure("2") do |config|
  config.vm.box = "bento/ubuntu-26.04"
  config.vm.box_version = "202606.01.0"
  config.vm.hostname = "mininet-ai"

  config.vm.provider "virtualbox" do |vb|
    vb.memory = 4096
    vb.cpus = 2
  end

  config.vm.provision "shell", privileged: true, inline: <<~'SHELL'
    set -euxo pipefail

    export DEBIAN_FRONTEND=noninteractive
    readonly UV_VERSION="0.12.18"
    readonly VM_ENVIRONMENT="/home/vagrant/.venvs/mininet-ai"

    apt-get update
    apt-get install -y \
      ca-certificates \
      curl \
      iproute2 \
      mininet \
      openvswitch-switch \
      openvswitch-testcontroller \
      python3-venv

    if [ ! -x /usr/local/bin/uv ] || \
        [ "$(/usr/local/bin/uv --version | awk '{print $2}')" != "${UV_VERSION}" ]; then
      curl -LsSf "https://astral.sh/uv/${UV_VERSION}/install.sh" | \
        env UV_UNMANAGED_INSTALL=/usr/local/bin sh
    fi

    install -d -m 0755 -o vagrant -g vagrant \
      /home/vagrant/.cache/uv \
      /home/vagrant/.venvs

    if [ -x "${VM_ENVIRONMENT}/bin/python" ]; then
      runuser -u vagrant -- \
        python3 -m venv --upgrade --system-site-packages "${VM_ENVIRONMENT}"
    else
      runuser -u vagrant -- \
        python3 -m venv --system-site-packages "${VM_ENVIRONMENT}"
    fi

    runuser -u vagrant -- env \
      HOME=/home/vagrant \
      PATH="${VM_ENVIRONMENT}/bin:/usr/local/bin:/usr/bin:/bin" \
      UV_CACHE_DIR=/home/vagrant/.cache/uv \
      UV_PROJECT_ENVIRONMENT="${VM_ENVIRONMENT}" \
      VIRTUAL_ENV="${VM_ENVIRONMENT}" \
      /usr/local/bin/uv sync --active --frozen --project /vagrant

    systemctl enable --now openvswitch-switch

    command -v python3
    command -v mn
    command -v ovs-vsctl
    command -v ovs-ofctl
    command -v ip
    command -v tc

    systemctl is-active --quiet openvswitch-switch
    ovs-vsctl --timeout=5 show >/dev/null

    runuser -u vagrant -- env \
      HOME=/home/vagrant \
      PATH="${VM_ENVIRONMENT}/bin:/usr/local/bin:/usr/bin:/bin" \
      UV_CACHE_DIR=/home/vagrant/.cache/uv \
      UV_PROJECT_ENVIRONMENT="${VM_ENVIRONMENT}" \
      VIRTUAL_ENV="${VM_ENVIRONMENT}" \
      /usr/local/bin/uv run --active --frozen --project /vagrant \
        python -c "import mininet, mininet_ai"
  SHELL
end
