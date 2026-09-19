# Mininet AI

An interactive control interface for Mininet using natural language, powered by [Agno](https://agno.com/) and [Ollama](https://ollama.com/).

This project demonstrates how to use an agentic team to manage and query a simulated software-defined network. Users can interact with the network using simple English commands to perform routing analysis, connectivity tests, and monitor switch statistics.

## Features

- **Natural Language Interface**: Control your Mininet topology using plain English.
- **Agentic Team Coordination**: Uses a Coordinator-Worker pattern to delegate tasks.
- **Path Computation**: Deterministic shortest-path calculation using NetworkX.
- **Real Connectivity Tests**: Execution of actual `ping` commands within the Mininet namespace.
- **Switch Statistics**: Real-time querying of interface statistics (`rx_bytes`, `tx_bytes`) from simulated switches.
- **Local Model Support**: Runs entirely locally using Ollama.

## Architecture

The system uses the Agno framework to build a multi-agent team:

- **Coordinator**: Orchestrates user requests and delegates to specific agents.
- **NetworkOps Agent**: Handles network-wide operations like connectivity tests.
- **Switch Agents**: Each switch (`s1`, `s2`, `s3`) has its own agent that provides local insights and statistics.
- **Routing Tool**: A shared tool for global path computation.

## Prerequisites

- **Python**: 3.14+ (as specified in `pyproject.toml`)
- **Mininet**: Must be installed on the host system.
- **Ollama**: Running locally with the required models.
- **Sudo**: Mininet requires root privileges to create network namespaces.

## Installation

1. **Clone the repository**:

   ```bash
   git clone <repository-url>
   cd mininet-agentic-interactive
   ```

2. **Install dependencies**:
   It is recommended to use `uv` or a virtual environment:

   ```bash
   uv sync
   ```

3. **Configure Ollama**:
   Ensure Ollama is running and download the models specified in `config.py`:
   ```bash
   ollama pull qwen3.5:9b
   ollama pull qwen3.5:2b
   ```

## Configuration

Edit `config.py` to change the Ollama URL or the models used by the agents:

```python
OLLAMA_BASE_URL = "http://localhost:11434"
COORDINATOR_MODEL = "qwen3.5:9b"
WORKER_MODEL = "qwen3.5:2b"
```

## Usage

Run the interactive loop with `sudo` (required by Mininet):

```bash
sudo uv run main.py
```

### Example Commands

- `Set up a network topology with 2 hosts and 3 switches.`
- `Ping from h1 to h2`
- `What are the stats for switch s1?`
- `How can I go from s1 to h2?`
- `Show me the current network topology.`

## Creating a Custom Topology

You can define and provision custom network topologies programmatically using the `tools.net_management` module.

### Example

The following script sets up a diamond switch topology connecting two end hosts (`h1` and `h2`):

```python
from tools.net_management import add_host, add_switch, add_link, deploy_topology

def setup_topo():
    # 1. Define hosts
    add_host("h1")
    add_host("h2")

    # 2. Define switches
    add_switch("s1")
    add_switch("s2")
    add_switch("s3")
    add_switch("s4")

    # 3. Define links
    add_link("h1", "s1")
    add_link("s1", "s2")
    add_link("s1", "s3")
    add_link("s2", "s4")
    add_link("s3", "s4")
    add_link("s4", "h2")

    # 4. Build and deploy
    deploy_topology()

if __name__ == "__main__":
    setup_topo()
```

## Roadmap

- **Import and share topologies**: Let users describe a complete Mininet network, including hosts, switches, and links, in a portable file format such as YAML, then load it with a single command.
- **Evolve into a true framework**: Make it easier for users to integrate custom agents and agent teams with Mininet-AI through clear extension points, reusable components, and straightforward configuration.
- **Integrate skills**: Add support for reusable skills that extend agents with specialized Mininet workflows and domain capabilities.
