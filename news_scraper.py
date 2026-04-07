import httpx
import asyncio
from bs4 import BeautifulSoup
import re

NEWS_URL = "https://brutalassault.cz/en/c/news"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

async def fetch_news() -> list[dict]:
    async with httpx.AsyncClient(timeout=15, headers=HEADERS, follow_redirects=True) as client:
        response = await client.get(NEWS_URL)
    
    soup = BeautifulSoup(response.text, "html.parser")
    
    results = []
    for link_tag in soup.find_all("a", href=True):
        titolo_tag = link_tag.find("h3")
        if not titolo_tag:
            continue
        titolo = titolo_tag.get_text(strip=True)
        url = link_tag["href"]
        if not url.startswith("http"):
            url = "https://brutalassault.cz" + url
        if "/en/a/" not in url:
            continue
        results.append({"id": url, "titolo": titolo, "url": url})
    
    return results


async def fetch_article(url: str) -> dict:
    async with httpx.AsyncClient(timeout=15, headers=HEADERS, follow_redirects=True) as client:
        response = await client.get(url)
    
    soup = BeautifulSoup(response.text, "html.parser")
    
    # Immagine
    image_url = None
    for img in soup.find_all("img", src=True):
        src = img["src"]
        if "/images/articles/" in src:
            if not src.startswith("http"):
                src = "https://content.brutalassault.cz" + src
            image_url = src.replace("_thumb.jpeg", ".jpeg").replace("_thumb.jpg", ".jpg")
            break
    
    # Testo
    content_div = soup.find("div", class_="article_content")
    if content_div:
        testo = content_div.get_text(separator=" ", strip=True)
        testo = " ".join(testo.split())
        # Rimuovi spazi prima di punteggiatura
        testo = re.sub(r'\s([.,!?;:])', r'\1', testo)
    else:
        testo = ""
    
    return {"image_url": image_url, "testo": testo}


if __name__ == "__main__":
    async def test():
        result = await fetch_article("https://brutalassault.cz/en/a/733/bands-update-fragile-spectrum-of-depth")
        print("IMAGE:", result["image_url"])
        print("TESTO:", result["testo"])
    
    asyncio.run(test())