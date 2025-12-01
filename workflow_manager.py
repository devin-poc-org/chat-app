import os
import json
import re
import asyncio
import subprocess
import threading
import time
from datetime import datetime
from typing import Dict, List, Any, Optional
from queue import Queue
import uuid


class WorkflowManager:
    """Manages workflow storage, parsing, and execution"""
    
    def __init__(self, workflows_dir='data/workflows', runs_dir='data/workflow_runs', workspaces_dir='data/workspaces'):
        self.workflows_dir = workflows_dir
        self.runs_dir = runs_dir
        self.workspaces_dir = workspaces_dir
        self.active_runs = {}
        
        os.makedirs(self.workflows_dir, exist_ok=True)
        os.makedirs(self.runs_dir, exist_ok=True)
        os.makedirs(self.workspaces_dir, exist_ok=True)
    
    def list_workflows(self) -> List[Dict[str, Any]]:
        """List all available workflows"""
        workflows = []
        
        if not os.path.exists(self.workflows_dir):
            return workflows
        
        for filename in os.listdir(self.workflows_dir):
            if filename.endswith('.md'):
                filepath = os.path.join(self.workflows_dir, filename)
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        content = f.read()
                    
                    params = self._extract_params(content)
                    title = self._extract_title(content)
                    
                    workflows.append({
                        'name': filename,
                        'title': title,
                        'params': params,
                        'enabled': True,
                        'path': filepath
                    })
                except Exception as e:
                    print(f"Error reading workflow {filename}: {e}")
        
        return workflows
    
    def get_workflow(self, name: str) -> Optional[Dict[str, Any]]:
        """Get a specific workflow by name"""
        filepath = os.path.join(self.workflows_dir, name)
        
        if not os.path.exists(filepath):
            return None
        
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
            
            params = self._extract_params(content)
            title = self._extract_title(content)
            
            return {
                'name': name,
                'title': title,
                'content': content,
                'params': params,
                'enabled': True,
                'path': filepath
            }
        except Exception as e:
            print(f"Error reading workflow {name}: {e}")
            return None
    
    def save_workflow(self, name: str, content: str) -> bool:
        """Save a workflow to disk"""
        try:
            if not name.endswith('.md'):
                name = f"{name}.md"
            
            filepath = os.path.join(self.workflows_dir, name)
            
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
            
            return True
        except Exception as e:
            print(f"Error saving workflow {name}: {e}")
            return False
    
    def delete_workflow(self, name: str) -> bool:
        """Delete a workflow"""
        try:
            filepath = os.path.join(self.workflows_dir, name)
            
            if os.path.exists(filepath):
                os.remove(filepath)
                return True
            
            return False
        except Exception as e:
            print(f"Error deleting workflow {name}: {e}")
            return False
    
    def _extract_params(self, content: str) -> Dict[str, Any]:
        """Extract parameter schema from workflow frontmatter"""
        params = {}
        
        frontmatter_match = re.search(r'^---\s*\n(.*?)\n---', content, re.DOTALL | re.MULTILINE)
        if frontmatter_match:
            try:
                import yaml
                frontmatter = yaml.safe_load(frontmatter_match.group(1))
                if isinstance(frontmatter, dict) and 'params' in frontmatter:
                    params = frontmatter['params']
            except:
                pass
        
        return params
    
    def _extract_title(self, content: str) -> str:
        """Extract title from workflow content"""
        lines = content.split('\n')
        for line in lines:
            line = line.strip()
            if line.startswith('# '):
                return line[2:].strip()
        
        return "Untitled Workflow"
    
    def create_run(self, workflow_name: str, params: Dict[str, Any]) -> str:
        """Create a new workflow run"""
        run_id = str(uuid.uuid4())
        
        run_data = {
            'run_id': run_id,
            'workflow_name': workflow_name,
            'params': params,
            'status': 'queued',
            'started_at': datetime.now().isoformat(),
            'finished_at': None,
            'steps': [],
            'logs': [],
            'workspace_dir': os.path.join(self.workspaces_dir, run_id)
        }
        
        os.makedirs(run_data['workspace_dir'], exist_ok=True)
        
        run_file = os.path.join(self.runs_dir, f"{run_id}.json")
        with open(run_file, 'w') as f:
            json.dump(run_data, f, indent=2)
        
        return run_id
    
    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Get run data"""
        run_file = os.path.join(self.runs_dir, f"{run_id}.json")
        
        if not os.path.exists(run_file):
            return None
        
        try:
            with open(run_file, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error reading run {run_id}: {e}")
            return None
    
    def update_run(self, run_id: str, updates: Dict[str, Any]) -> bool:
        """Update run data"""
        run_data = self.get_run(run_id)
        
        if not run_data:
            return False
        
        run_data.update(updates)
        
        run_file = os.path.join(self.runs_dir, f"{run_id}.json")
        try:
            with open(run_file, 'w') as f:
                json.dump(run_data, f, indent=2)
            return True
        except Exception as e:
            print(f"Error updating run {run_id}: {e}")
            return False
    
    def list_runs(self, workflow_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """List all runs, optionally filtered by workflow name"""
        runs = []
        
        if not os.path.exists(self.runs_dir):
            return runs
        
        for filename in os.listdir(self.runs_dir):
            if filename.endswith('.json'):
                try:
                    with open(os.path.join(self.runs_dir, filename), 'r') as f:
                        run_data = json.load(f)
                    
                    if workflow_name is None or run_data.get('workflow_name') == workflow_name:
                        runs.append(run_data)
                except Exception as e:
                    print(f"Error reading run file {filename}: {e}")
        
        runs.sort(key=lambda x: x.get('started_at', ''), reverse=True)
        
        return runs
    
    def parse_workflow(self, content: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Parse workflow markdown into executable steps"""
        content = self._interpolate_params(content, params)
        
        steps = []
        
        bash_blocks = re.finditer(r'```(?:bash|sh)\n(.*?)\n```', content, re.DOTALL)
        for match in bash_blocks:
            command = match.group(1).strip()
            steps.append({
                'type': 'shell',
                'command': command,
                'require_approval': True
            })
        
        mcp_tools = re.finditer(r'<mcp_tool\s+server="([^"]+)"\s+tool="([^"]+)">(.*?)</mcp_tool>', content, re.DOTALL)
        for match in mcp_tools:
            server = match.group(1)
            tool = match.group(2)
            args_str = match.group(3).strip()
            
            try:
                args = json.loads(args_str)
            except:
                args = {}
            
            steps.append({
                'type': 'mcp_tool',
                'server': server,
                'tool': tool,
                'arguments': args,
                'require_approval': True
            })
        
        read_files = re.finditer(r'<read_file>\s*<path>(.*?)</path>\s*</read_file>', content, re.DOTALL)
        for match in read_files:
            path = match.group(1).strip()
            steps.append({
                'type': 'read_file',
                'path': path
            })
        
        search_files = re.finditer(r'<search_files>\s*<path>(.*?)</path>\s*<regex>(.*?)</regex>(?:\s*<file_pattern>(.*?)</file_pattern>)?\s*</search_files>', content, re.DOTALL)
        for match in search_files:
            path = match.group(1).strip()
            regex = match.group(2).strip()
            file_pattern = match.group(3).strip() if match.group(3) else '*'
            
            steps.append({
                'type': 'search_files',
                'path': path,
                'regex': regex,
                'file_pattern': file_pattern
            })
        
        followup_questions = re.finditer(r'<ask_followup_question>\s*<question>(.*?)</question>(?:\s*<options>(.*?)</options>)?\s*</ask_followup_question>', content, re.DOTALL)
        for match in followup_questions:
            question = match.group(1).strip()
            options_str = match.group(2).strip() if match.group(2) else '[]'
            
            try:
                options = json.loads(options_str)
            except:
                options = []
            
            steps.append({
                'type': 'ask_followup_question',
                'question': question,
                'options': options
            })
        
        git_clones = re.finditer(r'<git_clone\s+repo="([^"]+)"(?:\s+branch="([^"]+)")?(?:\s+dir="([^"]+)")?\s*/>', content)
        for match in git_clones:
            repo = match.group(1)
            branch = match.group(2) or 'main'
            dir_name = match.group(3) or repo.split('/')[-1].replace('.git', '')
            
            steps.append({
                'type': 'git_clone',
                'repo': repo,
                'branch': branch,
                'dir': dir_name,
                'require_approval': True
            })
        
        if not steps:
            steps.append({
                'type': 'model_instruction',
                'content': content
            })
        
        return steps
    
    def _interpolate_params(self, content: str, params: Dict[str, Any]) -> str:
        """Interpolate parameters into workflow content"""
        for key, value in params.items():
            placeholder = f"{{{{params.{key}}}}}"
            content = content.replace(placeholder, str(value))
        
        return content


