"""
Loopback Server for OAuth Callback
Handles OAuth2 callback on 127.0.0.1 with dynamic port (48801-48811)
Based on Cline's AuthHandler implementation
"""

import http.server
import socketserver
import threading
import time
from typing import Optional, Callable, Dict, Any
from urllib.parse import urlparse, parse_qs


PORT_RANGE_START = 48801
PORT_RANGE_END = 48811
PORTS = list(range(PORT_RANGE_START, PORT_RANGE_END + 1))


class LoopbackAuthHandler(http.server.BaseHTTPRequestHandler):
    """HTTP request handler for OAuth callback"""
    
    callback_handler: Optional[Callable[[str, str], Dict[str, Any]]] = None
    success_redirect_url: str = "http://localhost:5000"
    
    def log_message(self, format, *args):
        """Override to customize logging"""
        print(f"LoopbackServer: {format % args}")
    
    def do_GET(self):
        """Handle GET request for OAuth callback"""
        try:
            parsed_url = urlparse(self.path)
            
            if parsed_url.path == '/auth/oca':
                query_params = parse_qs(parsed_url.query)
                code = query_params.get('code', [None])[0]
                state = query_params.get('state', [None])[0]
                error = query_params.get('error', [None])[0]
                
                if error:
                    self.send_error_response(f"Authentication error: {error}")
                    return
                
                if not code or not state:
                    self.send_error_response("Missing code or state parameter")
                    return
                
                handler = type(self).callback_handler
                if handler:
                    try:
                        result = handler(code, state)
                        if result.get('success'):
                            self.send_success_response()
                        else:
                            self.send_error_response(result.get('error', 'Authentication failed'))
                    except Exception as e:
                        self.send_error_response(f"Error processing authentication: {str(e)}")
                else:
                    self.send_error_response("No callback handler configured")
            else:
                self.send_response(404)
                self.send_header('Content-type', 'text/plain')
                self.end_headers()
                self.wfile.write(b'Not found')
                
        except Exception as e:
            print(f"LoopbackServer: Error handling request: {e}")
            self.send_error_response(f"Internal error: {str(e)}")
    
    def send_success_response(self):
        """Send success HTML response with redirect"""
        html = self.create_success_html()
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(html.encode('utf-8'))
    
    def send_error_response(self, error_message: str):
        """Send error HTML response"""
        html = self.create_error_html(error_message)
        self.send_response(400)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(html.encode('utf-8'))
    
    def create_success_html(self) -> str:
        """Create success HTML page with redirect"""
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>OCA Chat - Authentication Success</title>
    <script>
        setTimeout(() => {{
            window.location.href = '{self.success_redirect_url}';
        }}, 1500);
    </script>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
        }}
        .container {{
            text-align: center;
            padding: 40px;
            background: white;
            border-radius: 20px;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
            max-width: 480px;
        }}
        .checkmark {{
            width: 64px;
            height: 64px;
            border-radius: 50%;
            background: #4CAF50;
            margin: 0 auto 24px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 32px;
            color: white;
        }}
        h1 {{
            font-size: 24px;
            margin-bottom: 16px;
            color: #333;
        }}
        p {{
            font-size: 16px;
            color: #666;
            margin-bottom: 24px;
        }}
        .countdown {{
            font-size: 14px;
            color: #999;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="checkmark">✓</div>
        <h1>Authentication Successful!</h1>
        <p>Your authentication token has been securely saved.</p>
        <div class="countdown">Redirecting you back to the chat...</div>
    </div>
</body>
</html>"""
    
    def create_error_html(self, error_message: str) -> str:
        """Create error HTML page"""
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>OCA Chat - Authentication Error</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
        }}
        .container {{
            text-align: center;
            padding: 40px;
            background: white;
            border-radius: 20px;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
            max-width: 480px;
        }}
        .error-icon {{
            width: 64px;
            height: 64px;
            border-radius: 50%;
            background: #f44336;
            margin: 0 auto 24px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 32px;
            color: white;
        }}
        h1 {{
            font-size: 24px;
            margin-bottom: 16px;
            color: #333;
        }}
        p {{
            font-size: 16px;
            color: #666;
            margin-bottom: 24px;
        }}
        .error-details {{
            background: #f5f5f5;
            padding: 16px;
            border-radius: 8px;
            font-size: 14px;
            color: #666;
            word-wrap: break-word;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="error-icon">✗</div>
        <h1>Authentication Failed</h1>
        <p>There was an error during authentication.</p>
        <div class="error-details">{error_message}</div>
    </div>
</body>
</html>"""


class LoopbackServer:
    """Manages a temporary loopback HTTP server for OAuth callbacks"""
    
    def __init__(self, callback_handler: Callable[[str, str], Dict[str, Any]], 
                 success_redirect_url: str = "http://localhost:5000"):
        self.callback_handler = callback_handler
        self.success_redirect_url = success_redirect_url
        self.server: Optional[socketserver.TCPServer] = None
        self.server_thread: Optional[threading.Thread] = None
        self.port: Optional[int] = None
        self.is_running = False
    
    def start(self) -> str:
        """
        Start the loopback server and return the callback URL
        Returns: callback URL (e.g., http://127.0.0.1:48801)
        """
        if self.is_running:
            raise RuntimeError("Server is already running")
        
        for port in PORTS:
            try:
                LoopbackAuthHandler.callback_handler = self.callback_handler
                LoopbackAuthHandler.success_redirect_url = self.success_redirect_url
                
                self.server = socketserver.TCPServer(
                    ("127.0.0.1", port),
                    LoopbackAuthHandler,
                    bind_and_activate=True
                )
                self.port = port
                
                self.server_thread = threading.Thread(
                    target=self.server.serve_forever,
                    daemon=True
                )
                self.server_thread.start()
                self.is_running = True
                
                print(f"LoopbackServer: Started on 127.0.0.1:{port}")
                return f"http://127.0.0.1:{port}"
                
            except OSError as e:
                if e.errno == 98:  # Address already in use
                    print(f"LoopbackServer: Port {port} in use, trying next...")
                    continue
                else:
                    raise
        
        raise RuntimeError(f"No available port in range {PORT_RANGE_START}-{PORT_RANGE_END}")
    
    def stop(self):
        """Stop the loopback server"""
        if self.server:
            print(f"LoopbackServer: Stopping server on port {self.port}")
            self.server.shutdown()
            self.server.server_close()
            self.server = None
            self.server_thread = None
            self.port = None
            self.is_running = False
    
    def __enter__(self):
        """Context manager entry"""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.stop()
