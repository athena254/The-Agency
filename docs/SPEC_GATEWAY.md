# Nexus Gateway (Butler) Addon — Detailed Specification

## 1. Overview

The Gateway (codename "Butler") is the **front door** to Nexus. It's a **dumb message relay** that connects user interfaces (Telegram, Discord, CLI, VS Code) to the Nexus system.

**What it does:**
- Receives messages from whatever interface the user is using
- Routes them to the right agent(s) inside the Lattice
- Sends back responses through the same interface
- Handles governance — votes, escalations, system healing
- Monitors health — CPU, memory, disk, agent count, lattice status

**What it does NOT do:**
- ❌ No intelligence of its own ("dumb relay")
- ❌ No local state (except config)
- ❌ Doesn't make decisions — agents in the Lattice do that
- ❌ Doesn't store memory — the Lattice does

---

## 2. Three Gateway Modes

### 2.1 Mode 1: Butler Only

```
User → Butler → Domain Agents directly
```

- Butler routes messages straight to domain agents (personal, finance, tech, etc.)
- No Buddy UI agent at all
- Simple text-based responses through the same interface
- Use case: headless servers, API-only, lightweight deployments

**Config:** `mode: "butler-only"`

### 2.2 Mode 2: Butler + Buddy Separate (RECOMMENDED)

```
User → Butler → Lattice → Buddy → Domain Agents
                       ↓
                  WebSocket → Frontend UI
```

- Butler handles auth, rate-limiting, interface routing
- Buddy runs as separate Nexus node in its own process
- Full graphical UI with tool execution, agent routing, visualization
- Clean separation — each can scale independently

**Config:** `mode: "separate"` (default)

### 2.3 Mode 3: Butler + Buddy Merged

```
User → ButlerBuddy (single process) → Domain Agents
              ↓
         WebSocket UI
```

- Single process combines both roles
- Faster (no Lattice hop) but tightly coupled
- Can't upgrade/scale independently
- Only for dev/demo/single-container

**Config:** `mode: "merged"`

---

## 3. Routing Decision Tree (Separate Mode)

```python
async def route_message(message: Message, sender: str) -> None:
    """Route incoming message to correct destination."""
    
    # 1. Check if message explicitly targets Buddy
    if message.target == "buddy" or message.command.startswith("/buddy"):
        await route_to_buddy_conversation(message)
        return
    
    # 2. Check if message targets a specific agent
    if message.target and message.target.startswith("@"):
        await route_to_domain_agent(message)
        return
    
    # 3. Default: route to Buddy conversation
    await route_to_buddy_conversation(message)


async def route_response_to_buddy(message: Message) -> None:
    """Route domain agent response to Buddy for rendering."""
    if message.has_rendering_hint and message.render_via == "buddy":
        # Forward to Buddy via Lattice
        await lattice.send_to_agent("buddy", {
            "type": "domain_render_request",
            "original_message": message,
            "component": message.component,
            "data": message.data,
        })
```

---

## 4. Gateway Agent Core

### 4.1 Responsibilities
```python
class GatewayAgent:
    """Core gateway logic shared across all modes."""
    
    async def receive_message(self, message: Message) -> None:
        """Receive message from any interface adapter."""
        await self._validate_message(message)
        await self._log_message(message)
        await route_message(message, sender=message.source)
    
    async def send_response(self, response: Response, destination: str) -> None:
        """Send response back through the interface."""
        adapter = self._get_adapter(destination)
        await adapter.send(response)
    
    async def check_health(self) -> HealthStatus:
        """Monitor system health."""
        return HealthStatus(
            cpu_percent=psutil.cpu_percent(),
            memory_mb=psutil.virtual_memory().used / 1024 / 1024,
            disk_gb=psutil.disk_usage('/').free / 1024 / 1024 / 1024,
            agent_count=await lattice.get_agent_count(),
            lattice_status=await lattice.get_status(),
        )
    
    async def handle_governance(self, proposal: Proposal) -> None:
        """Handle voting, escalation, and system healing."""
        if proposal.type == "agent_restart":
            await self._restart_agent(proposal.target)
        elif proposal.type == "agent_replace":
            await self._replace_agent(proposal.target, proposal.replacement)
```

### 4.2 Governance Hooks
- **Voting**: Butler participates in governance votes (e.g., "should we restart agent X?")
- **Escalation**: When something breaks, Butler escalates to human or higher authority
- **Healing**: System-wide healing — restart misbehaving agents, vote to replace them

---

## 5. Interface Adapters

### 5.1 Telegram Adapter
```python
class TelegramAdapter:
    """Connect Butler to Telegram."""
    
    async def start(self) -> None:
        """Start polling for Telegram messages."""
        # Use python-telegram-bot or similar
        pass
    
    async def send(self, response: Response) -> None:
        """Send response back to Telegram."""
        pass
    
    async def receive(self) -> AsyncGenerator[Message, None]:
        """Yield incoming messages from Telegram."""
        pass
```

