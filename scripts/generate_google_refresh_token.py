"""Generate Google Ads OAuth2 Refresh Token using localhost redirect.

Usage: python scripts/generate_google_refresh_token.py
"""

import sys
sys.path.insert(0, ".")

import threading
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler

import requests
from src.config import get_settings

auth_code_result = {"code": None}


class OAuthHandler(BaseHTTPRequestHandler):
    """Catch the OAuth redirect and extract the authorization code."""

    def do_GET(self):
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)

        if "code" in params:
            auth_code_result["code"] = params["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                "<html><body><h1>인증 성공!</h1>"
                "<p>이 창을 닫아도 됩니다.</p></body></html>".encode("utf-8")
            )
        else:
            self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                "<html><body><h1>인증 실패</h1>"
                f"<p>{params}</p></body></html>".encode("utf-8")
            )

    def log_message(self, format, *args):
        pass  # Suppress logs


def main():
    settings = get_settings()
    client_id = settings.google_ads.google_ads_client_id
    client_secret = settings.google_ads.google_ads_client_secret

    if not client_id or not client_secret:
        print("Error: GOOGLE_ADS_CLIENT_ID and GOOGLE_ADS_CLIENT_SECRET must be set in .env")
        sys.exit(1)

    scope = "https://www.googleapis.com/auth/adwords"
    redirect_uri = "http://localhost:8090"

    auth_url = (
        "https://accounts.google.com/o/oauth2/auth"
        f"?client_id={client_id}"
        f"&redirect_uri={redirect_uri}"
        f"&scope={scope}"
        "&response_type=code"
        "&access_type=offline"
        "&prompt=consent"
    )

    print("=" * 60)
    print("Google Ads OAuth2 Refresh Token 발급")
    print("=" * 60)
    print()
    print("브라우저에서 아래 URL을 열어주세요:")
    print()
    print(auth_url)
    print()
    print("로그인 후 자동으로 코드를 받습니다. 대기 중...")

    # Start local server to catch redirect
    server = HTTPServer(("localhost", 8090), OAuthHandler)
    server.timeout = 300  # 5 min timeout

    server.handle_request()

    code = auth_code_result["code"]
    if not code:
        print("Error: 인증 코드를 받지 못했습니다.")
        sys.exit(1)

    print(f"\n인증 코드 수신 완료!")

    # Exchange code for refresh token
    token_url = "https://oauth2.googleapis.com/token"
    data = {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }

    resp = requests.post(token_url, data=data, timeout=30)

    if resp.status_code != 200:
        print(f"Error: Token exchange failed: {resp.status_code}")
        print(resp.text)
        sys.exit(1)

    tokens = resp.json()
    refresh_token = tokens.get("refresh_token")

    if refresh_token:
        print()
        print("=" * 60)
        print("Refresh Token 발급 성공!")
        print("=" * 60)
        print(f"Refresh Token: {refresh_token}")
        print()
        print(".env 파일의 GOOGLE_ADS_REFRESH_TOKEN에 이 값을 넣어주세요.")
    else:
        print("Error: Refresh token not found in response")
        print(tokens)


if __name__ == "__main__":
    main()
