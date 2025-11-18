# OCA Chat Application

A simple, local web-based chat interface for Oracle Code Assist (OCA). This application provides an easy-to-use UI where users can interact with OCA models through a chat interface.

## Features

- 🔐 **OAuth2 Authentication**: Secure PKCE-based authentication with Oracle IDCS
- 💬 **Real-time Streaming**: Messages stream in real-time as the model generates responses
- 📝 **Conversation History**: Maintains conversation context across messages
- 🎨 **Modern UI**: Clean, responsive interface that works on desktop and mobile
- 🔄 **Token Management**: Automatic token refresh for seamless experience
- 🌐 **Dual Mode Support**: Works with both internal and external OCA endpoints

## Architecture

The application consists of three main components:

1. **OCA Authentication Module** (`oca_auth.py`): Handles OAuth2 PKCE flow, token management, and refresh
2. **OCA API Client** (`oca_client.py`): Communicates with OCA API for chat completions
3. **Flask Backend** (`app.py`): Provides REST API and serves the web interface

## Prerequisites

- Python 3.8 or higher
- pip (Python package manager)
- Access to Oracle Code Assist (OCA)

## Installation

1. **Clone or download this repository**

```bash
cd oca-chat-app
```

2. **Create a virtual environment** (recommended)

```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. **Install dependencies**

```bash
pip install -r requirements.txt
```

4. **Configure environment variables**

Copy the example environment file and customize it:

```bash
cp .env.example .env
```

Edit `.env` with your preferred settings:

```env
# Choose "internal" or "external" mode
OCA_MODE=internal

# Model to use (default: gpt-4o)
OCA_MODEL_ID=gpt-4o

# Flask configuration
FLASK_SECRET_KEY=your-secret-key-change-this
FLASK_PORT=5000
```

**Note**: The default IDCS and OCA URLs are pre-configured. You only need to change them if you have custom endpoints.

## Usage

1. **Start the application**

```bash
python app.py
```

You should see output like:

```
============================================================
OCA Chat Application Starting
============================================================
Mode: internal
Model: gpt-4o
Server: http://localhost:5000
============================================================
```

2. **Open your browser**

Navigate to: `http://localhost:5000`

3. **Authenticate**

Click the "Login with OCA" button to authenticate with Oracle IDCS. You'll be redirected to the Oracle login page, and after successful authentication, you'll be redirected back to the chat interface.

4. **Start chatting**

Type your message in the input box and press Enter or click "Send". The AI will respond in real-time with streaming responses.

## Configuration

### OCA Modes

The application supports two modes:

- **Internal Mode**: For Oracle internal users
  - Uses internal IDCS and OCA endpoints
  - Default client ID and scopes are pre-configured

- **External Mode**: For external users
  - Uses external IDCS and OCA endpoints
  - Default client ID and scopes are pre-configured

Set the mode in your `.env` file:

```env
OCA_MODE=internal  # or "external"
```

### Custom Configuration

If you need to use custom IDCS or OCA endpoints, you can override them in your `.env` file:

```env
# Custom Internal Configuration
INTERNAL_IDCS_CLIENT_ID=your-client-id
INTERNAL_IDCS_URL=https://your-idcs-url
INTERNAL_IDCS_SCOPES=openid offline_access
INTERNAL_OCA_BASE_URL=https://your-oca-url

# Custom External Configuration
EXTERNAL_IDCS_CLIENT_ID=your-client-id
EXTERNAL_IDCS_URL=https://your-idcs-url
EXTERNAL_IDCS_SCOPES=openid offline_access
EXTERNAL_OCA_BASE_URL=https://your-oca-url
```

### Available Models

You can change the model by setting `OCA_MODEL_ID` in your `.env` file. Common models include:

- `gpt-4o` (default)
- `gpt-4o-mini`
- `claude-3-5-sonnet-20241022`
- And other models supported by your OCA instance

## API Endpoints

The application provides the following REST API endpoints:

