# Nexus Buddy UI Agent — Detailed Specification

## 1. Overview

Buddy is the **user-facing UI agent** in Nexus. It's a fork of [agent0ai/space-agent](https://github.com/agent0ai/space-agent), adapted to run as a first-class Nexus node. It handles:

1. **Mission Control** (default page) — Domain agent summaries, charts, tables, system status
2. **Buddy Page** — Renders complex visualizations (charts, tables, cards, forms) via component registry

**Key characteristic**: Buddy is a **UI renderer**, not a domain agent. It doesn't have domain expertise — it routes user queries to the right domain agents and renders their responses.

---

## 2. Architecture

### 2.1 SpaceAgentNode

```python
class SpaceAgentNode(BaseAgent):
    """Buddy's core node implementation."""
    
    def __init__(self, config: dict):
        super().__init__(config)
        self._capabilities = {
            "ui_rendering",
            "user_interface",
            "visualization",
            "agent_control",
        }
        self._registry = ComponentRegistry()
        self._router = MessageRouter()
    
    async def handle_message(self, msg: Message) -> None:
        """Handle incoming messages from Butler."""
        if msg.type == "user_input":
            await self._handle_user_input(msg)
        elif msg.type == "domain_render_request":
            await self._handle_domain_render(msg)
        elif msg.type == "tool_call":
            await self._handle_tool_call(msg)
    
    async def _handle_user_input(self, msg: Message) -> None:
        """Handle user conversation messages."""
        # Route to appropriate domain agent
        response = await self._router.route_to_agent(msg)
        
        # Render response
        if response.render_via == "buddy":
            rendered = await self._render_component(response)
            await self._send_to_websocket(rendered)
        else:
            await self._send_to_websocket(Response(text=response.text))
    
    async def _handle_domain_render(self, msg: Message) -> None:
        """Handle domain agent render requests."""
        component = msg.data.get("component")
        data = msg.data.get("data")
        
        rendered = await self._registry.render(component, data)
        
        await self._send_to_websocket({
            "type": "buddy_render",
            "component": component,
            "html": rendered,
            "original_agent": msg.data.get("agent_id"),
        })
```

### 2.2 BuddyAdapter

```python
class BuddyAdapter(NodeInterface):
    """Registers Buddy as a Nexus node."""
    
    def get_capabilities(self) -> set:
        return {
            "ui_rendering",
            "user_interface",
            "visualization",
            "agent_control",
        }
    
    def get_status(self) -> dict:
        return {
            "status": "online",
            "websocket_port": self._config.get("websocket_port", 3002),
            "components_available": self._registry.list_components(),
        }
```

### 2.3 MessageRouter

```python
class MessageRouter:
    """Routes user queries to domain agents via coordinator."""
    
    def __init__(self, coordinator_url: str):
        self._coordinator = ExternalCoordinator(coordinator_url)
    
    async def route_to_agent(self, message: Message) -> Response:
        """Route user query to the right domain agent."""
        # Determine target agent based on message content
        agent_id = await self._detect_target_agent(message)
        
        # Send to coordinator
        response = await self._coordinator.send_and_wait(
            agent_id=agent_id,
            message=message,
            timeout=message.timeout or 30.0,
        )
        
        return response
    
    async def _detect_target_agent(self, message: Message) -> str:
        """Detect which domain agent should handle this query."""
        # Could be: explicit @mention, keyword detection, or coordinator-based routing
        if message.target:
            return message.target.lstrip("@")
        
        # Use coordinator to determine best agent
        return await self._coordinator.detect_agent(message.text)
```

---

## 3. Component Rendering System

### 3.1 ComponentRegistry

