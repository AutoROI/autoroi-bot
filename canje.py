import requests

url = "https://api.mercadolibre.com/oauth/token"
payload = {
    "grant_type": "authorization_code",
    "client_id": "4863415175026919",
    "client_secret": "PAWr01KiFxTOOOhAEpkkLv0d2KyxfrYh",
    "code": "TG-6a0bdba15a66d700018b0766-345527979",
    "redirect_uri": "https://hlaholqtfcrsjlxjuxdy.supabase.co"
}

response = requests.post(url, data=payload)
print(response.json())