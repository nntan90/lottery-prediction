import requests
from bs4 import BeautifulSoup

def inspect_minhngoc():
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                     'AppleWebKit/537.36 (KHTML, like Gecko) '
                     'Chrome/120.0.0.0 Safari/537.36'
    }
    
    # Inspect structure for mien=1 (XSMN)
    url = "https://www.minhngoc.net/tra-cuu-ket-qua-xo-so.html?mien=1&ngay=1&thang=1&nam=2024"
    print(f"Fetching {url}...")
    headers = headers 
    try:
        response = requests.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(response.content, 'html.parser')
        
        table = soup.find('table', class_='bkqmiennam')
        if table:
            # Get provinces
            provinces = [p.text.strip() for p in table.find_all('td', class_='tinh')]
            print(f"Provinces: {provinces}")
            
            # Iterate rows
            rows = table.find_all('tr')
            for row in rows:
                # Check if it's a prize row
                # Usually class names like 'giai8', 'giai7', etc.
                # However, sometimes the class is on the td, not tr.
                # Let's inspect all tds
                tds = row.find_all('td')
                row_data = []
                for td in tds:
                    # Clean text
                    text = td.get_text(separator='|').strip()
                    cls = td.get('class', [])
                    row_data.append(f"{cls}:{text}")
                print(f"Row: {row_data}")
        else:
            print("Table not found")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    inspect_minhngoc()
