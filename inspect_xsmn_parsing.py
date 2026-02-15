import requests
from bs4 import BeautifulSoup
from datetime import date

def inspect_xsmn_parsing():
    target_date = date(2024, 1, 1)
    url = "https://xskt.com.vn/xsmn/ngay-1-1-2024"
    province = "ca-mau"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                     'AppleWebKit/537.36 (KHTML, like Gecko) '
                     'Chrome/120.0.0.0 Safari/537.36'
    }
    
    print(f"Fetching {url}...")
    response = requests.get(url, headers=headers)
    soup = BeautifulSoup(response.content, 'html.parser')
    
    table = soup.find('table', class_='tbl-xsmn')
    if not table:
        print("Table not found")
        return

    # Find Cà Mau column (index 3 based on previous logs)
    # But let's find it dynamically to be sure
    headers = table.find_all('th')
    col_idx = -1
    for i, th in enumerate(headers):
        if "Cà Mau" in th.text:
            col_idx = i
            print(f"Found Cà Mau at column {i}")
            break
            
    if col_idx == -1:
        print("Cà Mau column not found")
        return

    rows = table.find_all('tr')
    for row in rows[1:]:
        cells = row.find_all('td')
        if len(cells) <= col_idx: continue
        
        prize_name = cells[0].text.strip()
        cell_content = cells[col_idx]
        
        print(f"\nPrize: {prize_name}")
        print(f"Raw HTML: {cell_content}")
        print(f"Text content: {cell_content.text}")
        print(f"Text separated: {cell_content.get_text(separator='|')}")

if __name__ == "__main__":
    inspect_xsmn_parsing()