```python
class ComponentRegistry:
    """Registry of all available UI renderers."""
    
    def __init__(self):
        self._renderers: dict[str, Callable] = {}
        self._register_defaults()
    
    def register(self, name: str, renderer: Callable) -> None:
        """Register a component renderer."""
        self._renderers[name] = renderer
    
    async def render(self, name: str, data: dict) -> str:
        """Render a component to HTML."""
        renderer = self._renderers.get(name)
        if not renderer:
            return self._fallback_render(name, data)
        try:
            return await renderer(data) if asyncio.iscoroutinefunction(renderer) else renderer(data)
        except Exception as e:
            return self._error_render(name, e)
    
    def list_components(self) -> list[str]:
        """List all registered components."""
        return list(self._renderers.keys())
    
    def _register_defaults(self) -> None:
        """Register all built-in renderers."""
        self.register("chart_bar", render_chart_bar)
        self.register("chart_line", render_chart_line)
        self.register("chart_pie", render_chart_pie)
        self.register("table", render_table)
        self.register("card", render_card)
        self.register("form", render_form)
    
    def _fallback_render(self, name: str, data: dict) -> str:
        """Fallback renderer for unknown components."""
        return f"""
        <div class="buddy-render buddy-unknown">
            <p>Unknown component: {name}</p>
            <pre>{json.dumps(data, indent=2)}</pre>
        </div>
        """
    
    def _error_render(self, name: str, error: Exception) -> str:
        """Error renderer when component fails."""
        return f"""
        <div class="buddy-render buddy-error">
            <p>Error rendering {name}: {str(error)}</pre>
        </div>
        """
```

### 3.2 Built-in Renderers

#### chart_bar — Vertical/Horizontal Bar Charts (SVG)
```python
def render_chart_bar(data: dict) -> str:
    labels = data.get("labels", [])
    values = data.get("values", [])
    orientation = data.get("orientation", "vertical")
    title = data.get("title", "")
    
    # Generate SVG
    if orientation == "horizontal":
        # Horizontal bars
        bars = ""
        for i, (label, value) in enumerate(zip(labels, values)):
            width = (value / max(values)) * 300 if values else 0
            bars += f'<rect x="80" y="{i * 30 + 20}" width="{width}" height="25" fill="steelblue" />'
            bars += f'<text x="75" y="{i * 30 + 37}" text-anchor="end" font-size="12">{label}</text>'
        svg_width = 400
        svg_height = len(labels) * 30 + 40
    else:
        # Vertical bars
        bars = ""
        for i, (label, value) in enumerate(zip(labels, values)):
            height = (value / max(values)) * 200 if values else 0
            x = i * 50 + 50
            bars += f'<rect x="{x}" y="{250 - height}" width="40" height="{height}" fill="steelblue" />'
            bars += f'<text x="{x + 20}" y="270" text-anchor="middle" font-size="10">{label}</text>'
        svg_width = len(labels) * 50 + 50
        svg_height = 300
    
    return f"""
    <div class="buddy-chart buddy-chart-bar">
        <h4>{title}</h4>
        <svg width="{svg_width}" height="{svg_height}">
            {bars}
        </svg>
    </div>
    """
```

#### chart_line — Line Charts (SVG)
```python
def render_chart_line(data: dict) -> str:
    labels = data.get("labels", [])
    values = data.get("values", [])
    title = data.get("title", "")
    
    if not values:
        return "<div>No data</div>"
    
    # Generate polyline points
    points = ""
    for i, value in enumerate(values):
        x = i * 50 + 50
        y = 250 - (value / max(values)) * 200
        points += f"{x},{y} "
    
    return f"""
    <div class="buddy-chart buddy-chart-line">
        <h4>{title}</h4>
        <svg width="{len(values) * 50 + 50}" height="300">
            <polyline points="{points.strip()}" fill="none" stroke="steelblue" stroke-width="2"/>
        </svg>
    </div>
    """
```

#### chart_pie — Pie Charts (CSS conic-gradient)
```python
def render_chart_pie(data: dict) -> str:
    labels = data.get("labels", [])
    values = data.get("values", [])
    title = data.get("title", "")
    
    if not values:
        return "<div>No data</div>"
    
    # Generate conic-gradient
    total = sum(values)
    colors = ["#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f", "#edc948", "#b07aa1", "#ff9da7", "#9c755f", "#bab0ac"]
    gradient = ""
    current_angle = 0
    for i, value in enumerate(values):
        angle = (value / total) * 360
        color = colors[i % len(colors)]
        gradient += f"{color} {current_angle}deg {current_angle + angle}deg, "
        current_angle += angle
    
    return f"""
    <div class="buddy-chart buddy-chart-pie">
        <h4>{title}</h4>
        <div style="width: 200px; height: 200px; border-radius: 50%; 
                    background: conic-gradient({gradient.rstrip(', ')});"></div>
    </div>
    """
```