class WorkflowRunner:
    """Executes workflow steps and manages run state"""
    
    def __init__(self, workflow_manager: WorkflowManager, mcp_manager, oca_client, auth_provider):
        self.workflow_manager = workflow_manager
        self.mcp_manager = mcp_manager
        self.oca_client = oca_client
        self.auth_provider = auth_provider
        self.event_queues = {}
        self.approval_events = {}
    
    def start_run(self, run_id: str):
        """Start executing a workflow run in background thread"""
        event_queue = Queue()
        self.event_queues[run_id] = event_queue
        
        thread = threading.Thread(target=self._execute_run, args=(run_id,))
        thread.daemon = True
        thread.start()
    
    def _execute_run(self, run_id: str):
        """Execute workflow run"""
        run_data = self.workflow_manager.get_run(run_id)
        
        if not run_data:
            return
        
        workflow = self.workflow_manager.get_workflow(run_data['workflow_name'])
        
        if not workflow:
            self._emit_event(run_id, {'type': 'error', 'error': 'Workflow not found'})
            return
        
        self._emit_event(run_id, {'type': 'run_started', 'run_id': run_id})
        
        self.workflow_manager.update_run(run_id, {'status': 'running'})
        
        try:
            steps = self.workflow_manager.parse_workflow(workflow['content'], run_data['params'])
            
            for i, step in enumerate(steps):
                self._emit_event(run_id, {
                    'type': 'step_started',
                    'step_index': i,
                    'step_type': step['type'],
                    'step': step
                })
                
                if step.get('require_approval', False):
                    approval_event = threading.Event()
                    self.approval_events[f"{run_id}_{i}"] = {
                        'event': approval_event,
                        'approved': None
                    }
                    
                    self._emit_event(run_id, {
                        'type': 'approval_needed',
                        'step_index': i,
                        'step': step
                    })
                    
                    approval_event.wait(timeout=300)
                    
                    approval_data = self.approval_events.get(f"{run_id}_{i}")
                    if not approval_data or not approval_data.get('approved'):
                        self._emit_event(run_id, {
                            'type': 'step_rejected',
                            'step_index': i
                        })
                        break
                
                try:
                    result = self._execute_step(run_id, step)
                    
                    self._emit_event(run_id, {
                        'type': 'step_succeeded',
                        'step_index': i,
                        'result': result
                    })
                except Exception as e:
                    self._emit_event(run_id, {
                        'type': 'step_failed',
                        'step_index': i,
                        'error': str(e)
                    })
                    self.workflow_manager.update_run(run_id, {
                        'status': 'failed',
                        'finished_at': datetime.now().isoformat()
                    })
                    return
            
            self.workflow_manager.update_run(run_id, {
                'status': 'succeeded',
                'finished_at': datetime.now().isoformat()
            })
            
            self._emit_event(run_id, {'type': 'run_completed', 'status': 'succeeded'})
        
        except Exception as e:
            self._emit_event(run_id, {'type': 'error', 'error': str(e)})
            self.workflow_manager.update_run(run_id, {
                'status': 'failed',
                'finished_at': datetime.now().isoformat()
            })
    
    def _execute_step(self, run_id: str, step: Dict[str, Any]) -> Any:
        """Execute a single workflow step"""
        run_data = self.workflow_manager.get_run(run_id)
        workspace_dir = run_data['workspace_dir']
        
        if step['type'] == 'shell':
            result = subprocess.run(
                step['command'],
                shell=True,
                cwd=workspace_dir,
                capture_output=True,
                text=True,
                timeout=300
            )
            
            self._emit_event(run_id, {
                'type': 'step_log',
                'log': f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}\nReturn code: {result.returncode}"
            })
            
            return {
                'stdout': result.stdout,
                'stderr': result.stderr,
                'returncode': result.returncode
            }
        
        elif step['type'] == 'mcp_tool':
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(
                self.mcp_manager.call_tool(step['server'], step['tool'], step['arguments'])
            )
            loop.close()
            
            self._emit_event(run_id, {
                'type': 'step_log',
                'log': f"MCP Tool Result:\n{json.dumps(result, indent=2)}"
            })
            
            return result
        
        elif step['type'] == 'read_file':
            filepath = os.path.join(workspace_dir, step['path'])
            
            if os.path.exists(filepath):
                with open(filepath, 'r') as f:
                    content = f.read()
                
                self._emit_event(run_id, {
                    'type': 'step_log',
                    'log': f"File content:\n{content}"
                })
                
                return {'content': content}
            else:
                raise FileNotFoundError(f"File not found: {step['path']}")
        
        elif step['type'] == 'search_files':
            import glob
            
            search_path = os.path.join(workspace_dir, step['path'])
            pattern = os.path.join(search_path, step.get('file_pattern', '*'))
            
            matches = []
            for filepath in glob.glob(pattern, recursive=True):
                if os.path.isfile(filepath):
                    with open(filepath, 'r', errors='ignore') as f:
                        content = f.read()
                    
                    if re.search(step['regex'], content):
                        matches.append(filepath)
            
            self._emit_event(run_id, {
                'type': 'step_log',
                'log': f"Found {len(matches)} matching files:\n" + '\n'.join(matches)
            })
            
            return {'matches': matches}
        
        elif step['type'] == 'git_clone':
            clone_dir = os.path.join(workspace_dir, step['dir'])
            
            result = subprocess.run(
                ['git', 'clone', '-b', step['branch'], step['repo'], clone_dir],
                capture_output=True,
                text=True,
                timeout=300
            )
            
            self._emit_event(run_id, {
                'type': 'step_log',
                'log': f"Git clone output:\n{result.stdout}\n{result.stderr}"
            })
            
            return {
                'cloned_to': clone_dir,
                'returncode': result.returncode
            }
        
        elif step['type'] == 'ask_followup_question':
            approval_event = threading.Event()
            approval_key = f"{run_id}_question_{int(time.time())}"
            self.approval_events[approval_key] = {
                'event': approval_event,
                'response': None
            }
            
            self._emit_event(run_id, {
                'type': 'question_asked',
                'question': step['question'],
                'options': step.get('options', []),
                'approval_key': approval_key
            })
            
            approval_event.wait(timeout=300)
            
            approval_data = self.approval_events.get(approval_key)
            response = approval_data.get('response') if approval_data else None
            
            return {'response': response}
        
        elif step['type'] == 'model_instruction':
            access_token = self.auth_provider.get_valid_access_token()
            
            messages = [
                {'role': 'user', 'content': step['content']}
            ]
            
            full_response = ""
            
            for chunk in self.oca_client.chat_completion(
                access_token=access_token,
                messages=messages,
                system_prompt="You are a helpful AI assistant executing a workflow.",
                stream=True
            ):
                if 'choices' in chunk and len(chunk['choices']) > 0:
                    delta = chunk['choices'][0].get('delta', {})
                    content = delta.get('content', '')
                    
                    if content:
                        full_response += content
                        self._emit_event(run_id, {
                            'type': 'step_log',
                            'log': content
                        })
            
            return {'response': full_response}
        
        return {}
    
    def _emit_event(self, run_id: str, event: Dict[str, Any]):
        """Emit event to run's event queue"""
        if run_id in self.event_queues:
            self.event_queues[run_id].put(event)
    
    def get_events(self, run_id: str):
        """Get events from run's event queue (generator for SSE)"""
        if run_id not in self.event_queues:
            return
        
        queue = self.event_queues[run_id]
        
        while True:
            try:
                event = queue.get(timeout=30)
                yield event
                
                if event.get('type') in ['run_completed', 'error']:
                    break
            except:
                yield {'type': 'heartbeat'}
    
    def approve_step(self, run_id: str, step_index: int, approved: bool):
        """Approve or reject a step"""
        approval_key = f"{run_id}_{step_index}"
        
        if approval_key in self.approval_events:
            self.approval_events[approval_key]['approved'] = approved
            self.approval_events[approval_key]['event'].set()
    
    def answer_question(self, approval_key: str, response: str):
        """Answer a followup question"""
        if approval_key in self.approval_events:
            self.approval_events[approval_key]['response'] = response
            self.approval_events[approval_key]['event'].set()
    
    def cancel_run(self, run_id: str):
        """Cancel a running workflow"""
        self.workflow_manager.update_run(run_id, {
            'status': 'cancelled',
            'finished_at': datetime.now().isoformat()
        })
        
        self._emit_event(run_id, {'type': 'run_completed', 'status': 'cancelled'})