### 5.2 CLI Adapter
```python
class CLIAdapter:
    """Connect Butler to command line interface."""
    
    async def start(self) -> None:
        """Start CLI event loop."""
        pass
    
    async def send(self, response: Response) -> None:
        """Print response to console."""
        print(response.text)
    
    async def receive(self) -> AsyncGenerator[Message, None]:
        """Yield input from stdin."""
        pass
```

### 5.3 VS Code Adapter
```python
class VSCodeAdapter:
    """Connect Butler to VS Code extension."""
    
    async def start(self) -> None:
        """Start WebSocket server for VS Code extension."""
        pass
    
    async def send(self, response: Response) -> None:
        """Send response to VS Code extension via WebSocket."""
        pass
    
    async def receive(self) -> AsyncGenerator[Message, None]:
        """Yield messages from VS Code extension."""
        pass
```

### 5.4 Discord Adapter (Stub)
```python
class DiscordAdapter:
    """Connect Butler to Discord (future)."""
    
    async def start(self) -> None:
        raise NotImplementedError("Discord adapter coming soon")
    
    async def send(self, response: Response) -> None:
        pass
    
    async def receive(self) -> AsyncGenerator[Message, None]:
        pass
```

---

## 6. Gateway Launcher

```python
class GatewayLauncher:
    """Unified entry point — reads config, launches correct mode."""
    
    def __init__(self, config_path: str = "config/gateway_modes.yaml"):
        self._config = self._load_config(config_path)
        self._mode = self._config.get("mode", "separate")
    
    def launch(self) -> None:
        """Launch the configured gateway mode."""
        if self._mode == "butler-only":
            from .butler_only import ButlerOnly
            gateway = ButlerOnly(self._config)
        elif self._mode == "separate":
            from .butler_separate import ButlerSeparate
            gateway = ButlerSeparate(self._config)
        elif self._mode == "merged":
            from .butler_merged import ButlerMerged
            gateway = ButlerMerged(self._config)
        else:
            raise ValueError(f"Unknown mode: {self._mode}")
        
        asyncio.run(gateway.start())
    
    def _load_config(self, path: str) -> dict:
        """Load YAML config or read from env var."""
        config_path = Path(path)
        if config_path.exists():
            with open(config_path) as f:
                return yaml.safe_load(f)
        
        # Fallback to env var
        mode = os.getenv("NEXUS_GATEWAY_MODE", "separate")
        return {"mode": mode}
```

---

## 7. Configuration

```yaml
# config/gateway_modes.yaml
mode: "separate"

modes:
  butler-only:
    description: "Butler routes directly to domain agents, no Buddy UI"
    entry: "python -m nexus.gateways.butler_only"
    
  separate:
    description: "Butler routes to Buddy via Lattice + to domain agents"
    entry: "python -m nexus.gateways.butler_separate"
    buddy_entry: "python -m nexus.gateways.buddy.node"
    butler_port: 3000
    buddy_port: 3002
    lattice_url: "localhost:47113"
    
  merged:
    description: "Single process handles both gateway routing and UI"
    entry: "python -m nexus.gateways.butler_merged"
    port: 3002

# Interface adapters configuration
interfaces:
  telegram:
    enabled: true
    token: "${TELEGRAM_BOT_TOKEN}"
    allowed_chats: ["5981285472"]
    
  cli:
    enabled: true
    
  vscode:
    enabled: false
    websocket_port: 8765
    
  discord:
    enabled: false
    token: "${DISCORD_BOT_TOKEN}"

# Gatekeeper configuration
gatekeeper:
  rate_limit_per_minute: 60
  auth_required: true
  risk_threshold: 0.7

# Health monitoring
health:
  check_interval_seconds: 30
  cpu_threshold: 90
  memory_threshold: 90
  disk_threshold: 90
```

---

## 8. Butler-Only Mode Implementation

```python
class ButlerOnly:
    """Mode 1: Butler routes directly to domain agents."""
    
    def __init__(self, config: dict):
        self._gateway = GatewayAgent(config)
        self._adapters: list[InterfaceAdapter] = []
    
    async def start(self) -> None:
        """Start Butler-only gateway."""
        # Initialize adapters
        if config["interfaces"]["telegram"]["enabled"]:
            self._adapters.append(TelegramAdapter(config))
        if config["interfaces"]["cli"]["enabled"]:
            self._adapters.append(CLIAdapter(config))
        
        # Start all adapters
        await asyncio.gather(*[a.start() for a in self._adapters])
        
        # Process messages
        await self._process_messages()
    
    async def _process_messages(self) -> None:
        """Main message processing loop."""
        while True:
            for adapter in self._adapters:
                async for message in adapter.receive():
                    # Route directly to domain agent
                    response = await self._gateway.route_to_agent(message)
                    await adapter.send(response)
```

---

## 9. Butler-Separate Mode Implementation (Production)

