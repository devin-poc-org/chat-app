"""
OCA Chat Application
A simple web-based chat interface for Oracle Code Assist
"""

import os
import json
import webbrowser
import threading
import time
import uuid
import asyncio
import subprocess
import queue
from datetime import datetime
from typing import Dict, Any, List, Optional
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, Response
from flask_cors import CORS
from dotenv import load_dotenv
from oca_auth import OCAAuthProvider
from oca_client_openai import OCAClient
from loopback_server import LoopbackServer
from file_handler import FileHandler
from mcp_manager import mcp_manager
from workflow_manager import WorkflowManager, WorkflowRunner

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'change-this-secret-key')
CORS(app)

OCA_CONFIG = {
    'internal': {
        'client_id': os.getenv('INTERNAL_IDCS_CLIENT_ID', 'a8331954c0cf48ba99b5dd223a14c6ea'),
        'idcs_url': os.getenv('INTERNAL_IDCS_URL', 'https://idcs-9dc693e80d9b469480d7afe00e743931.identity.oraclecloud.com'),
        'scopes': os.getenv('INTERNAL_IDCS_SCOPES', 'openid offline_access')
    },
    'external': {
        'client_id': os.getenv('EXTERNAL_IDCS_CLIENT_ID', 'c1aba3deed5740659981a752714eba33'),
        'idcs_url': os.getenv('EXTERNAL_IDCS_URL', 'https://login-ext.identity.oraclecloud.com'),
        'scopes': os.getenv('EXTERNAL_IDCS_SCOPES', 'openid offline_access')
    }
}

DEFAULT_OCA_MODE = os.getenv('OCA_MODE', 'internal')
DEFAULT_INTERNAL_BASE_URL = os.getenv('INTERNAL_OCA_BASE_URL', 'https://code-internal.aiservice.us-chicago-1.oci.oraclecloud.com/20250206/app/litellm')
DEFAULT_EXTERNAL_BASE_URL = os.getenv('EXTERNAL_OCA_BASE_URL', 'https://code.aiservice.us-chicago-1.oci.oraclecloud.com/20250206/app/litellm')
DEFAULT_MODEL_ID = os.getenv('OCA_MODEL_ID', 'oca/gpt-4.1')

auth_provider = OCAAuthProvider(OCA_CONFIG)

runtime_settings = {
    'oca_mode': DEFAULT_OCA_MODE,
    'oca_base_url': DEFAULT_INTERNAL_BASE_URL if DEFAULT_OCA_MODE == 'internal' else DEFAULT_EXTERNAL_BASE_URL,
    'model_id': DEFAULT_MODEL_ID
}

workflow_manager = WorkflowManager()
workflow_runner = None

def get_oca_client():
    """Get OCA client with current runtime settings"""
    return OCAClient(runtime_settings['oca_base_url'], runtime_settings['model_id'])

conversations: Dict[str, Dict[str, Any]] = {}
api_logs: List[Dict[str, Any]] = []

loopback_server = None
auth_in_progress = False

file_handler = FileHandler()

terminal_process = None
terminal_output_queue = queue.Queue()

def add_api_log(log_type: str, data: Dict[str, Any]):
    """Add entry to API logs"""
    log_entry = {
        'id': str(uuid.uuid4()),
        'timestamp': datetime.now().isoformat(),
        'type': log_type,
        'data': data
    }
    api_logs.append(log_entry)
    if len(api_logs) > 1000:
        api_logs.pop(0)


def create_conversation(name: Optional[str] = None) -> str:
    """Create a new conversation"""
    conv_id = str(uuid.uuid4())
    conversations[conv_id] = {
        'id': conv_id,
        'name': name or f"Chat {len(conversations) + 1}",
        'created_at': datetime.now().isoformat(),
        'messages': [],
        'file_context': []
    }
    return conv_id


def retry_with_backoff(func, max_retries=3, initial_delay=1):
    """Retry function with exponential backoff for 5xx errors and timeouts"""
    for attempt in range(max_retries):
        try:
            return func()
        except Exception as e:
            error_str = str(e).lower()
            is_retryable = (
                '5' in error_str and ('50' in error_str or '51' in error_str or '52' in error_str or '53' in error_str) or
                'timeout' in error_str or
                'connection' in error_str
            )
            
            if not is_retryable or attempt == max_retries - 1:
                raise
            
            delay = initial_delay * (2 ** attempt)
            add_api_log('retry', {
                'attempt': attempt + 1,
                'max_retries': max_retries,
                'delay': delay,
                'error': str(e)
            })
            time.sleep(delay)


