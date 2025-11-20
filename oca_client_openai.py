"""
OCA API Client using OpenAI SDK
Handles communication with Oracle Code Assist API for chat completions
This implementation uses the OpenAI Python SDK to match Cline's approach
"""

import os
import json
import hashlib
import time
import secrets
from typing import Dict, Any, List, Optional, Generator
from openai import OpenAI, OpenAIError


class OCAClient:
    """Client for Oracle Code Assist API using OpenAI SDK"""
    
    def __init__(self, base_url: str, model_id: str = "oca/gpt-4.1"):
        self.base_url = base_url.rstrip('/')
        self.model_id = model_id
        self.session_id = self._generate_session_id()
        self.client = None
        
    def _generate_session_id(self) -> str:
        """Generate a unique session ID"""
        return f"oca-chat-{int(time.time())}-{secrets.token_hex(8)}"
    
    def _generate_opc_request_id(self, task_id: str, token: str) -> str:
        """Generate OPC request ID for tracking"""
        def hash_8(s: str) -> str:
            return hashlib.sha256(s.encode()).hexdigest()[:8]
        
        token_hex = hash_8(token)
        task_hex = hash_8(task_id)
        timestamp_hex = format(int(time.time()), '08x')
        random_hex = format(secrets.randbits(32), '08x')
        
        return token_hex + task_hex + timestamp_hex + random_hex
    
    def _create_headers(self, access_token: str, task_id: str, user_email: Optional[str] = None) -> Dict[str, str]:
        """Create headers for OCA API requests"""
        opc_request_id = self._generate_opc_request_id(task_id, access_token)
        
        headers = {
            'Content-Type': 'application/json',
            'client': 'OCA-Chat-App',
            'client-version': '1.0.0',
            'client-ide': 'web',
            'client-ide-version': '1.0.0',
            'opc-request-id': opc_request_id
        }
        
        include_principal = os.getenv('OCA_INCLUDE_ORIGINAL_PRINCIPAL', 'false').lower() == 'true'
        if include_principal and user_email:
            headers['original-principal'] = user_email
        
        return headers
    
    def _ensure_client(self, access_token: str) -> OpenAI:
        """Ensure OpenAI client is initialized with access token"""
        self.client = OpenAI(
            base_url=self.base_url,
            api_key=access_token  # Use access token as API key for proper Authorization header
        )
        return self.client
    
    def chat_completion(
        self,
        access_token: str,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        stream: bool = True,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        user_email: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: str = "auto"
    ) -> Generator[Dict[str, Any], None, None]:
        """
        Send chat completion request to OCA API using OpenAI SDK
        
        Args:
            access_token: Valid OCA access token
            messages: List of message dicts with 'role' and 'content'
            system_prompt: Optional system prompt
            stream: Whether to stream the response
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            user_email: Optional user email for original-principal header
            tools: Optional list of tools for function calling
            tool_choice: Tool choice strategy ("auto", "none", or specific tool)
            
        Yields:
            Chunks of the response as they arrive
        """
        task_id = self.session_id
        headers = self._create_headers(access_token, task_id, user_email)
        opc_request_id = headers['opc-request-id']
        
        client = self._ensure_client(access_token)
        
        api_messages = []
        if system_prompt:
            api_messages.append({
                'role': 'system',
                'content': system_prompt
            })
        api_messages.extend(messages)
        
        try:
            request_params = {
                'model': self.model_id,
                'messages': api_messages,
                'temperature': temperature,
                'max_tokens': max_tokens,
                'extra_headers': headers,
                'extra_body': {
                    'litellm_session_id': f'cline-{task_id}'
                }
            }
            
            if tools:
                request_params['tools'] = tools
                request_params['tool_choice'] = tool_choice
            
            if stream:
                request_params['stream'] = True
                request_params['stream_options'] = {'include_usage': True}
                response = client.chat.completions.create(**request_params)
                
                first_chunk = True
                for chunk in response:
                    chunk_dict = chunk.model_dump()
                    if first_chunk:
                        chunk_dict['opc_request_id'] = opc_request_id
                        first_chunk = False
                    yield chunk_dict
            else:
                request_params['stream'] = False
                response = client.chat.completions.create(**request_params)
                result = response.model_dump()
                result['opc_request_id'] = opc_request_id
                yield result
                
        except OpenAIError as e:
            error_msg = f"OCA API error: {str(e)}\nopc-request-id: {opc_request_id}"
            if hasattr(e, 'response') and hasattr(e.response, 'headers'):
                server_opc_id = e.response.headers.get('opc-request-id')
                if server_opc_id:
                    error_msg += f"\nserver opc-request-id: {server_opc_id}"
            raise Exception(error_msg) from e
        except Exception as e:
            error_msg = f"Error: {str(e)}\nopc-request-id: {opc_request_id}"
            raise Exception(error_msg) from e
    
    def list_models(self, access_token: str) -> List[Dict[str, Any]]:
        """List available models from OCA API"""
        task_id = self.session_id
        headers = self._create_headers(access_token, task_id)
        
        client = self._ensure_client(access_token)
        
        try:
            models = client.models.list(extra_headers=headers)
            return [model.model_dump() for model in models.data]
        except Exception as e:
            print(f"Error listing models: {e}")
            return []
    
    def calculate_cost(
        self,
        access_token: str,
        prompt_tokens: int,
        completion_tokens: int
    ) -> Optional[float]:
        """Calculate cost for token usage"""
        import requests
        
        task_id = self.session_id
        headers = self._create_headers(access_token, task_id)
        
        payload = {
            'completion_response': {
                'model': self.model_id,
                'usage': {
                    'prompt_tokens': prompt_tokens,
                    'completion_tokens': completion_tokens
                }
            }
        }
        
        try:
            url = f'{self.base_url}/spend/calculate'
            response = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=10
            )
            
            if response.ok:
                data = response.json()
                return data.get('cost')
            else:
                print(f"Error calculating cost: {response.status_code}")
                return None
        except Exception as e:
            print(f"Error calculating cost: {e}")
            return None
