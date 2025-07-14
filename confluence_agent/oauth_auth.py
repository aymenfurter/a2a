import asyncio
import base64
import hashlib
import secrets
import webbrowser
from urllib.parse import urlencode, parse_qs, urlparse
import httpx
import json
import os
from datetime import datetime, timedelta

class AtlassianOAuthFlow:
    def __init__(self):
        self.client_id = "vlHvoTZhX0XCd7Qf"
        self.redirect_uri = "http://localhost:6274/oauth/callback/debug"
        self.token_file = os.path.expanduser("~/.atlassian_mcp_token.json")
        self.oauth_base = "https://mcp.atlassian.com"
        
    async def get_oauth_config(self):
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{self.oauth_base}/.well-known/oauth-authorization-server")
            return response.json()
    
    def load_token(self):
        if not os.path.exists(self.token_file):
            return None
        try:
            with open(self.token_file, 'r') as f:
                token_data = json.load(f)
            if "expires_in" in token_data and "obtained_at" in token_data:
                obtained_at = datetime.fromisoformat(token_data["obtained_at"])
                if datetime.now() > obtained_at + timedelta(seconds=token_data["expires_in"]):
                    return None
            return token_data
        except:
            return None
    
    def save_token(self, token_data):
        token_data["obtained_at"] = datetime.now().isoformat()
        with open(self.token_file, 'w') as f:
            json.dump(token_data, f)
        os.chmod(self.token_file, 0o600)
    
    async def authenticate(self) -> str:
        token_data = self.load_token()
        if token_data and "access_token" in token_data:
            return token_data["access_token"]
        
        code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode('utf-8').rstrip('=')
        code_challenge = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest()).decode('utf-8').rstrip('=')
        
        params = {
            "response_type": "code", "client_id": self.client_id, "redirect_uri": self.redirect_uri,
            "scope": "offline_access read:me search:confluence read:confluence-user read:page:confluence",
            "code_challenge": code_challenge, "code_challenge_method": "S256"
        }
        auth_url = f"{self.oauth_base}/v1/authorize?" + urlencode(params)
        
        print(f"Visit: {auth_url}")
        try:
            webbrowser.open(auth_url)
        except:
            pass
        
        redirect_url = input("Paste redirect URL: ").strip()
        code = parse_qs(urlparse(redirect_url).query)["code"][0]
        
        oauth_config = await self.get_oauth_config()
        async with httpx.AsyncClient() as client:
            response = await client.post(oauth_config["token_endpoint"], data={
                "grant_type": "authorization_code", "code": code, "redirect_uri": self.redirect_uri,
                "client_id": self.client_id, "code_verifier": code_verifier
            })
            token_data = response.json()
        
        self.save_token(token_data)
        return token_data["access_token"]

async def get_atlassian_bearer_token() -> str:
    return await AtlassianOAuthFlow().authenticate()