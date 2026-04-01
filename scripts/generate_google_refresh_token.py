"""Generate Google Ads OAuth2 Refresh Token.

Usage: python scripts/generate_google_refresh_token.py
"""

import sys
sys.path.insert(0, ".")

from src.config import get_settings

def main():
    settings = get_settings()
    client_id = settings.google_ads.google_ads_client_id
    client_secret = settings.google_ads.google_ads_client_secret

    if not client_id or not client_secret:
        print("Error: GOOGLE_ADS_CLIENT_ID and GOOGLE_ADS_CLIENT_SECRET must be set in .env")
        sys.exit(1)

    # Step 1: Generate authorization URL
    scope = "https://www.googleapis.com/auth/adwords"
    redirect_uri = "urn:ietf:wg:oauth:2.0:oob"  # For desktop/manual copy-paste flow

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
    print("1. 아래 URL을 브라우저에서 열어주세요:")
    print()
    print(auth_url)
    print()
    print("2. Google 계정으로 로그인하고 권한을 허용하세요.")
    print("3. 화면에 나오는 인증 코드를 복사하세요.")
    print()

    auth_code = input("4. 인증 코드를 여기에 붙여넣기: ").strip()

    if not auth_code:
        print("Error: 인증 코드가 비어있습니다.")
        sys.exit(1)

    # Step 2: Exchange auth code for refresh token
    import requests

    token_url = "https://oauth2.googleapis.com/token"
    data = {
        "code": auth_code,
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
