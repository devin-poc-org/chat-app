# Troubleshooting Guide

## Common Issues

### Error: "module 'jwt' has no attribute 'decode'"

**Cause**: You have the wrong `jwt` package installed instead of `PyJWT`.

There are two different packages that both install as `jwt`:
- `jwt` (deprecated, wrong package) - does NOT have `decode` method
- `PyJWT` (correct package) - has `decode` method

**Solution**:

1. **Uninstall the wrong package and install the correct one**:
```bash
pip uninstall -y jwt
pip install "PyJWT>=2.8.0,<3.0.0"
```

2. **Verify the installation**:
```bash
python -c "import jwt; print('Version:', jwt.__version__); print('Has decode:', hasattr(jwt, 'decode'))"
```

You should see:
```
Version: 2.x.x
Has decode: True
```

3. **If you're still having issues**, check which module is being imported:
```bash
python -c "import jwt; print('Module path:', jwt.__file__)"
```

4. **Make sure you're using the project's virtual environment**:
```bash
# Activate the venv
source venv/bin/activate  # On Linux/Mac
# or
venv\Scripts\activate  # On Windows

# Reinstall requirements
pip install -r requirements.txt
```

### Error: "Invalid redirect URI"

**Cause**: The IDCS client is configured to accept loopback URIs on specific ports (48801-48811).

**Solution**: The app automatically uses the correct port range. If you still see this error, check that:
1. No other application is using ports 48801-48811
2. Your firewall allows localhost connections on these ports

### Error: "404 Not Found" when sending chat messages

**Cause**: The model ID specified doesn't exist in your OCA deployment.

**Solution**:
1. Check available models:
```bash
curl http://localhost:5000/api/models
```

2. Update your `.env` file with a valid model ID:
```bash
OCA_MODEL_ID=anthropic/claude-3-7-sonnet-20250219
```

Common model IDs:
- `anthropic/claude-3-7-sonnet-20250219` (default)
- `oca/gpt-4.1` (if available in your deployment)

### Authentication Issues

If authentication fails:

1. **Check your OCA mode** (internal vs external):
```bash
# In .env file
OCA_MODE=internal  # or external
```

2. **Clear tokens and try again**:
```bash
rm tokens.json
```

3. **Check token validity**:
```bash
python -c "from oca_auth import OCAAuthProvider; auth = OCAAuthProvider({'internal': {'client_id': 'YOUR_CLIENT_ID', 'idcs_url': 'YOUR_IDCS_URL', 'scopes': 'openid offline_access'}}); print('Valid token:', auth.get_valid_access_token() is not None)"
```

## Getting Help

If you encounter other issues:

1. Check the application logs for detailed error messages
2. Verify all environment variables are set correctly in `.env`
3. Ensure you're using Python 3.8 or higher
4. Make sure all dependencies are installed: `pip install -r requirements.txt`

For OCA-specific issues, include the `opc-request-id` from the error message when contacting support.