@app.route('/')
def index():
    """Main chat interface"""
    access_token = auth_provider.get_valid_access_token()
    user_info = auth_provider.get_user_info()
    
    if access_token and len(conversations) == 0:
        create_conversation("Default Chat")
    
    return render_template('index.html', 
                         authenticated=access_token is not None,
                         user_info=user_info,
                         oca_mode=runtime_settings['oca_mode'],
                         model_id=runtime_settings['model_id'],
                         oca_base_url=runtime_settings['oca_base_url'])


@app.route('/api/auth/status')
def auth_status():
    """Check authentication status"""
    access_token = auth_provider.get_valid_access_token()
    user_info = auth_provider.get_user_info()
    
    return jsonify({
        'authenticated': access_token is not None,
        'user_info': user_info
    })


@app.route('/api/auth/login')
def login():
    """Initiate OAuth2 login flow with loopback server"""
    global loopback_server, auth_in_progress
    
    if auth_in_progress:
        return jsonify({'error': 'Authentication already in progress'}), 400
    
    mode = request.args.get('mode', DEFAULT_OCA_MODE)
    model_id = request.args.get('model_id', DEFAULT_MODEL_ID)
    base_url = request.args.get('base_url', '')
    
    runtime_settings['oca_mode'] = mode
    runtime_settings['model_id'] = model_id
    
    if base_url:
        runtime_settings['oca_base_url'] = base_url
    else:
        runtime_settings['oca_base_url'] = DEFAULT_INTERNAL_BASE_URL if mode == 'internal' else DEFAULT_EXTERNAL_BASE_URL
    
    try:
        auth_in_progress = True
        
        def handle_oauth_callback(code: str, state: str) -> Dict[str, Any]:
            global loopback_server, auth_in_progress
            try:
                result = auth_provider.exchange_code_for_tokens(code, state, mode)
                
                if loopback_server:
                    threading.Timer(2.0, loopback_server.stop).start()
                
                auth_in_progress = False
                return {'success': True, 'result': result}
            except Exception as e:
                auth_in_progress = False
                if loopback_server:
                    threading.Timer(2.0, loopback_server.stop).start()
                return {'success': False, 'error': str(e)}
        
        loopback_server = LoopbackServer(
            callback_handler=handle_oauth_callback,
            success_redirect_url='http://localhost:5000'
        )
        callback_base = loopback_server.start()
        callback_url = f"{callback_base}/auth/oca"
        
        auth_url = auth_provider.get_auth_url(callback_url, mode)
        
        threading.Thread(target=lambda: webbrowser.open(str(auth_url)), daemon=True).start()
        
        return jsonify({
            'success': True,
            'auth_url': str(auth_url),
            'message': 'Opening browser for authentication...'
        })
        
    except Exception as e:
        auth_in_progress = False
        if loopback_server:
            loopback_server.stop()
            loopback_server = None
        return jsonify({'error': str(e)}), 500


@app.route('/api/models', methods=['GET'])
def list_models():
    """List available OCA models"""
    access_token = auth_provider.get_valid_access_token()
    if not access_token:
        return jsonify({'error': 'Not authenticated'}), 401
    
    try:
        oca_client = get_oca_client()
        models = oca_client.list_models(access_token)
        return jsonify({'models': models})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/settings', methods=['POST'])
def update_settings():
    """Update runtime settings (model ID and base URL)"""
    access_token = auth_provider.get_valid_access_token()
    if not access_token:
        return jsonify({'error': 'Not authenticated'}), 401
    
    model_id = request.args.get('model_id', '')
    base_url = request.args.get('base_url', '')
    
    if model_id:
        runtime_settings['model_id'] = model_id
    
    if base_url:
        runtime_settings['oca_base_url'] = base_url
    
    return jsonify({
        'success': True,
        'settings': runtime_settings
    })


@app.route('/api/auth/logout', methods=['POST'])
def logout():
    """Logout and clear tokens"""
    auth_provider.clear_tokens()
    session.clear()
    return jsonify({'success': True})


