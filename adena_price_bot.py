"""
Bot lấy giá adena (Lineage Classic) ở 5 server và quy ra VNĐ.

Nguồn giá: https://enchant-lab.com/market (giá niêm yết bằng KRW - won Hàn).
Tỉ giá KRW -> VND: lấy từ https://open.er-api.com, lỗi thì dùng tỉ giá dự phòng.
Kết quả chỉ được print ra màn hình (không gửi Telegram, không ghi file).

Cài đặt:
    pip install selenium requests
    (cần có Google Chrome; Selenium >= 4.6 tự tải chromedriver phù hợp)

Chạy:
    python adena_price_bot.py
    python adena_price_bot.py --rate 18.6      # tự đặt tỉ giá 1 KRW = 18.6 VNĐ
    python adena_price_bot.py --show-browser   # mở Chrome có giao diện để debug
"""

import argparse
import re
import sys
import time

import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

MARKET_URL = "https://enchant-lab.com/market"
RATE_URL = "https://open.er-api.com/v6/latest/KRW"
TARGET_SERVERS = ["파아그리오", "안타라스", "글루디오", "군터", "데포로쥬"]
FALLBACK_KRW_TO_VND = 18.5  # tỉ giá dự phòng, chỉnh lại nếu cần


def get_krw_to_vnd_rate():
    """Trả về (tỉ giá, nguồn)."""
    try:
        res = requests.get(RATE_URL, timeout=10)
        res.raise_for_status()
        rate = float(res.json()["rates"]["VND"])
        return rate, "open.er-api.com"
    except Exception as e:
        print(f"[Tỉ giá] Không lấy được tỉ giá online ({e}), dùng tỉ giá dự phòng.")
        return FALLBACK_KRW_TO_VND, "dự phòng"


def get_driver(headless=True):
    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--lang=ko-KR")
    return webdriver.Chrome(options=options)


def fetch_page_text(headless=True):
    driver = get_driver(headless)
    try:
        driver.get(MARKET_URL)
        WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        # Trang render bằng JS: chờ tới khi tên server đầu tiên xuất hiện (tối đa 20s)
        deadline = time.time() + 20
        text = ""
        while time.time() < deadline:
            text = driver.find_element(By.TAG_NAME, "body").text
            if TARGET_SERVERS[0] in text and "원" in text:
                break
            time.sleep(1)
        return text
    finally:
        driver.quit()


def extract_server_data(full_text, server):
    """Tìm giá (KRW) và % thay đổi của 1 server. Trả về (giá_int | None, status)."""
    pattern = rf"{server}\s+평균[^\n\r]*[\n\r]+\s*([0-9,]+)원\s*[\n\r]+\s*([+-]?[0-9.]+%)"
    m = re.search(pattern, full_text)
    if m:
        return int(m.group(1).replace(",", "")), m.group(2)

    # Dự phòng: dò trong đoạn text ngay sau tên server
    idx = full_text.find(server)
    if idx == -1:
        return None, "-"
    price, status = None, "-"
    for line in (l.strip() for l in full_text[idx: idx + 400].splitlines()):
        if not line:
            continue
        if price is None and "원" in line and "평균" not in line and "최고" not in line:
            digits = re.sub(r"[^\d]", "", line)
            if digits and 0 < len(digits) <= 6 and int(digits) > 0:
                price = int(digits)
        if status == "-" and "%" in line:
            sm = re.search(r"([+-]?\d+(?:\.\d+)?%)", line)
            if sm:
                status = sm.group(1)
    return price, status


def main():
    parser = argparse.ArgumentParser(description="Lấy giá adena Lineage Classic và quy ra VNĐ")
    parser.add_argument("--rate", type=float, help="Tỉ giá 1 KRW = ? VNĐ (bỏ qua tỉ giá online)")
    parser.add_argument("--show-browser", action="store_true", help="Hiện cửa sổ Chrome")
    args = parser.parse_args()

    if args.rate:
        rate, rate_src = args.rate, "nhập tay"
    else:
        rate, rate_src = get_krw_to_vnd_rate()

    print(f"Đang lấy dữ liệu từ {MARKET_URL} ...")
    try:
        text = fetch_page_text(headless=not args.show_browser)
    except Exception as e:
        print(f"LỖI: không mở được trang ({e})")
        sys.exit(1)

    print()
    print(f"Tỉ giá: 1 KRW = {rate:,.2f} VNĐ ({rate_src})")
    print(f"{'Server':<12}{'Giá (KRW)':>12}{'Giá (VNĐ)':>14}{'Thay đổi':>11}")
    print("-" * 49)

    failed = []
    for server in TARGET_SERVERS:
        price, status = extract_server_data(text, server)
        if price is None:
            failed.append(server)
            print(f"{server:<12}{'không thấy':>12}{'-':>14}{status:>11}")
        else:
            print(f"{server:<12}{price:>10,}원{round(price * rate):>11,} ₫{status:>11}")

    print("-" * 49)
    print("Giá thường niêm yết theo 1만 (10.000) adena; hãy đối chiếu với trang nguồn.")
    if failed:
        print(f"Không lấy được giá: {', '.join(failed)} (trang có thể đã đổi giao diện).")
        sys.exit(1)


if __name__ == "__main__":
    main()
