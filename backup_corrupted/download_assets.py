
import os
import requests

ASSETS = {
    "tailwind.min.js": "https://cdn.tailwindcss.com/3.4.1",
    "lucide.min.js": "https://unpkg.com/lucide@latest/dist/umd/lucide.min.js",
    "chart.min.js": "https://cdn.jsdelivr.net/npm/chart.js"
}

OUTPUT_DIR = "static/js"

def download_assets():
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)
    
    for filename, url in ASSETS.items():
        path = os.path.join(OUTPUT_DIR, filename)
        print(f"Downloading {filename} from {url}...")
        try:
            response = requests.get(url, timeout=10)
            if response.ok:
                with open(path, "wb") as f:
                    f.write(response.content)
                print(f"Successfully saved to {path}")
            else:
                print(f"Failed to download {filename}: HTTP {response.status_code}")
        except Exception as e:
            print(f"Error downloading {filename}: {str(e)}")

if __name__ == "__main__":
    download_assets()
