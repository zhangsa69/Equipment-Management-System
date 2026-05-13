import requests

try:
    res = requests.get("http://127.0.0.1:8000/devices/")
    print(f"Status: {res.status_code}")
    if res.status_code == 200:
        data = res.json()
        print(f"Count: {len(data)}")
        if len(data) > 0:
            print(f"First device: {data[0].get('name')}")
    else:
        print(f"Error Body: {res.text}")
except Exception as e:
    print(f"Request failed: {e}")
