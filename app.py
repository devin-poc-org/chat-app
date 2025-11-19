"""
OCA Chat Application
A simple web-based chat interface for Oracle Code Assist
"""

import os
import json
import webbrowser
import threading
from typing import Dict, Any, List
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, Response
from flask_cors import CORS
from dotenv import load_dotenv
from oca_auth import OCAAuthProvider
from oca_client import OCAClient
from loopback_server import LoopbackServer

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

OCA_MODE = os.getenv('OCA_MODE', 'internal')
OCA_BASE_URL = os.getenv(
    f'{OCA_MODE.upper()}_OCA_BASE_URL',
    'https://code-internal.aiservice.us-chicago-1.oci.oraclecloud.com/20250206/app/litellm'
)
OCA_MODEL_ID = os.getenv('OCA_MODEL_ID', 'gpt-4o')

auth_provider = OCAAuthProvider(OCA_CONFIG)
oca_client = OCAClient(OCA_BASE_URL, OCA_MODEL_ID)

conversations: Dict[str, List[Dict[str, str]]] = {}

loopback_server = None
auth_in_progress = False


@app.route('/')
def index():
    """Main chat interface"""
    access_token = auth_provider.get_valid_access_token()
    user_info = auth_provider.get_user_info()
    
    return render_template('index.html', 
                         authenticated=access_token is not None,
                         user_info=user_info,
                         oca_mode=OCA_MODE,
                         model_id=OCA_MODEL_ID)


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
    
    try:
        auth_in_progress = True
        
        def handle_oauth_callback(code: str, state: str) -> Dict[str, Any]:
            global loopback_server, auth_in_progress
            try:
                result = auth_provider.exchange_code_for_tokens(code, state, OCA_MODE)
                
                if loopback_server:
                    threading.Timer(2.0, loopback_server.stop).start()
                
                auth_in_progress = False
                return {'success': True, 'result': result}
            except Exception as e:
                auth_in_progress = False
                return {'success': False, 'error': str(e)}
        
        loopback_server = LoopbackServer(
            callback_handler=handle_oauth_callback,
            success_redirect_url='http://localhost:5000'
        )
        callback_base = loopback_server.start()
        callback_url = f"{callback_base}/auth/oca"
        
        auth_url = auth_provider.get_auth_url(callback_url, OCA_MODE)
        
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
    conversation_id = data.get('conversation_id', 'default')
    
    if not user_message:
        return jsonify({'error': 'Message is required'}), 400
    
    if conversation_id not in conversations:
        conversations[conversation_id] = []
    
    conversation = conversations[conversation_id]
    
    conversation.append({
        'role': 'user',
        'content': user_message
    })
    
    def generate():
        try:
            full_response = ""
            usage_data = None
            
            for chunk in oca_client.chat_completion(
                access_token=access_token,
                messages=conversation,
                system_prompt="You are a helpful AI assistant.",
                stream=True
            ):
                if 'choices' in chunk and len(chunk['choices']) > 0:
                    delta = chunk['choices'][0].get('delta', {})
                    content = delta.get('content', '')
                    
                    if content:
                        full_response += content
                        yield f"data: {json.dumps({'type': 'content', 'content': content})}\n\n"
                
                if 'usage' in chunk:
                    usage_data = chunk['usage']
            
            conversation.append({
                'role': 'assistant',
                'content': full_response
            })
            
            completion_data = {
                'type': 'done',
                'usage': usage_data
            }
            yield f"data: {json.dumps(completion_data)}\n\n"
            
        except Exception as e:
            error_data = {
                'type': 'error',
                'error': str(e)
            }
            yield f"data: {json.dumps(error_data)}\n\n"
    
    return Response(generate(), mimetype='text/event-stream')


@app.route('/api/conversations/<conversation_id>', methods=['GET'])
def get_conversation(conversation_id):
    """Get conversation history"""
    access_token = auth_provider.get_valid_access_token()
    if not access_token:
        return jsonify({'error': 'Not authenticated'}), 401
    
    conversation = conversations.get(conversation_id, [])
    return jsonify({'messages': conversation})


@app.route('/api/conversations/<conversation_id>', methods=['DELETE'])
def clear_conversation(conversation_id):
    """Clear conversation history"""
    access_token = auth_provider.get_valid_access_token()
    if not access_token:
        return jsonify({'error': 'Not authenticated'}), 401
    
    if conversation_id in conversations:
        del conversations[conversation_id]
    
    return jsonify({'success': True})


@app.route('/api/conversations', methods=['GET'])
def list_conversations():
    """List all conversation IDs"""
    access_token = auth_provider.get_valid_access_token()
    if not access_token:
        return jsonify({'error': 'Not authenticated'}), 401
    
    return jsonify({'conversations': list(conversations.keys())})


if __name__ == '__main__':
    port = int(os.getenv('FLASK_PORT', 5000))
    print(f"\n{'='*60}")
    print(f"OCA Chat Application Starting")
    print(f"{'='*60}")
    print(f"Mode: {OCA_MODE}")
    print(f"Model: {OCA_MODEL_ID}")
    print(f"Server: http://localhost:{port}")
    print(f"{'='*60}\n")
    
    app.run(host='0.0.0.0', port=port, debug=True)