@app.route('/api/chat', methods=['POST'])
def chat():
    """Send a chat message and get response"""
    access_token = auth_provider.get_valid_access_token()
    if not access_token:
        return jsonify({'error': 'Not authenticated'}), 401
    
    data = request.json
    user_message = data.get('message', '').strip()
    conversation_id = data.get('conversation_id')
    
    if not user_message:
        return jsonify({'error': 'Message is required'}), 400
    
    if not conversation_id or conversation_id not in conversations:
        return jsonify({'error': 'Invalid conversation ID'}), 400
    
    conversation = conversations[conversation_id]
    messages = conversation['messages']
    file_context = conversation.get('file_context', [])
    
    messages.append({
        'role': 'user',
        'content': user_message,
        'timestamp': datetime.now().isoformat()
    })
    
    def generate():
        try:
            full_response = ""
            usage_data = None
            opc_request_id = None
            user_info = auth_provider.get_user_info()
            user_email = user_info.get('email') if user_info else None
            
            oca_client = get_oca_client()
            
            messages_to_send = messages.copy()
            
            if file_context:
                context_message = "Files in context:\n\n" + "\n\n".join(file_context)
                messages_to_send.insert(0, {
                    'role': 'system',
                    'content': context_message
                })
            
            add_api_log('chat_request', {
                'conversation_id': conversation_id,
                'message_count': len(messages_to_send),
                'has_file_context': len(file_context) > 0
            })
            
            mcp_tools = mcp_manager.build_openai_tools()
            
            if mcp_tools:
                try:
                    def make_tool_detection_request():
                        return oca_client.chat_completion(
                            access_token=access_token,
                            messages=messages_to_send,
                            system_prompt="You are a helpful AI assistant.",
                            stream=False,
                            user_email=user_email,
                            tools=mcp_tools,
                            tool_choice="auto"
                        )
                    
                    tool_detection_chunks = list(retry_with_backoff(make_tool_detection_request, max_retries=3))
                    if tool_detection_chunks:
                        tool_response = tool_detection_chunks[0]
                        
                        if 'opc_request_id' in tool_response:
                            opc_request_id = tool_response['opc_request_id']
                            yield f"data: {json.dumps({'type': 'opc_request_id', 'opc_request_id': opc_request_id})}\n\n"
                        
                        if 'choices' in tool_response and len(tool_response['choices']) > 0:
                            message = tool_response['choices'][0].get('message', {})
                            tool_calls = message.get('tool_calls', [])
                            
                            if tool_calls:
                                for tool_call in tool_calls:
                                    function = tool_call.get('function', {})
                                    function_name = function.get('name', '')
                                    arguments_str = function.get('arguments', '{}')
                                    tool_call_id = tool_call.get('id', '')
                                    
                                    server_name, tool_name = mcp_manager.parse_tool_function_name(function_name)
                                    
                                    if server_name and tool_name:
                                        server_tools = mcp_manager.get_tools_for_server(server_name)
                                        tool_desc = next((t.get('description', '') for t in server_tools if t['name'] == tool_name), '')
                                        
                                        try:
                                            arguments = json.loads(arguments_str)
                                        except:
                                            arguments = {}
                                        
                                        tool_request_data = {
                                            'type': 'tool_request',
                                            'server_name': server_name,
                                            'tool_name': tool_name,
                                            'function_name': function_name,
                                            'tool_call_id': tool_call_id,
                                            'description': tool_desc,
                                            'arguments': arguments
                                        }
                                        yield f"data: {json.dumps(tool_request_data)}\n\n"
                                        
                                        return
                except Exception as e:
                    print(f"Tool detection failed: {e}")
            
            def make_request():
                return oca_client.chat_completion(
                    access_token=access_token,
                    messages=messages_to_send,
                    system_prompt="You are a helpful AI assistant.",
                    stream=True,
                    user_email=user_email
                )
            
            for chunk in retry_with_backoff(make_request, max_retries=3):
                if 'opc_request_id' in chunk:
                    opc_request_id = chunk['opc_request_id']
                    yield f"data: {json.dumps({'type': 'opc_request_id', 'opc_request_id': opc_request_id})}\n\n"
                
                if 'choices' in chunk and len(chunk['choices']) > 0:
                    delta = chunk['choices'][0].get('delta', {})
                    content = delta.get('content', '')
                    
                    if content:
                        full_response += content
                        yield f"data: {json.dumps({'type': 'content', 'content': content})}\n\n"
                
                if 'usage' in chunk:
                    usage_data = chunk['usage']
            
            messages.append({
                'role': 'assistant',
                'content': full_response,
                'timestamp': datetime.now().isoformat()
            })
            
            add_api_log('chat_response', {
                'conversation_id': conversation_id,
                'response_length': len(full_response),
                'usage': usage_data,
                'opc_request_id': opc_request_id
            })
            
            completion_data = {
                'type': 'done',
                'usage': usage_data,
                'opc_request_id': opc_request_id
            }
            yield f"data: {json.dumps(completion_data)}\n\n"
            
        except Exception as e:
            add_api_log('chat_error', {
                'conversation_id': conversation_id,
                'error': str(e)
            })
            error_data = {
                'type': 'error',
                'error': str(e)
            }
            yield f"data: {json.dumps(error_data)}\n\n"
    
    return Response(generate(), mimetype='text/event-stream')


