"""
OCA API Client
Handles communication with Oracle Code Assist API for chat completions
"""

import json
import hashlib
import time
import secrets
from typing import Dict, Any, List, Optional, Generator
import requests


class OCAClient:
    """Client for Oracle Code Assist API"""
    
    def __init__(self, base_url: str, model_id: str = "gpt-4o"):
        self.base_url = base_url.rstrip('/')
        self.model_id = model_id
        self.session_id = self._generate_session_id()
        
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
    
    def _create_headers(self, access_token: str, task_id: str) -> Dict[str, str]:
        """Create headers for OCA API requests"""
        opc_request_id = self._generate_opc_request_id(task_id, access_token)
        
        return {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json',
            'client': 'OCA-Chat-App',
            'client-version': '1.0.0',
            'client-ide': 'web',
            'client-ide-version': '1.0.0',
            'opc-request-id': opc_request_id
        }
    
    def chat_completion(
        self,
        access_token: str,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        stream: bool = True,
        temperature: float = 0.7,
        max_tokens: int = 4096
    ) -> Generator[Dict[str, Any], None, None]:
        """
        Send chat completion request to OCA API
        
        Args:
            access_token: Valid OCA access token
            messages: List of message dicts with 'role' and 'content'
            system_prompt: Optional system prompt
            stream: Whether to stream the response
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            
        Yields:
            Chunks of the response as they arrive
        """
        task_id = self.session_id
        headers = self._create_headers(access_token, task_id)
        
        api_messages = []
        if system_prompt:
            api_messages.append({
                'role': 'system',
                'content': system_prompt
            })
        api_messages.extend(messages)
        
        payload = {
            'model': self.model_id,
            'messages': api_messages,
            'temperature': temperature,
            'max_tokens': max_tokens,
            'stream': stream,
            'stream_options': {'include_usage': True} if stream else None,
            'litellm_session_id': f'cline-{task_id}'
        }
        
        url = f'{self.base_url}/chat/completions'
        
        if stream:
            response = requests.post(
                url,
                headers=headers,
                json=payload,
                stream=True,
                timeout=60
            )
            try:
                response.raise_for_status()
            except requests.exceptions.HTTPError as e:
                error_detail = ""
                try:
                    error_body = response.json()
                    error_detail = f"\nError details: {json.dumps(error_body, indent=2)}"
                except:
                    error_detail = f"\nError body: {response.text}"
                raise Exception(f"OCA API error (status {response.status_code}): {str(e)}{error_detail}") from e
            
            for line in response.iter_lines():
                if line:
                    line_str = line.decode('utf-8')
                    if line_str.startswith('data: '):
                        data_str = line_str[6:]  # Remove 'data: ' prefix
                        if data_str.strip() == '[DONE]':
                            break
                        try:
                            chunk = json.loads(data_str)
                            yield chunk
                        except json.JSONDecodeError:
                            continue
        else:
            response = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=60
            )
            try:
                response.raise_for_status()
            except requests.exceptions.HTTPError as e:
                error_detail = ""
                try:
                    error_body = response.json()
                    error_detail = f"\nError details: {json.dumps(error_body, indent=2)}"
                except:
                    error_detail = f"\nError body: {response.text}"
                raise Exception(f"OCA API error (status {response.status_code}): {str(e)}{error_detail}") from e
            yield response.json()
    
    def list_models(self, access_token: str) -> List[Dict[str, Any]]:
        """List available models from OCA API"""
        task_id = self.session_id
        headers = self._create_headers(access_token, task_id)
        
        try:
            url = f'{self.base_url}/models'
            response = requests.get(
                url,
                headers=headers,
                timeout=10
            )
            response.raise_for_status()
            data = response.json()
            return data.get('data', [])
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
                print(f"Error calculating cost: {response.status_text}")
                return None
        except Exception as e:
            print(f"Error calculating cost: {e}")
            return None
