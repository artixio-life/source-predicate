import time
import random
import logging
import requests

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 65_536          # 64 KB per iter_content chunk
_LOG_PROGRESS_EVERY = 10      # log download progress every N seconds


def download_with_retries(
    url,
    retries=5,
    backoff_factor=5,
    timeout=60,
    max_download_time=300,    # hard wall-clock cap on body read (seconds)
    headers=None,
    stream=True,
    cookies=None,
    session=None,
):
    """
    Downloads a file from a URL with robust retry logic for 429 and 5xx errors.

    Args:
        url (str): The URL to download.
        retries (int): Number of attempts.
        backoff_factor (int): Base seconds for exponential backoff.
        timeout (int|tuple): requests connect/read timeout (seconds).
        max_download_time (int): Hard cap on total body-read time (seconds).
        headers (dict): Optional request headers.
        stream (bool): Whether to stream the response (recommended for files).

    Returns:
        tuple: (response_content, content_type, status_code) on success,
               (None, None, status_code) otherwise.
    """
    if headers is None:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "application/pdf,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }

    active_session = session if session else requests.Session()

    for attempt in range(retries):
        try:
            response = active_session.get(
                url,
                timeout=timeout,
                stream=stream,
                headers=headers,
                cookies=cookies,
            )

            if response.status_code == 200:
                content_type = response.headers.get("Content-Type", "")

                chunks = []
                total_bytes = 0
                deadline = time.monotonic() + max_download_time
                last_log = time.monotonic()

                for chunk in response.iter_content(chunk_size=_CHUNK_SIZE):
                    if not chunk:
                        continue
                    chunks.append(chunk)
                    total_bytes += len(chunk)

                    now = time.monotonic()

                    if now - last_log >= _LOG_PROGRESS_EVERY:
                        elapsed = now - (deadline - max_download_time)
                        logger.info(
                            f"Downloading {url} — {total_bytes / 1_048_576:.1f} MB "
                            f"in {elapsed:.0f}s …"
                        )
                        last_log = now

                    if now >= deadline:
                        logger.warning(
                            f"Download exceeded {max_download_time}s wall-clock limit "
                            f"({total_bytes / 1_048_576:.1f} MB received) for {url}. Aborting."
                        )
                        return None, None, 0

                content = b"".join(chunks)

                if content and len(content) < 5_000 and b"temporarily unavailable" in content.lower():
                    wait = backoff_factor * (3 ** attempt) + random.uniform(5, 10)
                    logger.warning(
                        f"Detected 'Server Unavailable' in 200 response for {url}. "
                        f"Waiting {wait:.2f}s (attempt {attempt + 1}/{retries})"
                    )
                    time.sleep(wait)
                    continue

                logger.info(f"Downloaded {total_bytes / 1_048_576:.2f} MB from {url}")
                return content, content_type, response.status_code

            elif response.status_code in [429, 503, 502, 504]:
                wait = backoff_factor * (3 ** attempt) + random.uniform(5, 15)
                logger.warning(
                    f"Got {response.status_code} for {url}, "
                    f"waiting {wait:.2f}s (attempt {attempt + 1}/{retries})"
                )
                time.sleep(wait)
                continue

            elif 500 <= response.status_code < 600:
                wait = backoff_factor * (2 ** attempt) + random.uniform(2, 5)
                logger.warning(
                    f"Got {response.status_code} for {url}, "
                    f"waiting {wait:.2f}s (attempt {attempt + 1}/{retries})"
                )
                time.sleep(wait)
                continue

            else:
                logger.error(f"Failed to download {url}: Status {response.status_code}")
                return None, None, response.status_code

        except requests.exceptions.RequestException as e:
            if "Name or service not known" in str(e):
                logger.warning(f"DNS error for {url}: {e}. Skipping retries.")
                break

            wait = backoff_factor * (2 ** attempt) + random.uniform(0.5, 1.5)
            logger.warning(
                f"Network error downloading {url}: {e}. "
                f"Waiting {wait:.2f}s (attempt {attempt + 1}/{retries}) …"
            )
            time.sleep(wait)

    logger.error(f"Max retries exceeded for {url}")
    return None, None, 0