@app.route('/api/conversations', methods=['GET'])
def list_conversations():
    """List all conversations"""
    access_token = auth_provider.get_valid_access_token()
    if not access_token:
        return jsonify({'error': 'Not authenticated'}), 401
    
    conv_list = [
        {
            'id': conv['id'],
            'name': conv['name'],
            'created_at': conv['created_at'],
            'message_count': len(conv['messages'])
        }
        for conv in conversations.values()
    ]
    return jsonify({'conversations': conv_list})


@app.route('/api/conversations', methods=['POST'])
def create_new_conversation():
    """Create a new conversation"""
    access_token = auth_provider.get_valid_access_token()
    if not access_token:
        return jsonify({'error': 'Not authenticated'}), 401
    
    data = request.json or {}
    name = data.get('name')
    conv_id = create_conversation(name)
    
    return jsonify({
        'success': True,
        'conversation': conversations[conv_id]
    })


@app.route('/api/conversations/<conversation_id>', methods=['GET'])
def get_conversation(conversation_id):
    """Get conversation history"""
    access_token = auth_provider.get_valid_access_token()
    if not access_token:
        return jsonify({'error': 'Not authenticated'}), 401
    
    conversation = conversations.get(conversation_id)
    if not conversation:
        return jsonify({'error': 'Conversation not found'}), 404
    
    return jsonify({'conversation': conversation})


@app.route('/api/conversations/<conversation_id>', methods=['PUT'])
def update_conversation(conversation_id):
    """Update conversation name"""
    access_token = auth_provider.get_valid_access_token()
    if not access_token:
        return jsonify({'error': 'Not authenticated'}), 401
    
    if conversation_id not in conversations:
        return jsonify({'error': 'Conversation not found'}), 404
    
    data = request.json or {}
    name = data.get('name')
    
    if name:
        conversations[conversation_id]['name'] = name
    
    return jsonify({'success': True, 'conversation': conversations[conversation_id]})


@app.route('/api/conversations/<conversation_id>', methods=['DELETE'])
def delete_conversation(conversation_id):
    """Delete conversation"""
    access_token = auth_provider.get_valid_access_token()
    if not access_token:
        return jsonify({'error': 'Not authenticated'}), 401
    
    if conversation_id in conversations:
        del conversations[conversation_id]
    
    return jsonify({'success': True})


@app.route('/api/conversations/<conversation_id>/clear', methods=['POST'])
def clear_conversation_messages(conversation_id):
    """Clear conversation messages but keep the conversation"""
    access_token = auth_provider.get_valid_access_token()
    if not access_token:
        return jsonify({'error': 'Not authenticated'}), 401
    
    if conversation_id not in conversations:
        return jsonify({'error': 'Conversation not found'}), 404
    
    conversations[conversation_id]['messages'] = []
    conversations[conversation_id]['file_context'] = []
    
    return jsonify({'success': True})


@app.route('/api/upload', methods=['POST'])
def upload_file():
    """Upload and process file"""
    access_token = auth_provider.get_valid_access_token()
    if not access_token:
        return jsonify({'error': 'Not authenticated'}), 401
    
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    
    file = request.files['file']
    conversation_id = request.form.get('conversation_id')
    
    if not file.filename:
        return jsonify({'error': 'No file selected'}), 400
    
    if not conversation_id or conversation_id not in conversations:
        return jsonify({'error': 'Invalid conversation ID'}), 400
    
    try:
        file_data = file.read()
        
        is_valid, error_msg = file_handler.validate_file(file_data, file.filename)
        if not is_valid:
            return jsonify({'error': error_msg}), 400
        
        text_content, file_type = file_handler.extract_text_from_file(file_data, file.filename)
        
        formatted_content = file_handler.format_file_content_for_model(file.filename, text_content)
        conversations[conversation_id]['file_context'].append(formatted_content)
        
        preview = file_handler.format_file_content_for_chat(file.filename, file_type, text_content)
        
        add_api_log('file_upload', {
            'conversation_id': conversation_id,
            'filename': file.filename,
            'file_type': file_type,
            'size': len(file_data),
            'text_length': len(text_content)
        })
        
        return jsonify({
            'success': True,
            'filename': file.filename,
            'file_type': file_type,
            'preview': preview,
            'text_length': len(text_content)
        })
    
    except Exception as e:
        add_api_log('file_upload_error', {
            'conversation_id': conversation_id,
            'filename': file.filename,
            'error': str(e)
        })
        return jsonify({'error': str(e)}), 500