### Authentication

- `GET /api/auth/status` - Check authentication status
- `GET /api/auth/login` - Initiate OAuth2 login flow
- `GET /api/auth/callback` - OAuth2 callback handler
- `POST /api/auth/logout` - Logout and clear tokens

### Chat

- `POST /api/chat` - Send a message and receive streaming response
  ```json
  {
    "message": "Your message here",
    "conversation_id": "default"
  }
  ```

### Conversations

- `GET /api/conversations/<conversation_id>` - Get conversation history
- `DELETE /api/conversations/<conversation_id>` - Clear conversation
- `GET /api/conversations` - List all conversations

## How It Works

### Authentication Flow

1. User clicks "Login with OCA"
2. Application generates PKCE code verifier and challenge
3. User is redirected to Oracle IDCS for authentication
4. After successful login, IDCS redirects back with authorization code
5. Application exchanges code for access and refresh tokens
6. Tokens are stored locally in `tokens.json`
7. Access tokens are automatically refreshed when needed

### Chat Flow

1. User sends a message through the web interface
2. Message is added to conversation history
3. Backend sends request to OCA API with full conversation context
4. Response streams back in real-time using Server-Sent Events (SSE)
5. Frontend displays the response as it arrives
6. Conversation history is maintained in memory

## File Structure

```
oca-chat-app/
├── app.py                 # Flask application and API endpoints
├── oca_auth.py           # OCA authentication module
├── oca_client.py         # OCA API client
├── requirements.txt      # Python dependencies
├── .env.example          # Example environment configuration
├── .env                  # Your environment configuration (not in git)
├── .gitignore           # Git ignore rules
├── tokens.json          # Stored authentication tokens (not in git)
├── templates/
│   └── index.html       # Web interface
└── README.md            # This file
```

## Security Notes

- **Never commit** your `.env` file or `tokens.json` to version control
- The `FLASK_SECRET_KEY` should be changed to a random string in production
- Tokens are stored locally in `tokens.json` - keep this file secure
- The application runs on localhost by default - do not expose it directly to the internet without proper security measures

## Troubleshooting

### Authentication Issues

**Problem**: "Not authenticated" error

**Solution**: 
- Click "Login with OCA" to authenticate
- If already logged in, try logging out and logging in again
- Check that your `.env` file has the correct OCA_MODE setting

### Connection Issues

**Problem**: Cannot connect to OCA API

**Solution**:
- Verify your OCA_MODE is set correctly (internal/external)
- Check that the OCA_BASE_URL is correct for your mode
- Ensure you have network access to the OCA endpoints
- Check if your access token has expired (the app should auto-refresh)

### Token Refresh Issues

**Problem**: Token refresh fails

**Solution**:
- Delete `tokens.json` and log in again
- Verify your IDCS configuration is correct
- Check that offline_access scope is included

## Development

### Running in Debug Mode

The application runs in debug mode by default when started with `python app.py`. This enables:
- Auto-reload on code changes
- Detailed error messages
- Flask debug toolbar

### Adding New Features

The codebase is modular and easy to extend:

- **Add new API endpoints**: Edit `app.py`
- **Modify authentication**: Edit `oca_auth.py`
- **Change API client behavior**: Edit `oca_client.py`
- **Update UI**: Edit `templates/index.html`

## Based on Cline Implementation

This application is based on the Oracle Code Assist (OCA) model provider implementation from [Cline](https://github.com/cline/cline), an autonomous coding agent. The authentication flow and API client are adapted from Cline's TypeScript implementation to Python.

## License

This is a demonstration application for Oracle Code Assist integration.

## Support

For issues related to:
- **OCA Access**: Contact your Oracle administrator
- **Application Issues**: Check the troubleshooting section above
- **Feature Requests**: Feel free to extend the application for your needs

## Contributing

This is a simple demonstration application. Feel free to fork and modify it for your specific needs.

---

**Happy Chatting! 🤖**
