import os
import httpx
import logging

logger = logging.getLogger(__name__)

RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY", "")
RAPIDAPI_HOST = "multi-social-media-video-downloader-api.p.rapidapi.com"


async def _fetch_instagram_download_url(url: str) -> tuple[str | None, str | None, str | None, str | None]:
    api_key = RAPIDAPI_KEY
    rapidapi_host = RAPIDAPI_HOST

    if not api_key:
        return None, None, None, "Error: RAPIDAPI_KEY not set."

    headers = {
        "x-rapidapi-key": api_key,
        "x-rapidapi-host": rapidapi_host,
        "Content-Type": "application/json",
    }

    try:
        # Step 1: POST to get masked URL
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"https://{rapidapi_host}/media",
                json={"url": url, "platform": "ig"},
                headers=headers,
            )
            if resp.status_code != 200:
                return None, None, None, f"API Error {resp.status_code}: {resp.text}"

            data = resp.json()

        logger.info(f"Multi-social API step 1 response: {data}")

        if not data or not data.get("formats"):
            return None, None, None, "No media found. Make sure the post is public."

        # Get the first format URL (masked URL)
        masked_url = data["formats"][0].get("url")
        if not masked_url:
            return None, None, None, "Could not extract masked URL from response."

        # Step 2: GET masked URL to get final download URL
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp2 = await client.get(masked_url, headers=headers)
            if resp2.status_code != 200:
                return None, None, None, f"API Error {resp2.status_code}: {resp2.text}"

            final_data = resp2.json()

        logger.info(f"Multi-social API step 2 response: {final_data}")

        download_url = final_data.get("downloadUrl")
        media_type = final_data.get("type", "")
        title = final_data.get("title", "")

        if not download_url:
            return None, None, None, "Could not extract download URL from response."

        ext = ".mp4" if "mp4" in download_url or "video" in media_type else ".jpg"
        filename = f"temp_downloads/{os.urandom(8).hex()}{ext}"

        os.makedirs("temp_downloads", exist_ok=True)

        # Download the actual media file
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp3 = await client.get(download_url, follow_redirects=True)
            if resp3.status_code != 200:
                return None, None, None, "Failed to download media file."
            content = resp3.content

        with open(filename, "wb") as f:
            f.write(content)

        return filename, title, media_type, None

    except httpx.ReadTimeout:
        return None, None, None, "Download took too long. Please try again with a smaller video."
    except Exception as e:
        logger.error(f"Exception in _fetch_instagram_download_url: {e}", exc_info=True)
        return None, None, None, f"Error: {str(e)}"


async def download_instagram(url: str) -> tuple[str | None, str | None, str | None, str | None]:
    return await _fetch_instagram_download_url(url)