@app.route('/api/logs', methods=['GET'])
def get_logs():
    """Get API logs including MCP logs"""
    access_token = auth_provider.get_valid_access_token()
    if not access_token:
        return jsonify({'error': 'Not authenticated'}), 401
    
    limit = request.args.get('limit', 100, type=int)
    log_type = request.args.get('type')
    
    mcp_logs = mcp_manager.get_logs(limit=limit)
    all_logs = api_logs + mcp_logs
    
    all_logs.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
    
    if log_type:
        all_logs = [log for log in all_logs if log['type'] == log_type]
    
    return jsonify({'logs': all_logs[-limit:]})


@app.route('/api/logs', methods=['DELETE'])
def clear_logs():
    """Clear API logs and MCP logs"""
    access_token = auth_provider.get_valid_access_token()
    if not access_token:
        return jsonify({'error': 'Not authenticated'}), 401
    
    api_logs.clear()
    mcp_manager.mcp_logs.clear()
    mcp_manager.save_logs()
    return jsonify({'success': True})


@app.route('/api/mcp/servers', methods=['GET'])
def list_mcp_servers():
    """List all MCP servers"""
    access_token = auth_provider.get_valid_access_token()
    if not access_token:
        return jsonify({'error': 'Not authenticated'}), 401
    
    try:
        servers = mcp_manager.list_servers()
        return jsonify({'servers': servers})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/mcp/servers', methods=['POST'])
