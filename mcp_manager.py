"""
MCP Server Manager
Manages MCP server lifecycle, tool discovery, and execution
"""

import os
import json
import asyncio
import subprocess
import re
from datetime import datetime
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict
from contextlib import asynccontextmanager
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@dataclass
class MCPServerConfig:
    """MCP Server Configuration"""
    name: str
    disabled: bool = False
    timeout: int = 60
    command: str = ""
    args: List[str] = None
    env: Dict[str, str] = None
    transportType: str = "stdio"
    autoApprove: List[str] = None
    
    def __post_init__(self):
        if self.args is None:
            self.args = []
        if self.env is None:
            self.env = {}
        if self.autoApprove is None:
            self.autoApprove = []


class MCPServerManager:
    """Manages MCP servers and their tools"""
    
    def __init__(self, config_file: str = "mcp_servers.json", log_file: str = "data/mcp_logs.json"):
        self.config_file = config_file
        self.log_file = log_file
        self.servers: Dict[str, MCPServerConfig] = {}
        self.active_sessions: Dict[str, ClientSession] = {}
        self.server_tools: Dict[str, List[Dict[str, Any]]] = {}
        self.mcp_logs: List[Dict[str, Any]] = []
        self.load_config()
        self.load_logs()
    
    def load_config(self):
        """Load MCP server configurations from file"""
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r') as f:
                    data = json.load(f)
                    mcp_servers = data.get('mcpServers', {})
                    
                    for name, config in mcp_servers.items():
                        self.servers[name] = MCPServerConfig(
                            name=name,
                            disabled=config.get('disabled', False),
                            timeout=config.get('timeout', 60),
                            command=config.get('command', ''),
                            args=config.get('args', []),
                            env=config.get('env', {}),
                            transportType=config.get('transportType', 'stdio'),
                            autoApprove=config.get('autoApprove', [])
                        )
            except Exception as e:
                print(f"Error loading MCP config: {e}")
    
    def load_logs(self):
        """Load MCP logs from file"""
        if os.path.exists(self.log_file):
            try:
                with open(self.log_file, 'r') as f:
                    self.mcp_logs = json.load(f)
            except Exception as e:
                print(f"Error loading MCP logs: {e}")
                self.mcp_logs = []
    
    def save_logs(self):
        """Save MCP logs to file"""
        try:
            os.makedirs(os.path.dirname(self.log_file), exist_ok=True)
            with open(self.log_file, 'w') as f:
                json.dump(self.mcp_logs, f, indent=2)
        except Exception as e:
            print(f"Error saving MCP logs: {e}")
    
    def truncate_string(self, text: str, max_length: int = 1000) -> str:
        """Truncate string to max length"""
        if len(text) <= max_length:
            return text
        return text[:max_length] + f"... (truncated, {len(text) - max_length} more chars)"
    
    def mask_secrets(self, data: Any) -> Any:
        """Mask secrets in data (tokens, passwords, keys)"""
        if isinstance(data, dict):
            masked = {}
            for key, value in data.items():
                key_lower = key.lower()
                if any(secret in key_lower for secret in ['token', 'password', 'secret', 'key', 'auth', 'credential']):
                    if isinstance(value, str) and len(value) > 8:
                        masked[key] = value[:4] + '****' + value[-4:]
                    else:
                        masked[key] = '****'
                else:
                    masked[key] = self.mask_secrets(value)
            return masked
        elif isinstance(data, list):
            return [self.mask_secrets(item) for item in data]
        elif isinstance(data, str):
            if len(data) > 100 and ('.' in data or data.isalnum()):
                return data[:20] + '****' + data[-20:]
            return data
        else:
            return data
    
    def log_mcp_call(self, server_name: str, tool_name: str, arguments: Dict[str, Any], 
                     result: Dict[str, Any], duration: float, status: str):
        """Log an MCP tool call with truncation and secret masking"""
        log_entry = {
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'type': 'mcp',
            'server': server_name,
            'tool': tool_name,
            'arguments': self.truncate_string(json.dumps(self.mask_secrets(arguments))),
            'result': self.truncate_string(json.dumps(self.mask_secrets(result))),
            'duration': round(duration, 3),
            'status': status
        }
        
        self.mcp_logs.append(log_entry)
        
        if len(self.mcp_logs) > 1000:
            self.mcp_logs = self.mcp_logs[-1000:]
        
        self.save_logs()
    
    def get_logs(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent MCP logs"""
        return self.mcp_logs[-limit:]
    
    def save_config(self):
        """Save MCP server configurations to file"""
        try:
            data = {
                'mcpServers': {
                    name: {
                        'disabled': server.disabled,
                        'timeout': server.timeout,
                        'command': server.command,
                        'args': server.args,
                        'env': server.env,
                        'transportType': server.transportType,
                        'autoApprove': server.autoApprove
                    }
                    for name, server in self.servers.items()
                }
            }
            
            with open(self.config_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"Error saving MCP config: {e}")
            raise
    
    def add_server(self, name: str, config: Dict[str, Any]) -> bool:
        """Add a new MCP server"""
        try:
            self.servers[name] = MCPServerConfig(
                name=name,
                disabled=config.get('disabled', False),
                timeout=config.get('timeout', 60),
                command=config.get('command', ''),
                args=config.get('args', []),
                env=config.get('env', {}),
                transportType=config.get('transportType', 'stdio'),
                autoApprove=config.get('autoApprove', [])
            )
            self.save_config()
            return True
        except Exception as e:
            print(f"Error adding server: {e}")
            return False
    
    def update_server(self, name: str, config: Dict[str, Any]) -> bool:
        """Update an existing MCP server"""
        if name not in self.servers:
            return False
        
        try:
            self.servers[name] = MCPServerConfig(
                name=name,
                disabled=config.get('disabled', False),
                timeout=config.get('timeout', 60),
                command=config.get('command', ''),
                args=config.get('args', []),
                env=config.get('env', {}),
                transportType=config.get('transportType', 'stdio'),
                autoApprove=config.get('autoApprove', [])
            )
            self.save_config()
            return True
        except Exception as e:
            print(f"Error updating server: {e}")
            return False
    
    def delete_server(self, name: str) -> bool:
        """Delete an MCP server"""
        if name in self.servers:
            del self.servers[name]
            if name in self.active_sessions:
                del self.active_sessions[name]
            if name in self.server_tools:
                del self.server_tools[name]
            self.save_config()
            return True
        return False
    
    def get_server(self, name: str) -> Optional[MCPServerConfig]:
        """Get server configuration"""
        return self.servers.get(name)
    
    def list_servers(self) -> List[Dict[str, Any]]:
        """List all servers with their status"""
        return [
            {
                'name': name,
                'disabled': server.disabled,
                'timeout': server.timeout,
                'command': server.command,
                'args': server.args,
                'env': server.env,
                'transportType': server.transportType,
                'autoApprove': server.autoApprove,
                'active': name in self.active_sessions,
                'tools_count': len(self.server_tools.get(name, []))
            }
            for name, server in self.servers.items()
        ]
    
    @asynccontextmanager
    async def connect_to_server(self, name: str):
        """Connect to an MCP server"""
        server = self.servers.get(name)
        if not server or server.disabled:
            raise Exception(f"Server {name} not found or disabled")
        
        env = os.environ.copy()
        env.update(server.env)
        
        server_params = StdioServerParameters(
            command=server.command,
            args=server.args,
            env=env
        )
        
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session
    
    async def discover_tools(self, name: str) -> List[Dict[str, Any]]:
        """Discover tools from an MCP server"""
        try:
            async with self.connect_to_server(name) as session:
                tools_result = await session.list_tools()
                
                tools = []
                for tool in tools_result.tools:
                    tools.append({
                        'name': tool.name,
                        'description': tool.description or '',
                        'inputSchema': tool.inputSchema if hasattr(tool, 'inputSchema') else {}
                    })
                
                self.server_tools[name] = tools
                return tools
        except Exception as e:
            print(f"Error discovering tools for {name}: {e}")
            return []
    
    async def call_tool(self, server_name: str, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Call a tool on an MCP server"""
        start_time = datetime.utcnow()
        try:
            async with self.connect_to_server(server_name) as session:
                result = await session.call_tool(tool_name, arguments)
                
                response = {
                    'success': True,
                    'result': result.content if hasattr(result, 'content') else str(result),
                    'isError': result.isError if hasattr(result, 'isError') else False
                }
                
                duration = (datetime.utcnow() - start_time).total_seconds()
                self.log_mcp_call(server_name, tool_name, arguments, response, duration, 'success')
                
                return response
        except Exception as e:
            error_response = {
                'success': False,
                'error': str(e),
                'isError': True
            }
            
            duration = (datetime.utcnow() - start_time).total_seconds()
            self.log_mcp_call(server_name, tool_name, arguments, error_response, duration, 'error')
            
            return error_response
    
    def get_tools_for_server(self, name: str) -> List[Dict[str, Any]]:
        """Get cached tools for a server"""
        return self.server_tools.get(name, [])
    
    def get_all_tools(self) -> Dict[str, List[Dict[str, Any]]]:
        """Get all tools from all servers"""
        return {
            name: self.get_tools_for_server(name)
            for name in self.servers.keys()
            if not self.servers[name].disabled
        }
    
    def is_tool_auto_approved(self, server_name: str, tool_name: str) -> bool:
        """Check if a tool is auto-approved"""
        server = self.servers.get(server_name)
        if not server:
            return False
        return tool_name in server.autoApprove
    
    def build_openai_tools(self) -> List[Dict[str, Any]]:
        """Build OpenAI tools format from all enabled MCP servers"""
        openai_tools = []
        
        for server_name, server in self.servers.items():
            if server.disabled:
                continue
            
            tools = self.server_tools.get(server_name, [])
            for tool in tools:
                function_name = f"mcp_{server_name}__{tool['name']}"
                
                parameters = tool.get('inputSchema', {'type': 'object', 'properties': {}})
                
                openai_tool = {
                    'type': 'function',
                    'function': {
                        'name': function_name,
                        'description': tool.get('description', f"Tool {tool['name']} from {server_name}"),
                        'parameters': parameters
                    }
                }
                openai_tools.append(openai_tool)
        
        return openai_tools
    
    def parse_tool_function_name(self, function_name: str) -> tuple:
        """Parse server and tool name from OpenAI function name"""
        if not function_name.startswith('mcp_'):
            return None, None
        
        parts = function_name[4:].split('__', 1)
        if len(parts) != 2:
            return None, None
        
        return parts[0], parts[1]


mcp_manager = MCPServerManager()
