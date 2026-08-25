"""
monday.com GraphQL API client.

Reads board schema + items dynamically at query time. Nothing about the
board contents is hardcoded — only the board IDs (which boards to look at)
come from configuration, per the assignment's "do not hardcode CSV data"
requirement.
"""
import os
import time
import requests

MONDAY_API_URL = "https://api.monday.com/v2"


class MondayAPIError(Exception):
    pass


class MondayClient:
    def __init__(self, api_token: str | None = None):
        self.api_token = api_token or os.environ.get("MONDAY_API_KEY")
        if not self.api_token:
            raise MondayAPIError(
                "MONDAY_API_KEY is not set. Add it as an environment variable."
            )
        # Monday.com accepts both bare token and "Bearer <token>" — use Bearer format
        token = self.api_token.strip()
        if not token.lower().startswith("bearer "):
            token = f"Bearer {token}"
        self.headers = {
            "Authorization": token,
            "Content-Type": "application/json",
            "API-Version": "2024-10",
        }

    def _post(self, query: str, variables: dict | None = None, retries: int = 3) -> dict:
        last_err = None
        for attempt in range(retries):
            try:
                resp = requests.post(
                    MONDAY_API_URL,
                    json={"query": query, "variables": variables or {}},
                    headers=self.headers,
                    timeout=30,
                )
                data = resp.json()
                if "errors" in data:
                    raise MondayAPIError(str(data["errors"]))
                return data["data"]
            except (requests.RequestException, MondayAPIError, KeyError) as e:
                last_err = e
                time.sleep(1.5 * (attempt + 1))  # simple backoff for transient API failures
        raise MondayAPIError(f"monday.com API failed after {retries} attempts: {last_err}")

    def get_board_schema(self, board_id: str) -> list[dict]:
        """Return the column definitions (id, title, type) for a board."""
        query = """
        query ($boardId: [ID!]) {
          boards (ids: $boardId) {
            columns { id title type }
          }
        }
        """
        data = self._post(query, {"boardId": [board_id]})
        boards = data.get("boards") or []
        if not boards:
            raise MondayAPIError(f"Board {board_id} not found or not accessible with this token.")
        return boards[0]["columns"]

    def get_board_items(self, board_id: str, page_limit: int = 100) -> list[dict]:
        """
        Fetch ALL items on a board (handles pagination via cursor), each as
        {"name": <item name>, "id": ..., "column_values": [{id, title, text, value}, ...]}
        """
        items = []
        cursor = None
        query = """
        query ($boardId: ID!, $limit: Int!, $cursor: String) {
          boards (ids: [$boardId]) {
            items_page (limit: $limit, cursor: $cursor) {
              cursor
              items {
                id
                name
                column_values { id column { title } text value type }
              }
            }
          }
        }
        """
        while True:
            data = self._post(
                query, {"boardId": board_id, "limit": page_limit, "cursor": cursor}
            )
            page = data["boards"][0]["items_page"]
            items.extend(page["items"])
            cursor = page["cursor"]
            if not cursor:
                break
        return items

    def test_connection(self) -> dict:
        """Cheap call used to verify the token works, for startup health checks."""
        query = "query { me { name email } }"
        return self._post(query)