#### table — HTML Tables
```python
def render_table(data: dict) -> str:
    columns = data.get("columns", [])
    rows = data.get("rows", [])
    title = data.get("title", "")
    
    header = "".join(f"<th>{col}</th>" for col in columns)
    body = ""
    for row in rows:
        body += "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>"
    
    return f"""
    <div class="buddy-table">
        <h4>{title}</h4>
        <table>
            <thead><tr>{header}</tr></thead>
            <tbody>{body}</tbody>
        </table>
    </div>
    """
```

#### card — Info Cards
```python
def render_card(data: dict) -> str:
    title = data.get("title", "")
    content = data.get("content", "")
    subtitle = data.get("subtitle", "")
    metadata = data.get("metadata", {})
    
    meta_html = "".join(f'<span class="meta">{k}: {v}</span>' for k, v in metadata.items())
    
    return f"""
    <div class="buddy-card">
        <h4>{title}</h4>
        {f'<p class="subtitle">{subtitle}</p>' if subtitle else ''}
        <div class="content">{content}</div>
        {meta_html}
    </div>
    """
```

#### form — Interactive Forms
```python
def render_form(data: dict) -> str:
    form_id = data.get("form_id", "default-form")
    fields = data.get("fields", [])
    submit_label = data.get("submit_label", "Submit")
    title = data.get("title", "")
    
    fields_html = ""
    for field in fields:
        field_type = field.get("type", "text")
        field_name = field.get("name", "")
        field_label = field.get("label", field_name)
        required = "required" if field.get("required") else ""
        
        if field_type == "select":
            options = "".join(f'<option value="{opt}">{opt}</option>' for opt in field.get("options", []))
            fields_html += f"""
                <label>{field_label}</label>
                <select name="{field_name}">{options}</select>
            """
        elif field_type == "textarea":
            fields_html += f"""
                <label>{field_label}</label>
                <textarea name="{field_name} {required}"></textarea>
            """
        else:
            fields_html += f"""
                <label>{field_label}</label>
                <input type="{field_type}" name="{field_name}" {required}>
            """
    
    return f"""
    <div class="buddy-form">
        <h4>{title}</h4>
        <form id="{form_id}">
            {fields_html}
            <button type="submit">{submit_label}</button>
        </form>
    </div>
    """
```

---

## 4. Mission Control UI

### 4.1 Two Pages

1. **Mission Control** (default)
   - Domain agent summaries
   - Charts and tables from agent responses
   - System status overview
   - Quick actions

2. **Buddy Page** (floating icon)
   - Complex visualizations
   - Interactive forms
   - Detailed agent renders
   - Domain agent opt-in renders

### 4.2 Page Flow
```
User opens Nexus UI
    ↓
Mission Control (default page)
    ↓
User clicks Buddy floating icon
    ↓
UI switches to Buddy page
    ↓
User asks Buddy something → Butler routes to Buddy node
    ↓
Buddy renders response in Buddy page
```

---

## 5. Domain Agent → Buddy Integration

### 5.1 Render Request Protocol

Domain agents request Buddy rendering via response format:
```python
{
    "text": "Human-readable summary",
    "render_via": "buddy",
    "component": "chart_bar",
    "data": {
        "labels": ["Jan", "Feb", "Mar"],
        "values": [100, 150, 200],
        "title": "Quarterly Spending"
    }
}
```

### 5.2 Routing Logic
```python
# Butler sees render_via="buddy" flag
if response.render_via == "buddy":
    # Forward to Buddy via Lattice (separate mode)
    # or call directly (merged mode)
    await buddy.render(response)
```

---

## 6. Upstream Sync Strategy

### 6.1 Git Subtree Approach

```bash
# Add upstream once
git remote add buddy-upstream https://github.com/agent0ai/space-agent.git

# Pull updates regularly
git subtree pull --prefix=athena/gateways/buddy buddy-upstream main --squash

# Push custom changes back (if needed)
git subtree push --prefix=athena/gateways/buddy buddy-fork main
```

