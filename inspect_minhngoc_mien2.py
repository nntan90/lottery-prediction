import requests
from bs4 import BeautifulSoup

url = "https://www.minhngoc.net/tra-cuu-ket-qua-xo-so.html?mien=2&thu=0&ngay=6&thang=1&nam=2024"
print(f"Fetching {url}...")
resp = requests.get(url)
soup = BeautifulSoup(resp.content, 'html.parser')

# Find the main title or region header
title = soup.title.string if soup.title else "No Title"
print(f"Page Title: {title}")

# Try to find specific table class or header that indicates region
content = soup.find('div', class_='content')
if content:
    h2 = content.find('h2')
    if h2:
        print(f"H2 Header: {h2.get_text().strip()}")

# Check for keywords
text = soup.get_text()
if "Xổ số Miền Trung" in text:
    print("PAGE CONTAINS: 'Xổ số Miền Trung'")
if "Xổ số Miền Bắc" in text:
    print("PAGE CONTAINS: 'Xổ số Miền Bắc'")
if "Xổ số Miền Nam" in text:
    print("PAGE CONTAINS: 'Xổ số Miền Nam'")

# Check table content details
table = soup.find('table', class_='bkqmienbac')
if not table:
     table = soup.find('table', class_='bkqmiennam')

if table:
    print(f"Inspecting table: {table.get('class')}")
    rows = table.find_all('tr')
    for row in rows:
        text = row.get_text(separator='|', strip=True)
        print(f"Row: {text[:100]}...") # Print first 100 chars