```python
class ButlerSeparate:
    """Mode 2: Butler + Buddy as separate processes (RECOMMENDED)."""
    
    def __init__(self, config: dict):
        self._gateway = GatewayAgent(config)
        self._buddy_url = config["modes"]["separate"]["buddy_url"]
        self._adapters: list[InterfaceAdapter] = []
    
    async def start(self) -> None:
        """Start Butler-separate gateway."""
        # Initialize adapters
        self._init_adapters()
        
        # Start all adapters
        await asyncio.gather(*[a.start() for a in self._adapters])
        
        # Start Buddy as separate subprocess
        buddy_process = await asyncio.create_subprocess_exec(
            "python", "-m", "nexus.gateways.buddy.node",
            "--port", str(self._buddy_port),
        )
        
        # Process messages
        await self._process_messages()
    
    async def _process_messages(self) -> None:
        """Route messages to Buddy or domain agents."""
        while True:
            for adapter in self._adapters:
                async for message in adapter.receive():
                    # Use routing decision tree
                    await route_message(message, adapter.name)
    
    async def route_to_buddy_conversation(self, message: Message) -> None:
        """Send message to Buddy via Lattice."""
        await lattice.send_to_agent("buddy", {
            "type": "user_input",
            "text": message.text,
            "source": message.source,
        })
    
    async def route_to_domain_agent(self, message: Message) -> None:
        """Send message directly to domain agent."""
        agent_id = message.target.lstrip("@")
        response = await lattice.send_and_wait(agent_id, message)
        
        # Check if response should be rendered by Buddy
        if response.render_via == "buddy":
            await self.route_response_to_buddy(response)
        else:
            await adapter.send(Response(text=response.text))
    
    async def route_response_to_buddy(self, response: Response) -> None:
        """Forward domain agent response to Buddy for rendering."""
        await lattice.send_to_agent("buddy", {
            "type": "domain_render_request",
            "component": response.component,
            "data": response.data,
            "text": response.text,
        })
```

---

## 10. Butler-Merged Mode Implementation

```python
class ButlerMerged:
    """Mode 3: Single process combines Butler + Buddy."""
    
    def __init__(self, config: dict):
        self._gateway = GatewayAgent(config)
        self._buddy = SpaceAgentNode(config)  # In-process Buddy
        self._adapters: list[InterfaceAdapter] = []
    
    async def start(self) -> None:
        """Start merged gateway."""
        # Initialize adapters
        self._init_adapters()
        
        # Start adapters
        await asyncio.gather(*[a.start() for a in self._adapters])
        
        # Process messages (no subprocess)
        await self._process_messages()
    
    async def _process_messages(self) -> None:
        """Internal dispatch to Buddy or domain agent."""
        while True:
            for adapter in self._adapters:
                async for message in adapter.receive():
                    if self._should_route_to_buddy(message):
                        # Direct in-process call
                        response = await self._buddy.handle_message(message)
                    else:
                        response = await self._gateway.route_to_agent(message)
                    await adapter.send(response)
```

---

## 11. Advantages Summary

| Advantage | Description |
|-----------|-------------|
| **Separation of Concerns** | Agents: domain expertise; Butler: interface plumbing + governance |
| **Unified Multi-Interface** | One gateway supports all interfaces simultaneously |
| **Centralized Governance** | Gatekeeper handles auth, rate limiting, risk scoring once |
| **Observability** | All traffic passes through one point → complete audit trail |
| **Simplified Agent Code** | Agents focus on domain tasks, not interfaces |
| **Security Boundary** | Butler is the only external attack surface |
| **Graceful Degradation** | If one interface fails, others keep working |

---

## 12. Trade-offs

| Mode | Pros | Cons |
|------|------|------|
| **Butler Only** | Simple, headless, no Buddy dependency | No UI rendering |
| **Separate** | Clean architecture, scalable, Buddy upgradable | Extra hop, 2 processes |
| **Merged** | Fastest, single process | Tightly coupled, not scalable |

---

## 13. Health Monitoring

```python
@dataclass
class HealthStatus:
    cpu_percent: float
    memory_mb: float
    disk_gb: float
    agent_count: int
    lattice_status: str
    timestamp: datetime = field(default_factory=datetime.utcnow)

class HealthMonitor:
    """Continuous health monitoring."""
    
    def __init__(self, interval_seconds: float = 30.0):
        self._interval = interval_seconds
        self._history: list[HealthStatus] = []
    
    async def start(self) -> None:
        """Start monitoring loop."""
        while True:
            status = await self._check()
            self._history.append(status)
            
            # Check thresholds
            if status.cpu_percent > 90:
                await self._alert("CPU usage critical")
            if status.memory_mb > 8000:
                await self._alert("Memory usage critical")
            
            await asyncio.sleep(self._interval)
    
    async def _check(self) -> HealthStatus:
        """Gather current system health."""
        return HealthStatus(
            cpu_percent=psutil.cpu_percent(),
            memory_mb=psutil.virtual_memory().used / 1024 / 1024,
            disk_gb=psutil.disk_usage('/').free / 1024 / 1024 / 1024,
            agent_count=await lattice.get_agent_count(),
            lattice_status=await lattice.get_status(),
        )
```
