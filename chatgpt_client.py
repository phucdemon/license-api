"""
ChatGPT Client - Dựa trên sora_requests.py (SentinelTokenGenerator)
Sử dụng Cookie của tài khoản ChatGPT để chat qua unofficial API.

Cách dùng:
    python chatgpt_client.py

Yêu cầu:
    pip install requests pybase64

Lấy Cookie:
    1. Mở https://chatgpt.com trong trình duyệt, đăng nhập
    2. F12 → Network → chọn bất kỳ request nào đến chatgpt.com
    3. Copy toàn bộ giá trị header "cookie"
    4. Dán vào biến COOKIE_STRING bên dưới hoặc nhập khi chạy
"""

import json
import uuid
import hashlib
import re
import sys
import pybase64
import requests

# ─────────────────────────── CẤU HÌNH ───────────────────────────
COOKIE_STRING = ""          # Dán cookie vào đây, hoặc để trống để nhập khi chạy
DEFAULT_MODEL  = "gpt-4o"   # Hoặc: "gpt-4o-mini", "o1-mini", "o3-mini", "gpt-4-5"
USER_AGENT     = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/142.0.0.0 Safari/537.36"
)

# ─────────────────────────── SENTINEL TOKEN ──────────────────────

SENTINEL_REQ_URL = "https://chatgpt.com/backend-api/sentinel/chat-requirements"


def _solve_chat_pow(seed: str, difficulty: str) -> str:
    """Giải PoW cho chat-requirements khi server yêu cầu (required=true)."""
    diff_hex = difficulty.lstrip("0x")
    if len(diff_hex) % 2:
        diff_hex = "0" + diff_hex
    target    = bytes.fromhex(diff_hex)
    diff_len  = len(target)

    for n in range(10_000_000):
        candidate = f"{seed}{n}"
        h = hashlib.sha3_512(candidate.encode()).digest()
        if h[:diff_len] <= target:
            answer = json.dumps({"s": seed, "n": str(n)}, separators=(',', ':'))
            return "gAAAAAC" + pybase64.b64encode(answer.encode()).decode()

    return ""


def generate_sentinel_token(session: requests.Session) -> str:
    """Lấy openai-sentinel-chat-requirements-token qua GET request."""
    try:
        resp = session.get(SENTINEL_REQ_URL, timeout=15)
        resp.raise_for_status()
        data  = resp.json()
        token = data.get("token", "")
        pow_d = data.get("proofofwork") or {}
        if pow_d.get("required"):
            solved = _solve_chat_pow(pow_d.get("seed", ""), pow_d.get("difficulty", ""))
            if solved:
                token = solved
        return token
    except Exception as e:
        print(f"[Sentinel] Lỗi lấy token: {e}")
        return ""


# ─────────────────────────── AUTH ────────────────────────────────

def fetch_access_token(cookie: str) -> str:
    """Dùng cookie để lấy accessToken từ sora.chatgpt.com (giống SoraWorker._fetch_access_token)."""
    url = "https://chatgpt.com/api/auth/session"
    headers = {
        "accept":       "*/*",
        "user-agent":   USER_AGENT,
        "cookie":       cookie,
        "referer":      "https://chatgpt.com/",
    }
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    token = data.get("accessToken")
    if not token:
        raise ValueError("Không tìm thấy accessToken. Cookie có thể đã hết hạn.")
    return token


# ─────────────────────────── CHAT CLIENT ─────────────────────────

CONVERSATION_URL = "https://chatgpt.com/backend-api/conversation"


