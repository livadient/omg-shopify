"""Get a Google OAuth2 refresh token for Google Ads API.

Usage:
1. Run this script
2. Open the printed URL in your browser
3. Authorize — you'll get redirected to a page that won't load
4. Copy the FULL URL from your browser's address bar
5. Paste it when prompted
"""
import os
import requests
from urllib.parse import urlparse, parse_qs

# Read from env so secrets stay out of git. Set GOOGLE_ADS_CLIENT_ID and
# GOOGLE_ADS_CLIENT_SECRET in your .env (also used by the runtime app).
CLIENT_ID = os.environ["GOOGLE_ADS_CLIENT_ID"]
CLIENT_SECRET = os.environ["GOOGLE_ADS_CLIENT_SECRET"]
REDIRECT_URI = "http://localhost:9090"
SCOPE = "https://www.googleapis.com/auth/adwords"

auth_url = (
    f"https://accounts.google.com/o/oauth2/auth"
    f"?client_id={CLIENT_ID}"
    f"&redirect_uri={REDIRECT_URI}"
    f"&scope={SCOPE}"
    f"&response_type=code"
    f"&access_type=offline"
    f"&prompt=consent"
)

print("\nOpen this URL in your browser:\n")
print(auth_url)
print("\nAfter authorizing, you'll get a 'localhost refused' error page.")
print("That's OK! Copy the FULL URL from your browser's address bar.")
print("It looks like: http://localhost:9090/?code=4/0A...&scope=...\n")

redirect_url = input("Paste the full URL here: ").strip()

query = parse_qs(urlparse(redirect_url).query)
code = query.get("code", [None])[0]

if not code:
    print("No code found in URL. Make sure you copied the full URL.")
else:
    resp = requests.post("https://oauth2.googleapis.com/token", data={
        "code": code,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    })
    data = resp.json()
    if "refresh_token" in data:
        print(f"\n{'='*60}")
        print(f"REFRESH TOKEN: {data['refresh_token']}")
        print(f"{'='*60}")
    else:
        print(f"\nError: {data}")