def add_mcp_server():
    """Add a new MCP server"""
    access_token = auth_provider.get_valid_access_token()
    if not access_token:
        return jsonify({'error': 'Not authenticated'}), 401
    
    data = request.json
    name = data.get('name')
    config = data.get('config', {})
    
    if not name:
        return jsonify({'error': 'Server name is required'}), 400
    
    try:
        success = mcp_manager.add_server(name, config)
        if success:
            return jsonify({'success': True, 'message': 'Server added successfully'})
        else:
            return jsonify({'error': 'Failed to add server'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/mcp/servers/<server_name>', methods=['PUT'])
def update_mcp_server(server_name):
    """Update an MCP server"""
    access_token = auth_provider.get_valid_access_token()
    if not access_token:
        return jsonify({'error': 'Not authenticated'}), 401
    
    data = request.json
    config = data.get('config', {})
    
    try:
        success = mcp_manager.update_server(server_name, config)
        if success:
            return jsonify({'success': True, 'message': 'Server updated successfully'})
        else:
            return jsonify({'error': 'Server not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/mcp/servers/<server_name>', methods=['DELETE'])
def delete_mcp_server(server_name):
    """Delete an MCP server"""
    access_token = auth_provider.get_valid_access_token()
    if not access_token:
        return jsonify({'error': 'Not authenticated'}), 401
    
    try:
        success = mcp_manager.delete_server(server_name)
        if success:
            return jsonify({'success': True, 'message': 'Server deleted successfully'})
        else:
            return jsonify({'error': 'Server not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/mcp/servers/import', methods=['POST'])
def import_mcp_server():
    """Import/update an MCP server from JSON config"""
    access_token = auth_provider.get_valid_access_token()
    if not access_token:
        return jsonify({'error': 'Not authenticated'}), 401
    
    data = request.json
    name = data.get('name')
    config = data.get('config', {})
    
    if not name:
        return jsonify({'error': 'Server name is required'}), 400
    
    try:
        existing_servers = mcp_manager.list_servers()
        server_exists = name in existing_servers
        
        if server_exists:
            success = mcp_manager.update_server(name, config)
            if success:
                return jsonify({'success': True, 'message': f'Server "{name}" updated successfully'})
            else:
                return jsonify({'error': f'Failed to update server "{name}"'}), 500
        else:
            success = mcp_manager.add_server(name, config)
            if success:
                return jsonify({'success': True, 'message': f'Server "{name}" added successfully'})
            else:
                return jsonify({'error': f'Failed to add server "{name}"'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/mcp/servers/<server_name>/tools', methods=['GET'])
def get_mcp_server_tools(server_name):
    """Get tools for an MCP server"""
    access_token = auth_provider.get_valid_access_token()
    if not access_token:
        return jsonify({'error': 'Not authenticated'}), 401
    
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        tools = loop.run_until_complete(mcp_manager.discover_tools(server_name))
        loop.close()
        
        return jsonify({'tools': tools})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/mcp/servers/<server_name>/tools/<tool_name>', methods=['POST'])
def call_mcp_tool(server_name, tool_name):
    """Call an MCP tool"""
    access_token = auth_provider.get_valid_access_token()
    if not access_token:
        return jsonify({'error': 'Not authenticated'}), 401
    
    data = request.json
    arguments = data.get('arguments', {})
    
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(mcp_manager.call_tool(server_name, tool_name, arguments))
        loop.close()
        
        add_api_log('mcp_tool_call', {
            'server': server_name,
            'tool': tool_name,
            'arguments': arguments,
            'result': result
        })
        
        return jsonify(result)
    except Exception as e:
        add_api_log('mcp_tool_error', {
            'server': server_name,
            'tool': tool_name,
            'error': str(e)
        })
        return jsonify({'error': str(e)}), 500


@app.route('/api/mcp/tools', methods=['GET'])
def get_all_mcp_tools():
    """Get all tools from all MCP servers"""
    access_token = auth_provider.get_valid_access_token()
    if not access_token:
        return jsonify({'error': 'Not authenticated'}), 401
    
    try:
        all_tools = mcp_manager.get_all_tools()
        return jsonify({'tools': all_tools})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/chat/continue-tool', methods=['POST'])
def continue_tool():
    """Continue chat after tool execution"""
    access_token = auth_provider.get_valid_access_token()
    if not access_token:
        return jsonify({'error': 'Not authenticated'}), 401
    
    data = request.json
    conversation_id = data.get('conversation_id')
    server_name = data.get('server_name')
    tool_name = data.get('tool_name')
    tool_call_id = data.get('tool_call_id')
    function_name = data.get('function_name')
    arguments = data.get('arguments', {})
    
    if not all([conversation_id, server_name, tool_name, tool_call_id, function_name]):
        return jsonify({'error': 'Missing required parameters'}), 400
    
    if conversation_id not in conversations:
        return jsonify({'error': 'Invalid conversation ID'}), 400
    
    conversation = conversations[conversation_id]
    messages = conversation['messages']
    file_context = conversation.get('file_context', [])
    
    def generate():
        try:
            user_info = auth_provider.get_user_info()
            user_email = user_info.get('email') if user_info else None
            oca_client = get_oca_client()
            
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            tool_result = loop.run_until_complete(mcp_manager.call_tool(server_name, tool_name, arguments))
            loop.close()
            
            add_api_log('mcp_tool_call', {
                'conversation_id': conversation_id,
                'server': server_name,
                'tool': tool_name,
                'arguments': arguments,
                'success': tool_result.get('success', False)
            })
            
            tool_result_content = json.dumps(tool_result.get('result', tool_result.get('error', 'Unknown error')))
            
            messages.append({
                'role': 'tool',
                'content': tool_result_content,
                'tool_call_id': tool_call_id,
                'name': function_name
            })
            
            messages_to_send = messages.copy()
            
            if file_context:
                context_message = "Files in context:\n\n" + "\n\n".join(file_context)
                messages_to_send.insert(0, {
                    'role': 'system',
                    'content': context_message
                })
            
            full_response = ""
            usage_data = None
            opc_request_id = None
            
            def make_request():
                return oca_client.chat_completion(
                    access_token=access_token,
                    messages=messages_to_send,
                    system_prompt="You are a helpful AI assistant.",
                    stream=True,
                    user_email=user_email
                )
            
            for chunk in retry_with_backoff(make_request, max_retries=3):
                if 'opc_request_id' in chunk:
                    opc_request_id = chunk['opc_request_id']
                    yield f"data: {json.dumps({'type': 'opc_request_id', 'opc_request_id': opc_request_id})}\n\n"
                
                if 'choices' in chunk and len(chunk['choices']) > 0:
                    delta = chunk['choices'][0].get('delta', {})
                    content = delta.get('content', '')
                    
                    if content:
                        full_response += content
                        yield f"data: {json.dumps({'type': 'content', 'content': content})}\n\n"
                
                if 'usage' in chunk:
                    usage_data = chunk['usage']
            
            messages.append({
                'role': 'assistant',
                'content': full_response,
                'timestamp': datetime.now().isoformat()
            })
            
            add_api_log('chat_response', {
                'conversation_id': conversation_id,
                'response_length': len(full_response),
                'usage': usage_data,
                'opc_request_id': opc_request_id
            })
            
            completion_data = {
                'type': 'done',
                'usage': usage_data,
                'opc_request_id': opc_request_id
            }
            yield f"data: {json.dumps(completion_data)}\n\n"
            
        except Exception as e:
            add_api_log('chat_error', {
                'conversation_id': conversation_id,
                'error': str(e)
            })
            error_data = {
                'type': 'error',
                'error': str(e)
            }
            yield f"data: {json.dumps(error_data)}\n\n"
    
    return Response(generate(), mimetype='text/event-stream')


@app.route('/api/workflows', methods=['GET'])
def list_workflows():
    """List all workflows"""
    access_token = auth_provider.get_valid_access_token()
    if not access_token:
        return jsonify({'error': 'Not authenticated'}), 401
    
    try:
        workflows = workflow_manager.list_workflows()
        return jsonify({'workflows': workflows})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/workflows/<workflow_name>', methods=['GET'])
def get_workflow(workflow_name):
    """Get a specific workflow"""
    access_token = auth_provider.get_valid_access_token()
    if not access_token:
        return jsonify({'error': 'Not authenticated'}), 401
    
    try:
        workflow = workflow_manager.get_workflow(workflow_name)
        if workflow:
            return jsonify(workflow)
        else:
            return jsonify({'error': 'Workflow not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/workflows', methods=['POST'])
def create_workflow():
    """Create a new workflow"""
    access_token = auth_provider.get_valid_access_token()
    if not access_token:
        return jsonify({'error': 'Not authenticated'}), 401
    
    data = request.json
    name = data.get('name')
    content = data.get('content', '')
    
    if not name:
        return jsonify({'error': 'Workflow name is required'}), 400
    
    try:
        success = workflow_manager.save_workflow(name, content)
        if success:
            return jsonify({'success': True, 'message': 'Workflow created successfully'})
        else:
            return jsonify({'error': 'Failed to create workflow'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/workflows/<workflow_name>', methods=['PUT'])
def update_workflow(workflow_name):
    """Update a workflow"""
    access_token = auth_provider.get_valid_access_token()
    if not access_token:
        return jsonify({'error': 'Not authenticated'}), 401
    
    data = request.json
    content = data.get('content', '')
    
    try:
        success = workflow_manager.save_workflow(workflow_name, content)
        if success:
            return jsonify({'success': True, 'message': 'Workflow updated successfully'})
        else:
            return jsonify({'error': 'Failed to update workflow'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/workflows/<workflow_name>', methods=['DELETE'])
def delete_workflow(workflow_name):
    """Delete a workflow"""
    access_token = auth_provider.get_valid_access_token()
    if not access_token:
        return jsonify({'error': 'Not authenticated'}), 401
    
    try:
        success = workflow_manager.delete_workflow(workflow_name)
        if success:
            return jsonify({'success': True, 'message': 'Workflow deleted successfully'})
        else:
            return jsonify({'error': 'Workflow not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/workflows/<workflow_name>/run', methods=['POST'])
def run_workflow(workflow_name):
    """Start a workflow run"""
    access_token = auth_provider.get_valid_access_token()
    if not access_token:
        return jsonify({'error': 'Not authenticated'}), 401
    
    data = request.json
    params = data.get('params', {})
    
    try:
        run_id = workflow_manager.create_run(workflow_name, params)
        
        global workflow_runner
        if workflow_runner is None:
            workflow_runner = WorkflowRunner(workflow_manager, mcp_manager, get_oca_client(), auth_provider)
        
        workflow_runner.start_run(run_id)
        
        return jsonify({'success': True, 'run_id': run_id})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/workflows/runs/<run_id>', methods=['GET'])
def get_workflow_run(run_id):
    """Get workflow run details"""
    access_token = auth_provider.get_valid_access_token()
    if not access_token:
        return jsonify({'error': 'Not authenticated'}), 401
    
    try:
        run_data = workflow_manager.get_run(run_id)
        if run_data:
            return jsonify(run_data)
        else:
            return jsonify({'error': 'Run not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/workflows/runs', methods=['GET'])
def list_workflow_runs():
    """List all workflow runs"""
    access_token = auth_provider.get_valid_access_token()
    if not access_token:
        return jsonify({'error': 'Not authenticated'}), 401
    
    workflow_name = request.args.get('workflow_name')
    
    try:
        runs = workflow_manager.list_runs(workflow_name)
        return jsonify({'runs': runs})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/workflows/runs/<run_id>/stream', methods=['GET'])
def stream_workflow_run(run_id):
    """Stream workflow run events via SSE"""
    access_token = auth_provider.get_valid_access_token()
    if not access_token:
        return jsonify({'error': 'Not authenticated'}), 401
    
    def generate():
        global workflow_runner
        if workflow_runner is None:
            yield f"data: {json.dumps({'type': 'error', 'error': 'Workflow runner not initialized'})}\n\n"
            return
        
        try:
            for event in workflow_runner.get_events(run_id):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"
    
    return Response(generate(), mimetype='text/event-stream')


@app.route('/api/workflows/runs/<run_id>/approve', methods=['POST'])
def approve_workflow_step(run_id):
    """Approve or reject a workflow step"""
    access_token = auth_provider.get_valid_access_token()
    if not access_token:
        return jsonify({'error': 'Not authenticated'}), 401
    
    data = request.json
    step_index = data.get('step_index')
    approved = data.get('approved', False)
    
    if step_index is None:
        return jsonify({'error': 'step_index is required'}), 400
    
    try:
        global workflow_runner
        if workflow_runner is None:
            return jsonify({'error': 'Workflow runner not initialized'}), 500
        
        workflow_runner.approve_step(run_id, step_index, approved)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/workflows/runs/<run_id>/answer', methods=['POST'])
def answer_workflow_question(run_id):
    """Answer a workflow followup question"""
    access_token = auth_provider.get_valid_access_token()
    if not access_token:
        return jsonify({'error': 'Not authenticated'}), 401
    
    data = request.json
    approval_key = data.get('approval_key')
    response = data.get('response', '')
    
    if not approval_key:
        return jsonify({'error': 'approval_key is required'}), 400
    
    try:
        global workflow_runner
        if workflow_runner is None:
            return jsonify({'error': 'Workflow runner not initialized'}), 500
        
        workflow_runner.answer_question(approval_key, response)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/workflows/runs/<run_id>/cancel', methods=['POST'])
def cancel_workflow_run(run_id):
    """Cancel a workflow run"""
    access_token = auth_provider.get_valid_access_token()
    if not access_token:
        return jsonify({'error': 'Not authenticated'}), 401
    
    try:
        global workflow_runner
        if workflow_runner is None:
            return jsonify({'error': 'Workflow runner not initialized'}), 500
        
        workflow_runner.cancel_run(run_id)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def start_terminal():
    """Start a bash terminal process"""
    global terminal_process
    if terminal_process is None or terminal_process.poll() is not None:
        terminal_process = subprocess.Popen(
            ['/bin/bash'],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
            universal_newlines=False
        )
        
        def read_output():
            while terminal_process and terminal_process.poll() is None:
                try:
                    output = terminal_process.stdout.read(1024)
                    if output:
                        terminal_output_queue.put(output.decode('utf-8', errors='replace'))
                except Exception as e:
                    print(f"Terminal read error: {e}")
                    break
        
        threading.Thread(target=read_output, daemon=True).start()


@app.route('/api/terminal/stream')
def terminal_stream():
    """Stream terminal output via SSE"""
    access_token = auth_provider.get_valid_access_token()
    if not access_token:
        return jsonify({'error': 'Not authenticated'}), 401
    
    start_terminal()
    
    def generate():
        try:
            while True:
                try:
                    output = terminal_output_queue.get(timeout=1.0)
                    yield f"data: {json.dumps({'output': output})}\n\n"
                except queue.Empty:
                    yield f"data: {json.dumps({'ping': True})}\n\n"
        except GeneratorExit:
            pass
    
    return Response(generate(), mimetype='text/event-stream')


@app.route('/api/terminal/input', methods=['POST'])
def terminal_input():
    """Send input to terminal"""
    access_token = auth_provider.get_valid_access_token()
    if not access_token:
        return jsonify({'error': 'Not authenticated'}), 401
    
    data = request.json
    input_data = data.get('input', '')
    
    if terminal_process and terminal_process.poll() is None:
        try:
            terminal_process.stdin.write(input_data.encode('utf-8'))
            terminal_process.stdin.flush()
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    else:
        start_terminal()
        return jsonify({'success': True})


if __name__ == '__main__':
    port = int(os.getenv('FLASK_PORT', 5000))
    print(f"\n{'='*60}")
    print(f"OCA Chat Application Starting")
    print(f"{'='*60}")
    print(f"Mode: {runtime_settings['oca_mode']}")
    print(f"Model: {runtime_settings['model_id']}")
    print(f"Base URL: {runtime_settings['oca_base_url']}")
    print(f"Server: http://localhost:{port}")
    print(f"{'='*60}\n")
    
    workflow_runner = WorkflowRunner(workflow_manager, mcp_manager, get_oca_client(), auth_provider)
    
    app.run(host='0.0.0.0', port=port, debug=True)
