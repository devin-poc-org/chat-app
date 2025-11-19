"""
OCA Authentication Module
Handles OAuth2 PKCE flow for Oracle Code Assist authentication
"""

import os
import json
import hashlib
import base64
import secrets
import time
from typing import Optional, Dict, Any
import requests
import jwt
from datetime import datetime, timedelta


class OCAAuthProvider:
    """Handles OCA authentication using OAuth2 PKCE flow"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.pkce_state_map: Dict[str, Dict[str, Any]] = {}
        self.tokens_file = "tokens.json"
        
    def _generate_code_verifier(self, length: int = 128) -> str:
        """Generate PKCE code verifier"""
        chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
        return ''.join(secrets.choice(chars) for _ in range(length))
    
    def _generate_code_challenge(self, verifier: str) -> str:
        """Generate PKCE code challenge from verifier"""
        digest = hashlib.sha256(verifier.encode()).digest()
        return base64.urlsafe_b64encode(digest).decode().rstrip('=')
    
    def _generate_random_string(self, length: int = 32) -> str:
        """Generate random string for state and nonce"""
        return secrets.token_urlsafe(length)[:length]
    
    def get_auth_url(self, callback_url: str, oca_mode: str) -> str:
        """Generate authorization URL for OAuth2 flow"""
        mode_config = self.config[oca_mode]
        
        code_verifier = self._generate_code_verifier()
        code_challenge = self._generate_code_challenge(code_verifier)
        state = self._generate_random_string(32)
        nonce = self._generate_random_string(32)
        
        self.pkce_state_map[state] = {
            'code_verifier': code_verifier,
            'nonce': nonce,
            'created_at': time.time(),
            'redirect_uri': callback_url
        }
        
        cutoff = time.time() - 600
        self.pkce_state_map = {
            k: v for k, v in self.pkce_state_map.items() 
            if v['created_at'] >= cutoff
        }
        
        idcs_url = mode_config['idcs_url'].rstrip('/')
        params = {
            'client_id': mode_config['client_id'],
            'response_type': 'code',
            'scope': mode_config['scopes'],
            'code_challenge': code_challenge,
            'code_challenge_method': 'S256',
            'redirect_uri': callback_url,
            'state': state,
            'nonce': nonce
        }
        
        query_string = '&'.join(f"{k}={v}" for k, v in params.items())
        return f"{idcs_url}/oauth2/v1/authorize?{query_string}"
    
    def exchange_code_for_tokens(self, code: str, state: str, oca_mode: str) -> Dict[str, Any]:
        """Exchange authorization code for access and refresh tokens"""
        if state not in self.pkce_state_map:
            raise ValueError("Invalid state - PKCE flow not initiated or expired")
        
        pkce_data = self.pkce_state_map.pop(state)
        mode_config = self.config[oca_mode]
        
        idcs_url = mode_config['idcs_url'].rstrip('/')
        discovery_url = f"{idcs_url}/.well-known/openid-configuration"
        discovery_response = requests.get(discovery_url)
        discovery_response.raise_for_status()
        token_endpoint = discovery_response.json()['token_endpoint']
        
        token_data = {
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': pkce_data['redirect_uri'],
            'client_id': mode_config['client_id'],
            'code_verifier': pkce_data['code_verifier']
        }
        
        token_response = requests.post(
            token_endpoint,
            data=token_data,
            headers={'Content-Type': 'application/x-www-form-urlencoded'}
        )
        token_response.raise_for_status()
        tokens = token_response.json()
        
        id_token = tokens.get('id_token')
        if not id_token:
            raise ValueError("No ID token received")
        
        decoded = jwt.decode(id_token, options={"verify_signature": False})
        if decoded.get('nonce') != pkce_data['nonce']:
            raise ValueError("Nonce verification failed")
        
        user_info = {
            'uid': decoded.get('sub', ''),
            'email': decoded.get('sub', ''),
            'display_name': decoded.get('sub', '')
        }
        
        self._save_tokens({
            'access_token': tokens['access_token'],
            'refresh_token': tokens.get('refresh_token'),
            'id_token': id_token,
            'expires_at': time.time() + tokens.get('expires_in', 3600),
            'user_info': user_info,
            'oca_mode': oca_mode
        })
        
        return {
            'access_token': tokens['access_token'],
            'user_info': user_info
        }
    
    def _save_tokens(self, tokens: Dict[str, Any]):
        """Save tokens to file"""
        with open(self.tokens_file, 'w') as f:
            json.dump(tokens, f, indent=2)
    
    def _load_tokens(self) -> Optional[Dict[str, Any]]:
        """Load tokens from file"""
        try:
            with open(self.tokens_file, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            return None
    
    def should_refresh_token(self, access_token: str) -> bool:
        """Check if access token should be refreshed"""
        try:
            decoded = jwt.decode(access_token, options={"verify_signature": False})
            exp = decoded.get('exp', 0)
            expiration_time = exp * 1000  # Convert to milliseconds
            current_time = time.time() * 1000
            five_minutes_ms = 5 * 60 * 1000
            return current_time > (expiration_time - five_minutes_ms)
        except Exception:
            return True
    
    def get_valid_access_token(self) -> Optional[str]:
        """Get a valid access token, refreshing if necessary"""
        tokens = self._load_tokens()
        if not tokens:
            return None
        
        access_token = tokens.get('access_token')
        if not access_token:
            return None
        
        if not self.should_refresh_token(access_token):
            return access_token
        
        refresh_token = tokens.get('refresh_token')
        if not refresh_token:
            return None
        
        try:
            oca_mode = tokens.get('oca_mode', 'internal')
            new_tokens = self._refresh_access_token(refresh_token, oca_mode)
            return new_tokens['access_token']
        except Exception as e:
            print(f"Error refreshing token: {e}")
            return None
    
    def _refresh_access_token(self, refresh_token: str, oca_mode: str) -> Dict[str, Any]:
        """Refresh access token using refresh token"""
        mode_config = self.config[oca_mode]
        
        idcs_url = mode_config['idcs_url'].rstrip('/')
        discovery_url = f"{idcs_url}/.well-known/openid-configuration"
        discovery_response = requests.get(discovery_url)
        discovery_response.raise_for_status()
        token_endpoint = discovery_response.json()['token_endpoint']
        
        token_data = {
            'grant_type': 'refresh_token',
            'refresh_token': refresh_token,
            'client_id': mode_config['client_id']
        }
        
        token_response = requests.post(
            token_endpoint,
            data=token_data,
            headers={'Content-Type': 'application/x-www-form-urlencoded'}
        )
        token_response.raise_for_status()
        tokens = token_response.json()
        
        access_token = tokens['access_token']
        decoded = jwt.decode(access_token, options={"verify_signature": False})
        user_info = {
            'uid': decoded.get('sub', ''),
            'email': decoded.get('email', decoded.get('preferred_username', decoded.get('sub', ''))),
            'display_name': decoded.get('name', decoded.get('preferred_username', decoded.get('sub', '')))
        }
        
        self._save_tokens({
            'access_token': access_token,
            'refresh_token': tokens.get('refresh_token', refresh_token),
            'expires_at': time.time() + tokens.get('expires_in', 3600),
            'user_info': user_info,
            'oca_mode': oca_mode
        })
        
        return {
            'access_token': access_token,
            'user_info': user_info
        }
    
    def clear_tokens(self):
        """Clear stored tokens"""
        try:
            os.remove(self.tokens_file)
        except FileNotFoundError:
            pass
    
    def get_user_info(self) -> Optional[Dict[str, Any]]:
        """Get stored user info"""
        tokens = self._load_tokens()
        if tokens:
            return tokens.get('user_info')
        return None
