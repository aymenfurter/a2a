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
    OAUTH_BASE_URL = "https://mcp.atlassian.com"
    WELL_KNOWN_URL = f"{OAUTH_BASE_URL}/.well-known/oauth-authorization-server"
    
    def __init__(self):
        self.client_id = "vlHvoTZhX0XCd7Qf"
        self.redirect_uri = "http://localhost:6274/oauth/callback/debug"
        self.token_file = os.path.expanduser("~/.atlassian_mcp_token.json")
        
    async def get_oauth_config(self):
        async with httpx.AsyncClient() as client:
            response = await client.get(self.WELL_KNOWN_URL, headers={"accept": "*/*", "mcp-protocol-version": "2025-06-18"})
            return response.json()
    
    def generate_code_verifier(self):
        code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode('utf-8').rstrip('=')
        code_challenge = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest()).decode('utf-8').rstrip('=')
        return code_verifier, code_challenge
    
    def build_auth_url(self, code_challenge: str) -> str:
        params = {
            "response_type": "code", "client_id": self.client_id, "redirect_uri": self.redirect_uri,
            "scope": "offline_access read:me search:confluence read:confluence-user read:page:confluence write:page:confluence read:comment:confluence write:comment:confluence read:space:confluence read:hierarchical-content:confluence read:jira-work write:jira-work",
            "code_challenge": code_challenge, "code_challenge_method": "S256"
        }
        return f"{self.OAUTH_BASE_URL}/v1/authorize?" + urlencode(params)
    
    def save_token(self, token_data):
        token_data["obtained_at"] = datetime.now().isoformat()
        with open(self.token_file, 'w') as f:
            json.dump(token_data, f)
        os.chmod(self.token_file, 0o600)
    
    def load_token(self):
        if not os.path.exists(self.token_file):
            return None
        try:
            with open(self.token_file, 'r') as f:
                token_data = json.load(f)
            if "expires_in" in token_data and "obtained_at" in token_data:
                obtained_at = datetime.fromisoformat(token_data["obtained_at"])
                expires_at = obtained_at + timedelta(seconds=token_data["expires_in"])
                if datetime.now() > expires_at:
                    return None
            return token_data
        except:
            return None
    
    async def exchange_code_for_token(self, code: str, code_verifier: str):
        oauth_config = await self.get_oauth_config()
        async with httpx.AsyncClient() as client:
            response = await client.post(oauth_config["token_endpoint"], data={
                "grant_type": "authorization_code", "code": code, "redirect_uri": self.redirect_uri,
                "client_id": self.client_id, "code_verifier": code_verifier
            }, headers={"Content-Type": "application/x-www-form-urlencoded"})
            return response.json()
    
    async def refresh_token(self, refresh_token: str):
        oauth_config = await self.get_oauth_config()
        async with httpx.AsyncClient() as client:
            response = await client.post(oauth_config["token_endpoint"], data={
                "grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": self.client_id
            }, headers={"Content-Type": "application/x-www-form-urlencoded"})
            return response.json()
    
    async def authenticate(self) -> str:
        token_data = self.load_token()
        if token_data and "access_token" in token_data:
            if "refresh_token" in token_data:
                try:
                    new_token_data = await self.refresh_token(token_data["refresh_token"])
                    self.save_token(new_token_data)
                    return new_token_data["access_token"]
                except:
                    pass
            else:
                return token_data["access_token"]
        
        code_verifier, code_challenge = self.generate_code_verifier()
        auth_url = self.build_auth_url(code_challenge)
        
        print(f"\nVisit: {auth_url}")
        try:
            webbrowser.open(auth_url)
        except:
            pass
        
        redirect_url = input("Paste redirect URL: ").strip()
        parsed_url = urlparse(redirect_url)
        query_params = parse_qs(parsed_url.query)
        code = query_params["code"][0]
        
        token_data = await self.exchange_code_for_token(code, code_verifier)
        self.save_token(token_data)
        return token_data["access_token"]

async def get_atlassian_bearer_token() -> str:
    flow = AtlassianOAuthFlow()
    return await flow.authenticate()