class ChatGPTClient:
    """
    Client đơn giản để chat với ChatGPT dùng Cookie.
    Hỗ trợ: đa turn (lưu conversation_id), streaming SSE.
    """

    def __init__(self, cookie: str, model: str = DEFAULT_MODEL):
        self.model           = model
        self.session         = requests.Session()
        self.conversation_id: str | None = None
        self.parent_msg_id   = str(uuid.uuid4())  # root message id

        # Auth
        print("⟳ Đang xác thực cookie...")
        token = fetch_access_token(cookie)
        print("✓ Xác thực thành công.\n")

        self.session.headers.update({
            "accept":                   "*/*",
            "accept-language":          "en-US,en;q=0.9",
            "authorization":            f"Bearer {token}",
            "content-type":             "application/json",
            "origin":                   "https://chatgpt.com",
            "referer":                  "https://chatgpt.com/",
            "user-agent":               USER_AGENT,
            "oai-device-id":            str(uuid.uuid4()),
            "oai-language":             "en-US",
            "sec-ch-ua-mobile":         "?0",
            "sec-ch-ua-platform":       '"Windows"',
            "sec-fetch-dest":           "empty",
            "sec-fetch-mode":           "cors",
            "sec-fetch-site":           "same-origin",
        })

    def send(self, text: str) -> str:
        """Gửi tin nhắn, trả về toàn bộ reply dạng string."""
        msg_id = str(uuid.uuid4())

        sentinel = generate_sentinel_token(self.session)

        payload = {
            "action":           "next",
            "model":            self.model,
            "timezone_offset_min": -420,
            "suggestions":      [],
            "history_and_training_disabled": False,
            "parent_message_id": self.parent_msg_id,
            "messages": [
                {
                    "id":      msg_id,
                    "author":  {"role": "user"},
                    "content": {"content_type": "text", "parts": [text]},
                    "metadata": {},
                }
            ],
        }
        if self.conversation_id:
            payload["conversation_id"] = self.conversation_id

        headers = {"openai-sentinel-chat-requirements-token": sentinel}

        try:
            resp = self.session.post(
                CONVERSATION_URL,
                json=payload,
                headers=headers,
                stream=True,
                timeout=120,
            )
            resp.raise_for_status()
        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else "?"
            body = e.response.text[:300] if e.response is not None else ""
            raise RuntimeError(f"HTTP {code}: {body}") from e

        return self._parse_sse(resp)

    # ── SSE parser ──────────────────────────────────────────────

    def _parse_sse(self, resp: requests.Response) -> str:
        """
        Đọc stream SSE, in từng token ra màn hình theo thời gian thực,
        trả về toàn bộ nội dung cuối cùng.
        """
        full_text   = ""
        last_cursor = 0      # để in phần mới thêm vào

        for raw_line in resp.iter_lines(decode_unicode=True):
            if not raw_line or not raw_line.startswith("data: "):
                continue
            data_str = raw_line[6:]
            if data_str.strip() == "[DONE]":
                break

            try:
                chunk = json.loads(data_str)
            except json.JSONDecodeError:
                continue

            # Cập nhật conversation_id để các lượt sau tiếp nối đúng thread
            conv_id = chunk.get("conversation_id")
            if conv_id:
                self.conversation_id = conv_id

            # Lấy nội dung từ message
            msg = chunk.get("message") or {}
            content = msg.get("content") or {}
            parts   = content.get("parts") or []
            if not parts or not isinstance(parts[0], str):
                continue

            # Chỉ in phần mới (streaming effect)
            new_text = parts[0]
            delta    = new_text[last_cursor:]
            if delta:
                print(delta, end="", flush=True)
                last_cursor = len(new_text)
                full_text   = new_text

            # Cập nhật parent_msg_id cho turn tiếp theo
            msg_id = msg.get("id")
            if msg_id:
                self.parent_msg_id = msg_id

        print()  # xuống dòng sau khi stream xong
        return full_text


# ─────────────────────────── CLI LOOP ────────────────────────────

AVAILABLE_MODELS = [
    "gpt-4o",
    "gpt-4o-mini",
    "o1-mini",
    "o3-mini",
    "gpt-4-5",
]

HELP_TEXT = """
Lệnh đặc biệt:
  /new       - Bắt đầu cuộc trò chuyện mới (xóa history)
  /model     - Đổi model (ví dụ: /model gpt-4o-mini)
  /models    - Liệt kê các model hỗ trợ
  /quit      - Thoát
  /help      - Hiển thị trợ giúp này
"""

def _banner(model: str):
    print("=" * 55)
    print("  ChatGPT Client  |  model:", model)
    print("  Nhập /help để xem lệnh đặc biệt")
    print("=" * 55)


def main():
    global COOKIE_STRING

    if not COOKIE_STRING:
        print("Dán cookie ChatGPT vào đây (nhấn Enter 2 lần để kết thúc):")
        lines = []
        while True:
            line = input()
            if line == "" and lines:
                break
            lines.append(line)
        COOKIE_STRING = " ".join(lines).strip()

    if not COOKIE_STRING:
        print("Lỗi: Chưa nhập cookie.")
        sys.exit(1)

    model = DEFAULT_MODEL
    _banner(model)

    try:
        client = ChatGPTClient(COOKIE_STRING, model=model)
    except Exception as e:
        print(f"✗ Không thể kết nối: {e}")
        sys.exit(1)

    while True:
        try:
            user_input = input("\nBạn: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nThoát.")
            break

        if not user_input:
            continue

        # ── Lệnh đặc biệt ──
        if user_input.startswith("/"):
            cmd_parts = user_input[1:].split(maxsplit=1)
            cmd = cmd_parts[0].lower()

            if cmd == "quit":
                print("Thoát.")
                break

            elif cmd == "new":
                client.conversation_id = None
                client.parent_msg_id   = str(uuid.uuid4())
                print("✓ Đã bắt đầu cuộc trò chuyện mới.")

            elif cmd == "model":
                if len(cmd_parts) < 2:
                    print(f"Model hiện tại: {client.model}")
                    print("Dùng: /model <tên model>")
                else:
                    new_model = cmd_parts[1].strip()
                    client.model = new_model
                    print(f"✓ Đã đổi sang model: {new_model}")

            elif cmd == "models":
                print("Models hỗ trợ:")
                for m in AVAILABLE_MODELS:
                    marker = " ← hiện tại" if m == client.model else ""
                    print(f"  {m}{marker}")

            elif cmd == "help":
                print(HELP_TEXT)

            else:
                print(f"Lệnh không hợp lệ: /{cmd}  —  Nhập /help để xem danh sách.")
            continue

        # ── Gửi tin nhắn ──
        print(f"\nChatGPT ({client.model}): ", end="", flush=True)
        try:
            client.send(user_input)
        except RuntimeError as e:
            print(f"\n✗ Lỗi: {e}")
        except Exception as e:
            print(f"\n✗ Lỗi không xác định: {e}")


if __name__ == "__main__":
    main()