### 6.2 Sync Script
```python
# scripts/sync_buddy.py
import subprocess
from datetime import datetime

def sync_buddy():
    """Pull latest Space-Agent updates and merge into Buddy subtree."""
    
    # Fetch upstream
    subprocess.run(["git", "fetch", "buddy-upstream"], check=True)
    
    # Merge subtree
    result = subprocess.run(
        ["git", "subtree", "pull", "--prefix=athena/gateways/buddy",
         "buddy-upstream", "main", "--squash"],
        capture_output=True,
        text=True,
    )
    
    if result.returncode != 0:
        print(f"Merge conflict! Manual resolution needed: {result.stderr}")
        return False
    
    # Run tests to verify merge didn't break anything
    test_result = subprocess.run(
        ["pytest", "athena/gateways/buddy/tests/", "-v"],
        capture_output=True,
        text=True,
    )
    
    if test_result.returncode != 0:
        print(f"Tests failed after merge! {test_result.stdout}")
        return False
    
    # Commit merge
    subprocess.run(["git", "add", "."], check=True)
    subprocess.run(
        ["git", "commit", "-m", f"Sync Buddy with upstream — {datetime.utcnow().isoformat()}"],
        check=True,
    )
    
    return True
```

---

## 7. WebSocket Server

```python
class BuddyWebSocketServer:
    """WebSocket server connecting browser UI to Buddy's inbox/outbox."""
    
    def __init__(self, host: str = "localhost", port: int = 3002):
        self._host = host
        self._port = port
        self._connections: list = []
    
    async def start(self) -> None:
        """Start WebSocket server."""
        import websockets
        async with websockets.serve(self._handler, self._host, self._port):
            await asyncio.Future()  # Run forever
    
    async def _handler(self, websocket, path):
        """Handle new WebSocket connection."""
        self._connections.append(websocket)
        try:
            async for message in websocket:
                # Parse incoming message
                msg = json.loads(message)
                
                # Route to Buddy's handle_message
                response = await self._node.handle_message(Message(**msg))
                
                # Send response back
                await websocket.send(json.dumps(response.to_dict()))
        finally:
            self._connections.remove(websocket)
    
    async def broadcast(self, message: dict) -> None:
        """Broadcast message to all connected clients."""
        if self._connections:
            await asyncio.gather(
                *[conn.send(json.dumps(message)) for conn in self._connections]
            )
```

---

## 8. Personality System

Buddy's personality is defined in `personality.md` and injected as system prompt:

```markdown
# Buddy — System Prompt

You are **Buddy**, the user interface agent for Nexus.

## Your Role
- Help users understand Nexus system status
- Render complex data into visualizations
- Route user queries to the right domain agents
- Summarize agent responses in user-friendly format

## Your Personality
- Helpful and proactive
- Concise but thorough
- Friendly but professional
- Uses emojis sparingly for clarity

## Your Capabilities
- Rendering charts, tables, cards, forms
- Summarizing multi-agent responses
- Providing system health overviews
- Routing requests to domain agents

## What You Don't Do
- Make domain-specific decisions (agents do that)
- Store long-term memory (Lattice does that)
- Execute dangerous code (sandbox does that)
- Bypass governance rules (gatekeeper does that)
```

---

## 9. Dependencies

```
websockets>=12.0
httpx>=0.27.0
pydantic>=2.0
```

---

## 10. File Structure

```
athena/gateways/buddy/
├── __init__.py                 # Exports
├── node.py                     # SpaceAgentNode + BuddyAdapter
├── personality.md              # System prompt
├── backend/
│   ├── __init__.py
│   └── websocket_server.py     # WebSocket server for browser UI
├── components/
│   ├── __init__.py
│   ├── registry.py             # ComponentRegistry
│   └── renderers.py            # Built-in renderers
├── tests/
│   ├── __init__.py
│   ├── test_node.py
│   ├── test_registry.py
│   └── test_renderers.py
└── docs/
    └── BUDDY_INTEGRATION_SPEC.md
```

---

## 11. Relationship to Space-Agent

| Space-Agent (Upstream) | Buddy (Nexus) |
|------------------------|---------------|
| Standalone Node.js server | Nexus node in Python |
| Direct REST API → UI | Lattice messaging → WebSocket |
| Single agent with tools | Routes to specialized domain agents |
| Own auth/runtime | Uses Nexus Gatekeeper + Coordinator |
| File-based state | Graph database (Lattice) + distributed |

---

## 12. Deployment Modes

### Mode A: Separate Process (RECOMMENDED)
```bash
# Terminal 1: Start Buddy
python -m nexus.gateways.buddy.node --port 3002

# Terminal 2: Start Butler (routes to Buddy via Lattice)
python -m nexus.gateways.butler_separate
```

### Mode B: Merged Process (Dev/Demo)
```bash
# Single process handles both Butler + Buddy
python -m nexus.gateways.butler_merged --port 3002
```
