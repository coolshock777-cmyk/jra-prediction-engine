import os
import re
import math
import uuid
from datetime import datetime, date

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup


# =========================================================
# 基本設定
# =========================================================

VERSION = "Ver.2.07"

HISTORY_FILE = "JRA_Prediction_History.csv"

NETKEIBA_RACE_URL = (
    "https://race.netkeiba.com/race/shutuba.html?race_id={race_id}"
)

NETKEIBA_ODDS_URL = (
    "https://race.netkeiba.com/odds/index.html"
    "?race_id={race_id}&rf=shutuba_submenu&type=b1"
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

VENUE_CODES = {
    "札幌": "01",
    "函館": "02",
    "福島": "03",
    "新潟": "04",
    "東京": "05",
    "中山": "06",
    "中京": "07",
    "京都": "08",
    "阪神": "09",
    "小倉": "10",
}

TURF_DISTANCES = [
    "1000m",
    "1200m",
    "1400m",
    "1500m",
    "1600m",
    "1800m",
    "2000m",
    "2200m",
    "2300m",
    "2400m",
    "2500m",
    "2600m",
    "3000m",
    "3200m",
    "3400m",
    "3600m",
]

DIRT_DISTANCES = [
    "1000m",
    "1150m",
    "1200m",
    "1300m",
    "1400m",
    "1600m",
    "1700m",
    "1800m",
    "1900m",
    "2000m",
    "2100m",
    "2400m",
]

STYLE_LABELS = {
    "逃": "逃げ",
    "先": "先行",
    "差": "差し",
    "追": "追込",
}


# =========================================================
# Streamlit設定
# =========================================================

st.set_page_config(
    page_title="JRA Prediction Engine",
    page_icon="🏇",
    layout="wide",
)


# =========================================================
# ログイン
# =========================================================

def check_login():
    app_password = st.secrets.get("APP_PASSWORD", "")

    if not app_password:
        return True

    if st.session_state.get("logged_in", False):
        return True

    st.title("🏇 JRA Prediction Engine")
    st.caption(VERSION)

    password = st.text_input(
        "パスワード",
        type="password",
        key="login_password",
    )

    if st.button("ログイン", type="primary"):
        if password == app_password:
            st.session_state["logged_in"] = True
            st.rerun()
        else:
            st.error("パスワードが違います。")

    return False


if not check_login():
    st.stop()


# =========================================================
# HTTP共通処理
# =========================================================

def make_headers():
    return {
        "User-Agent": USER_AGENT,
        "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
        "Referer": "https://race.netkeiba.com/",
        "Connection": "keep-alive",
    }


def decode_response(response):
    """
    netkeibaページの文字コードをなるべく安全に決定。
    """
    content_type = response.headers.get("Content-Type", "").lower()

    if "charset=utf-8" in content_type:
        response.encoding = "utf-8"
    elif "euc-jp" in content_type:
        response.encoding = "euc-jp"
    else:
        # 現在のnetkeibaはUTF-8系ページも多いため、
        # apparent_encodingだけに依存しない。
        apparent = response.apparent_encoding

        if apparent:
            response.encoding = apparent
        else:
            response.encoding = "utf-8"

    return response.text


# =========================================================
# 文字列・数値ユーティリティ
# =========================================================

def normalize_text(text):
    if text is None:
        return ""

    text = str(text)
    text = text.replace("\xa0", " ")
    text = text.replace("\u3000", " ")
    return re.sub(r"\s+", " ", text).strip()


def normalize_horse_name(text):
    text = normalize_text(text)

    # netkeiba上の余計な記号を軽く除去
    text = text.replace("★", "")
    text = text.replace("△", "")
    text = text.replace("▲", "")
    text = text.replace("○", "")
    text = text.replace("◎", "")

    return text.strip()


def parse_int(text):
    if text is None:
        return None

    text = normalize_text(text)
    match = re.search(r"\d+", text)

    if not match:
        return None

    try:
        return int(match.group(0))
    except Exception:
        return None


def parse_float(text):
    if text is None:
        return None

    text = normalize_text(text)
    match = re.search(r"\d+(?:\.\d+)?", text)

    if not match:
        return None

    try:
        return float(match.group(0))
    except Exception:
        return None


# =========================================================
# オッズ解析
# =========================================================

def parse_odds(text):
    """
    単勝オッズ専用。
    例:
      1.8 -> 1.8
      10.2 -> 10.2
      123.4 -> 123.4
      ---.- -> None
      取消 -> None
      除外 -> None
    """

    if text is None:
        return None

    text = normalize_text(text)

    if not text:
        return None

    invalid_values = {
        "---.-",
        "---",
        "--.-",
        "-",
        "取消",
        "除外",
        "発走除外",
        "取止",
        "中止",
        "出走取消",
    }

    if text in invalid_values:
        return None

    # オッズページの通常表記
    match = re.search(
        r"(?<!\d)(\d{1,4}(?:\.\d+)?)(?!\d)",
        text,
    )

    if not match:
        return None

    try:
        value = float(match.group(1))
    except Exception:
        return None

    if value < 1.0:
        return None

    return value


# =========================================================
# 脚質解析
# =========================================================

def extract_running_style(row):
    """
    netkeiba側のHTML変更に備えて複数候補から脚質を取得する。

    重要:
    脚質がHTML上に存在しない場合は推測しない。
    その場合は「不明」を返す。
    """

    # -----------------------------------------------------
    # 1. class名から探す
    # -----------------------------------------------------

    class_pattern = re.compile(
        r"Kyakushitsu|Kyakusitsu|Style|RunningStyle|Running",
        re.IGNORECASE,
    )

    candidates = row.find_all(
        ["td", "th", "span", "div", "p"],
        class_=class_pattern,
    )

    for elem in candidates:
        text = normalize_text(elem.get_text(" ", strip=True))

        if not text:
            continue

        # 逃げ / 先行 / 差し / 追込
        for key, label in STYLE_LABELS.items():
            if key in text:
                return label

        # 「逃」「先」など単独表示
        if text in STYLE_LABELS:
            return STYLE_LABELS[text]

    # -----------------------------------------------------
    # 2. data-* 属性から探す
    # -----------------------------------------------------

    for elem in row.find_all(True):
        attrs = elem.attrs

        for attr_name, attr_value in attrs.items():
            attr_name_lower = str(attr_name).lower()

            if (
                "style" in attr_name_lower
                or "kyaku" in attr_name_lower
                or "脚質" in attr_name_lower
            ):
                if isinstance(attr_value, list):
                    value = " ".join(map(str, attr_value))
                else:
                    value = str(attr_value)

                value = normalize_text(value)

                for key, label in STYLE_LABELS.items():
                    if key in value:
                        return label

    # -----------------------------------------------------
    # 3. 「脚質：逃げ」のような表示を探す
    # -----------------------------------------------------

    row_text = normalize_text(row.get_text(" ", strip=True))

    match = re.search(
        r"脚質\s*[:：]?\s*(逃げ|先行|差し|追込|逃|先|差|追)",
        row_text,
    )

    if match:
        value = match.group(1)

        if value in STYLE_LABELS:
            return STYLE_LABELS[value]

        return value

    return "不明"


# =========================================================
# 枠番・馬番解析
# =========================================================

def extract_horse_number(row):
    """
    出馬表のHorseListから馬番を取得。
    """

    # class名から探す
    class_pattern = re.compile(
        r"Umaban|HorseNum|HorseNo|Num",
        re.IGNORECASE,
    )

    elems = row.find_all(
        ["td", "th", "span", "div"],
        class_=class_pattern,
    )

    for elem in elems:
        text = normalize_text(elem.get_text(" ", strip=True))

        if re.fullmatch(r"\d{1,2}", text):
            number = int(text)

            if 1 <= number <= 18:
                return number

    # 一般的な出馬表では馬番が2番目のセルにあることが多い
    cells = row.find_all(["td", "th"])

    texts = [
        normalize_text(cell.get_text(" ", strip=True))
        for cell in cells
    ]

    if len(texts) >= 2:
        candidate = texts[1]

        if re.fullmatch(r"\d{1,2}", candidate):
            number = int(candidate)

            if 1 <= number <= 18:
                return number

    # 最後の保険
    for text in texts:
        if re.fullmatch(r"\d{1,2}", text):
            number = int(text)

            if 1 <= number <= 18:
                return number

    return None


def extract_frame_number(row):
    """
    枠番を取得。
    """

    class_pattern = re.compile(
        r"Waku|Frame",
        re.IGNORECASE,
    )

    elems = row.find_all(
        ["td", "th", "span", "div"],
        class_=class_pattern,
    )

    for elem in elems:
        text = normalize_text(elem.get_text(" ", strip=True))

        if re.fullmatch(r"\d", text):
            value = int(text)

            if 1 <= value <= 8:
                return value

    # 枠番画像のalt等
    for elem in row.find_all(True):
        for attr_name in ["alt", "title", "data-waku"]:
            value = elem.get(attr_name)

            if value:
                text = normalize_text(value)

                match = re.search(r"(\d)", text)

                if match:
                    frame = int(match.group(1))

                    if 1 <= frame <= 8:
                        return frame

    # 最初のセル
    cells = row.find_all(["td", "th"])

    if cells:
        text = normalize_text(cells[0].get_text(" ", strip=True))

        if re.fullmatch(r"\d", text):
            value = int(text)

            if 1 <= value <= 8:
                return value

    return None


# =========================================================
# 騎手・馬名・斤量解析
# =========================================================

def extract_horse_name(row):
    """
    馬名を複数候補から取得。
    """

    candidates = row.find_all(
        ["a", "span", "div"],
        class_=re.compile(
            r"Bamei|HorseName|Horse|UmaName",
            re.IGNORECASE,
        ),
    )

    for elem in candidates:
        text = normalize_horse_name(
            elem.get_text(" ", strip=True)
        )

        if text and len(text) >= 2:
            return text

    # horse_idリンク周辺
    for link in row.find_all("a", href=True):
        href = link.get("href", "")

        if "/horse/" in href:
            text = normalize_horse_name(
                link.get_text(" ", strip=True)
            )

            if text:
                return text

    # 最後の保険
    cells = row.find_all("td")

    for cell in cells:
        text = normalize_horse_name(
            cell.get_text(" ", strip=True)
        )

        if (
            text
            and len(text) >= 2
            and not re.fullmatch(r"\d+", text)
            and "kg" not in text.lower()
        ):
            # 騎手名らしいセルは除外しやすくする
            if "騎手" not in text:
                return text

    return "不明"


def extract_jockey(row):
    candidates = row.find_all(
        ["a", "span", "div"],
        class_=re.compile(
            r"Jockey|JockeyName|Kisyu",
            re.IGNORECASE,
        ),
    )

    for elem in candidates:
        text = normalize_text(
            elem.get_text(" ", strip=True)
        )

        if text:
            return text

    # /jockey/ リンク
    for link in row.find_all("a", href=True):
        href = link.get("href", "")

        if "/jockey/" in href:
            text = normalize_text(
                link.get_text(" ", strip=True)
            )

            if text:
                return text

    return "不明"


def extract_weight(row):
    """
    斤量。
    例: 56.0 / 56
    """

    candidates = row.find_all(
        ["td", "span", "div"],
        class_=re.compile(
            r"Weight|Futan|Futankin|Bataiju",
            re.IGNORECASE,
        ),
    )

    for elem in candidates:
        value = parse_float(
            elem.get_text(" ", strip=True)
        )

        if value is not None and 40 <= value <= 70:
            return value

    # セル全体から 40～70kg を探す
    for cell in row.find_all("td"):
        text = normalize_text(
            cell.get_text(" ", strip=True)
        )

        match = re.search(
            r"(?<!\d)(4\d(?:\.\d)?|5\d(?:\.\d)?|6\d(?:\.\d)?|70(?:\.0)?)(?:kg)?",
            text,
        )

        if match:
            try:
                value = float(match.group(1))

                if 40 <= value <= 70:
                    return value
            except Exception:
                pass

    return None


# =========================================================
# 出馬表取得
# =========================================================

@st.cache_data(ttl=30, show_spinner=False)
def fetch_race_card(race_id):
    url = NETKEIBA_RACE_URL.format(race_id=race_id)

    response = requests.get(
        url,
        headers=make_headers(),
        timeout=20,
    )

    response.raise_for_status()

    html = decode_response(response)

    soup = BeautifulSoup(html, "html.parser")

    # -----------------------------------------------------
    # レース情報
    # -----------------------------------------------------

    race_name = "不明"

    title = soup.find("h1")

    if title:
        race_name = normalize_text(
            title.get_text(" ", strip=True)
        )

    race_info_text = ""

    race_data = soup.find(
        "div",
        class_=re.compile(r"RaceData01"),
    )

    if race_data:
        race_info_text = normalize_text(
            race_data.get_text(" ", strip=True)
        )

    # 日付
    race_date = None

    date_match = re.search(
        r"(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日",
        soup.get_text(" ", strip=True),
    )

    if date_match:
        try:
            race_date = date(
                int(date_match.group(1)),
                int(date_match.group(2)),
                int(date_match.group(3)),
            )
        except Exception:
            race_date = None

    # -----------------------------------------------------
    # 場所
    # -----------------------------------------------------

    venue = "不明"

    for venue_name in VENUE_CODES:
        if venue_name in race_info_text:
            venue = venue_name
            break

    if venue == "不明":
        for venue_name in VENUE_CODES:
            if venue_name in soup.get_text(" ", strip=True):
                venue = venue_name
                break

    # -----------------------------------------------------
    # 距離・芝ダ
    # -----------------------------------------------------

    track_type = "不明"
    distance = None
    condition = "不明"

    track_match = re.search(
        r"(芝|ダート|ダ)\s*(\d{3,4})m",
        race_info_text,
    )

    if track_match:
        raw_track = track_match.group(1)
        distance = int(track_match.group(2))

        if raw_track == "芝":
            track_type = "芝"
        else:
            track_type = "ダート"

    # HTML全体からの保険
    if distance is None:
        all_text = soup.get_text(" ", strip=True)

        match = re.search(
            r"(芝|ダート|ダ)\s*(\d{3,4})m",
            all_text,
        )

        if match:
            distance = int(match.group(2))

            if match.group(1) == "芝":
                track_type = "芝"
            else:
                track_type = "ダート"

    # 馬場状態
    for cond in ["良", "稍重", "重", "不良"]:
        if f"馬場:{cond}" in race_info_text:
            condition = cond
            break

        if f"馬場：{cond}" in race_info_text:
            condition = cond
            break

    # -----------------------------------------------------
    # 出走馬
    # -----------------------------------------------------

    horse_rows = soup.find_all(
        "tr",
        class_=re.compile(r"HorseList"),
    )

    horses = []

    for row in horse_rows:
        horse_number = extract_horse_number(row)

        if horse_number is None:
            continue

        frame_number = extract_frame_number(row)

        horse_name = extract_horse_name(row)

        jockey = extract_jockey(row)

        weight = extract_weight(row)

        running_style = extract_running_style(row)

        horses.append(
            {
                "枠": frame_number,
                "馬番": horse_number,
                "馬名": horse_name,
                "騎手": jockey,
                "斤量": weight,
                "脚質": running_style,
                "オッズ": None,
            }
        )

    if not horses:
        raise RuntimeError(
            "出走馬を取得できませんでした。"
            "netkeiba側のHTML構造変更またはアクセス制限の可能性があります。"
        )

    # 馬番順
    horses = sorted(
        horses,
        key=lambda x: x["馬番"],
    )

    return {
        "race_id": race_id,
        "race_name": race_name,
        "race_date": race_date,
        "venue": venue,
        "track_type": track_type,
        "distance": distance,
        "condition": condition,
        "race_info_text": race_info_text,
        "horses": horses,
    }


# =========================================================
# オッズページ解析
# =========================================================

def extract_odds_from_table(table):
    """
    単勝表から
        馬番 -> オッズ
    の辞書を作る。

    netkeibaの単勝表は
        枠 / 馬番 / 印 / 選択 / 馬名 / オッズ
    の構造になっているため、
    馬番と最後の数値セルを中心に解析する。
    """

    odds_map = {}

    rows = table.find_all("tr")

    for row in rows:
        cells = row.find_all(["td", "th"])

        if len(cells) < 3:
            continue

        cell_texts = [
            normalize_text(
                cell.get_text(" ", strip=True)
            )
            for cell in cells
        ]

        # -------------------------------------------------
        # 馬番
        # -------------------------------------------------

        horse_number = None

        # classから探す
        for cell in cells:
            class_text = " ".join(
                cell.get("class", [])
            )

            if re.search(
                r"Umaban|HorseNum|HorseNo|Num",
                class_text,
                re.IGNORECASE,
            ):
                text = normalize_text(
                    cell.get_text(" ", strip=True)
                )

                if re.fullmatch(r"\d{1,2}", text):
                    value = int(text)

                    if 1 <= value <= 18:
                        horse_number = value
                        break

        # 通常構造: 2番目セルが馬番
        if horse_number is None and len(cell_texts) >= 2:
            candidate = cell_texts[1]

            if re.fullmatch(r"\d{1,2}", candidate):
                value = int(candidate)

                if 1 <= value <= 18:
                    horse_number = value

        if horse_number is None:
            continue

        # -------------------------------------------------
        # オッズ
        # -------------------------------------------------

        odds = None

        # Oddsクラスを優先
        for cell in reversed(cells):
            class_text = " ".join(
                cell.get("class", [])
            )

            if re.search(
                r"Odds|OddsValue",
                class_text,
                re.IGNORECASE,
            ):
                odds = parse_odds(
                    cell.get_text(" ", strip=True)
                )

                if odds is not None:
                    break

        # 最後のセルから探す
        if odds is None:
            for text in reversed(cell_texts):
                value = parse_odds(text)

                if value is not None:
                    odds = value
                    break

        odds_map[horse_number] = odds

    return odds_map


def parse_odds_page(html):
    soup = BeautifulSoup(html, "html.parser")

    odds_map = {}

    # -----------------------------------------------------
    # 「単勝」表を優先
    # -----------------------------------------------------

    tables = soup.find_all("table")

    target_tables = []

    for table in tables:
        text = normalize_text(
            table.get_text(" ", strip=True)
        )

        if (
            "単勝" in text
            and "馬番" in text
            and "オッズ" in text
        ):
            target_tables.append(table)

    # 単勝表が取れない場合、馬番・オッズ表を探す
    if not target_tables:
        for table in tables:
            text = normalize_text(
                table.get_text(" ", strip=True)
            )

            if "馬番" in text and "オッズ" in text:
                target_tables.append(table)

    for table in target_tables:
        parsed = extract_odds_from_table(table)

        for horse_number, odds in parsed.items():
            odds_map[horse_number] = odds

        # 18頭分以上取れたら十分
        if len(odds_map) >= 18:
            break

    return odds_map


# =========================================================
# オッズ取得
# =========================================================

@st.cache_data(ttl=10, show_spinner=False)
def fetch_race_odds(race_id):
    url = NETKEIBA_ODDS_URL.format(
        race_id=race_id
    )

    try:
        response = requests.get(
            url,
            headers=make_headers(),
            timeout=20,
        )

        response.raise_for_status()

        html = decode_response(response)

        odds_map = parse_odds_page(html)

        return {
            "success": True,
            "url": url,
            "odds_map": odds_map,
            "error": None,
        }

    except Exception as e:
        return {
            "success": False,
            "url": url,
            "odds_map": {},
            "error": str(e),
        }


# =========================================================
# 出馬表 + オッズ統合
# =========================================================

def merge_race_data(race_data, odds_data):
    horses = race_data["horses"]

    odds_map = odds_data.get(
        "odds_map",
        {},
    )

    merged = []

    for horse in horses:
        horse_copy = dict(horse)

        horse_number = horse_copy["馬番"]

        horse_copy["オッズ"] = odds_map.get(
            horse_number
        )

        merged.append(horse_copy)

    race_data = dict(race_data)
    race_data["horses"] = merged

    valid_odds_count = sum(
        1
        for horse in merged
        if isinstance(horse.get("オッズ"), (int, float))
        and horse["オッズ"] >= 1.0
    )

    total_horses = len(merged)

    if total_horses > 0:
        odds_coverage = (
            valid_odds_count / total_horses
        )
    else:
        odds_coverage = 0.0

    if odds_coverage >= 0.99:
        odds_status = "当日確定"
    elif odds_coverage >= 0.70:
        odds_status = "当日一部欠損"
    else:
        odds_status = "前日暫定 / 未取得"

    style_count = sum(
        1
        for horse in merged
        if horse.get("脚質")
        and horse.get("脚質") != "不明"
    )

    style_coverage = (
        style_count / total_horses
        if total_horses > 0
        else 0.0
    )

    race_data["valid_odds_count"] = valid_odds_count
    race_data["odds_coverage"] = odds_coverage
    race_data["odds_status"] = odds_status

    race_data["style_count"] = style_count
    race_data["style_coverage"] = style_coverage

    race_data["odds_fetch_success"] = odds_data.get(
        "success",
        False,
    )

    race_data["odds_fetch_error"] = odds_data.get(
        "error"
    )

    race_data["odds_url"] = odds_data.get(
        "url",
        "",
    )

    return race_data


# =========================================================
# レースID
# =========================================================

def build_race_id(
    selected_date,
    venue,
    kai,
    nichi,
    race_number,
):
    """
    JRA/netkeiba 12桁race_id
    YYYY + 場コード + 回 + 日 + R
    """

    year = selected_date.year

    venue_code = VENUE_CODES.get(
        venue,
        "01",
    )

    return (
        f"{year:04d}"
        f"{venue_code}"
        f"{int(kai):02d}"
        f"{int(nichi):02d}"
        f"{int(race_number):02d}"
    )


# =========================================================
# モデル
# =========================================================

def calculate_model_score(
    horse,
    use_front_bias=True,
    use_inside_bias=True,
    use_outer_bias=True,
    use_green_belt=False,
    use_g1_sign=False,
    track_type="芝",
    distance=1200,
):
    """
    市場オッズをモデルスコア生成には使用しない。

    オッズはValue Indexの比較対象としてのみ使用する。
    """

    score = 0.0

    # -----------------------------------------------------
    # 騎手
    # -----------------------------------------------------

    jockey = str(
        horse.get("騎手", "")
    ).replace(" ", "").replace("　", "")

    if (
        "ルメール" in jockey
        or "川田" in jockey
    ):
        score += math.log(1.15)

    # -----------------------------------------------------
    # 脚質
    # -----------------------------------------------------

    style = str(
        horse.get("脚質", "")
    )

    if use_front_bias:
        if (
            "逃" in style
            or "先" in style
        ):
            score += math.log(1.08)

    # -----------------------------------------------------
    # 枠
    # -----------------------------------------------------

    frame = horse.get("枠")

    try:
        frame = int(frame)
    except Exception:
        frame = None

    if use_inside_bias and frame in [1, 2]:
        score += math.log(1.05)

    if use_outer_bias and frame in [7, 8]:
        score += math.log(1.05)

    # -----------------------------------------------------
    # ダート1200m内枠
    # -----------------------------------------------------

    try:
        distance_value = int(distance)
    except Exception:
        distance_value = 0

    if (
        track_type == "ダート"
        and distance_value == 1200
        and frame in [1, 2, 3]
    ):
        score += math.log(1.08)

    # -----------------------------------------------------
    # グリーンベルト
    # -----------------------------------------------------

    if use_green_belt:
        if horse.get("馬番") == 1:
            score += math.log(1.06)

    # -----------------------------------------------------
    # G1サイン
    # -----------------------------------------------------

    if use_g1_sign:
        if horse.get("馬番") in [1, 3, 7]:
            score += math.log(1.03)

    return score


def calculate_model_shares(
    horses,
    **model_kwargs,
):
    if not horses:
        return []

    scores = []

    for horse in horses:
        score = calculate_model_score(
            horse,
            **model_kwargs,
        )

        scores.append(score)

    # 数値安定化
    max_score = max(scores)

    exp_scores = [
        math.exp(score - max_score)
        for score in scores
    ]

    total = sum(exp_scores)

    if total <= 0:
        total = 1.0

    result = []

    for horse, score, exp_score in zip(
        horses,
        scores,
        exp_scores,
    ):
        item = dict(horse)

        share = exp_score / total

        item["モデルスコア"] = score
        item["モデルシェア"] = share

        result.append(item)

    return result


# =========================================================
# Value Index
# =========================================================

def calculate_value_index(horse):
    odds = horse.get("オッズ")
    share = horse.get("モデルシェア")

    if not isinstance(odds, (int, float)):
        return None

    if odds <= 0:
        return None

    if not isinstance(share, (int, float)):
        return None

    market_prob = 1.0 / odds

    if market_prob <= 0:
        return None

    return share / market_prob


def add_value_indices(horses, odds_status):
    result = []

    odds_available = odds_status in [
        "当日確定",
        "当日一部欠損",
    ]

    for horse in horses:
        item = dict(horse)

        if odds_available:
            item["Value Index"] = (
                calculate_value_index(item)
            )
        else:
            item["Value Index"] = None

        result.append(item)

    return result


# =========================================================
# CSV履歴
# =========================================================

HISTORY_COLUMNS = [
    "予測ログID",
    "予測日時",
    "モデルバージョン",
    "Race ID",
    "開催日",
    "競馬場",
    "レース名",
    "距離",
    "馬場",
    "馬番",
    "馬名",
    "騎手",
    "斤量",
    "脚質",
    "オッズ",
    "モデルシェア",
    "Value Index",
    "順位",
    "推奨区分",
    "購入額",
    "結果",
    "払戻",
    "収支",
    "メモ",
]


def load_history_df():
    if not os.path.exists(HISTORY_FILE):
        return pd.DataFrame(
            columns=HISTORY_COLUMNS
        )

    try:
        df = pd.read_csv(
            HISTORY_FILE,
            encoding="utf-8-sig",
        )

        for column in HISTORY_COLUMNS:
            if column not in df.columns:
                df[column] = None

        return df[HISTORY_COLUMNS]

    except Exception:
        return pd.DataFrame(
            columns=HISTORY_COLUMNS
        )


def save_history_df(df):
    df.to_csv(
        HISTORY_FILE,
        index=False,
        encoding="utf-8-sig",
    )


def create_prediction_log_id(
    race_id,
):
    timestamp = datetime.now().strftime(
        "%Y%m%d%H%M%S"
    )

    random_part = uuid.uuid4().hex[:6]

    return (
        f"{race_id}_"
        f"{timestamp}_"
        f"{random_part}_"
        f"{VERSION.replace('.', '')}"
    )


def append_prediction_log(
    race_data,
    horses,
    log_id,
    date_value,
    track_type,
    distance,
    condition,
    budget,
):
    history_df = load_history_df()

    sorted_horses = sorted(
        horses,
        key=lambda x: x.get(
            "モデルシェア",
            0,
        ),
        reverse=True,
    )

    rows = []

    for rank, horse in enumerate(
        sorted_horses,
        start=1,
    ):
        if rank == 1:
            category = "本命"
            purchase = budget * 0.50
        elif rank == 2:
            category = "相手"
            purchase = budget * 0.30
        elif rank == 3:
            category = "相手"
            purchase = budget * 0.20
        else:
            category = ""

            purchase = 0

        rows.append(
            {
                "予測ログID": log_id,
                "予測日時": datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                "モデルバージョン": VERSION,
                "Race ID": race_data["race_id"],
                "開催日": str(date_value),
                "競馬場": race_data["venue"],
                "レース名": race_data["race_name"],
                "距離": (
                    f"{track_type}{distance}m"
                    if distance
                    else ""
                ),
                "馬場": condition,
                "馬番": horse.get("馬番"),
                "馬名": horse.get("馬名"),
                "騎手": horse.get("騎手"),
                "斤量": horse.get("斤量"),
                "脚質": horse.get("脚質"),
                "オッズ": horse.get("オッズ"),
                "モデルシェア": horse.get(
                    "モデルシェア"
                ),
                "Value Index": horse.get(
                    "Value Index"
                ),
                "順位": rank,
                "推奨区分": category,
                "購入額": purchase,
                "結果": "",
                "払戻": 0,
                "収支": 0,
                "メモ": "",
            }
        )

    new_df = pd.DataFrame(rows)

    history_df = pd.concat(
        [
            history_df,
            new_df,
        ],
        ignore_index=True,
    )

    save_history_df(history_df)

    return history_df


# =========================================================
# UI
# =========================================================

st.title("🏇 JRA Prediction Engine")
st.caption(f"{VERSION} | 市場オッズ非依存モデル")

st.divider()


# =========================================================
# サイドバー
# =========================================================

with st.sidebar:
    st.header("レース設定")

    selected_date = st.date_input(
        "開催日",
        value=date.today(),
    )

    venue = st.selectbox(
        "競馬場",
        list(VENUE_CODES.keys()),
    )

    kai = st.number_input(
        "開催回",
        min_value=1,
        max_value=6,
        value=1,
        step=1,
    )

    nichi = st.number_input(
        "開催日目",
        min_value=1,
        max_value=12,
        value=1,
        step=1,
    )

    race_number = st.number_input(
        "レース番号",
        min_value=1,
        max_value=12,
        value=11,
        step=1,
    )

    st.divider()

    st.subheader("モデル設定")

    use_front_bias = st.checkbox(
        "逃げ・先行バイアス",
        value=True,
    )

    use_inside_bias = st.checkbox(
        "内枠バイアス",
        value=True,
    )

    use_outer_bias = st.checkbox(
        "外枠バイアス",
        value=True,
    )

    use_green_belt = st.checkbox(
        "グリーンベルト",
        value=False,
    )

    use_g1_sign = st.checkbox(
        "G1サイン",
        value=False,
    )

    st.divider()

    budget = st.number_input(
        "購入予算",
        min_value=0,
        value=10000,
        step=1000,
    )


# =========================================================
# Race ID
# =========================================================

race_id = build_race_id(
    selected_date,
    venue,
    kai,
    nichi,
    race_number,
)

st.subheader("レース情報")

col1, col2, col3, col4 = st.columns(4)

with col1:
    st.metric(
        "Race ID",
        race_id,
    )

with col2:
    st.metric(
        "競馬場",
        venue,
    )

with col3:
    st.metric(
        "R",
        f"{race_number}R",
    )

with col4:
    st.metric(
        "モデル",
        VERSION,
    )


# =========================================================
# データ取得
# =========================================================

if st.button(
    "🔄 出馬表・オッズを取得",
    type="primary",
    use_container_width=True,
):
    with st.spinner("netkeibaから取得中..."):
        try:
            race_data = fetch_race_card(
                race_id
            )

            odds_data = fetch_race_odds(
                race_id
            )

            race_data = merge_race_data(
                race_data,
                odds_data,
            )

            st.session_state[
                "race_data"
            ] = race_data

            st.success(
                "レースデータを取得しました。"
            )

        except Exception as e:
            st.error(
                f"データ取得エラー: {e}"
            )


# =========================================================
# セッションから取得
# =========================================================

race_data = st.session_state.get(
    "race_data"
)

if race_data is None:
    st.info(
        "「出馬表・オッズを取得」を押してください。"
    )

    st.stop()


# =========================================================
# レース概要
# =========================================================

st.subheader(
    f"{race_data['race_name']}"
)

info_col1, info_col2, info_col3, info_col4 = st.columns(4)

with info_col1:
    st.metric(
        "開催",
        race_data["venue"],
    )

with info_col2:
    distance_text = (
        f"{race_data['track_type']}"
        f"{race_data['distance']}m"
        if race_data["distance"]
        else "不明"
    )

    st.metric(
        "コース",
        distance_text,
    )

with info_col3:
    st.metric(
        "馬場",
        race_data["condition"],
    )

with info_col4:
    st.metric(
        "オッズ取得",
        f"{race_data['valid_odds_count']}"
        f"/{len(race_data['horses'])}",
    )


# =========================================================
# 取得診断
# =========================================================

with st.expander(
    "🔎 データ取得診断",
    expanded=False,
):
    odds_coverage = race_data.get(
        "odds_coverage",
        0,
    )

    style_coverage = race_data.get(
        "style_coverage",
        0,
    )

    st.write(
        "**オッズ取得元**: netkeiba 単勝オッズページ"
    )

    st.write(
        f"**オッズ取得件数**: "
        f"{race_data.get('valid_odds_count', 0)}"
        f"/"
        f"{len(race_data['horses'])}"
    )

    st.write(
        f"**オッズ取得率**: "
        f"{odds_coverage:.1%}"
    )

    st.write(
        f"**脚質取得件数**: "
        f"{race_data.get('style_count', 0)}"
        f"/"
        f"{len(race_data['horses'])}"
    )

    st.write(
        f"**脚質取得率**: "
        f"{style_coverage:.1%}"
    )

    st.write(
        f"**オッズ状態**: "
        f"{race_data.get('odds_status', '不明')}"
    )

    if race_data.get(
        "odds_fetch_error"
    ):
        st.error(
            "オッズページ取得エラー: "
            + str(
                race_data["odds_fetch_error"]
            )
        )

    st.caption(
        race_data.get(
            "odds_url",
            "",
        )
    )

    if odds_coverage == 0:
        st.warning(
            "単勝オッズを数値として取得できていません。"
            "発売前などでnetkeiba側が「---.-」の場合も"
            "ここに含まれます。"
        )

    if style_coverage < 0.70:
        st.warning(
            "脚質の取得率が70%未満です。"
            "netkeibaの出馬表HTMLに脚質情報が存在しない場合、"
            "推測せず「不明」としています。"
        )


# =========================================================
# モデル計算
# =========================================================

track_type = race_data.get(
    "track_type",
    "芝",
)

distance = race_data.get(
    "distance",
    1200,
)

condition = race_data.get(
    "condition",
    "不明",
)

model_horses = calculate_model_shares(
    race_data["horses"],
    use_front_bias=use_front_bias,
    use_inside_bias=use_inside_bias,
    use_outer_bias=use_outer_bias,
    use_green_belt=use_green_belt,
    use_g1_sign=use_g1_sign,
    track_type=track_type,
    distance=distance,
)

model_horses = add_value_indices(
    model_horses,
    race_data.get(
        "odds_status",
        "前日暫定 / 未取得",
    ),
)


# =========================================================
# 表示用DataFrame
# =========================================================

display_rows = []

for horse in model_horses:
    share = horse.get(
        "モデルシェア"
    )

    value_index = horse.get(
        "Value Index"
    )

    display_rows.append(
        {
            "枠": horse.get("枠"),
            "馬番": horse.get("馬番"),
            "馬名": horse.get("馬名"),
            "騎手": horse.get("騎手"),
            "斤量": horse.get("斤量"),
            "脚質": horse.get("脚質"),
            "オッズ": horse.get("オッズ"),
            "モデルシェア": (
                f"{share:.1%}"
                if isinstance(
                    share,
                    (int, float),
                )
                else "-"
            ),
            "Value Index": (
                f"{value_index:.2f}"
                if isinstance(
                    value_index,
                    (int, float),
                )
                else "-"
            ),
        }
    )

display_df = pd.DataFrame(
    display_rows
)


# =========================================================
# 脚質警告
# =========================================================

if race_data.get(
    "style_coverage",
    0,
) < 0.70:

    st.warning(
        "⚠️ 脚質取得率が70%未満です。"
        "脚質バイアスをONにしている場合、"
        "脚質が「不明」の馬にはその補正は入りません。"
    )


# =========================================================
# オッズ状態
# =========================================================

odds_status = race_data.get(
    "odds_status",
    "前日暫定 / 未取得",
)

if odds_status == "当日確定":
    st.success(
        f"💰 オッズ: {odds_status} "
        f"（{race_data['valid_odds_count']}"
        f"/{len(race_data['horses'])}頭）"
    )

elif odds_status == "当日一部欠損":
    st.warning(
        f"💰 オッズ: {odds_status} "
        f"（{race_data['valid_odds_count']}"
        f"/{len(race_data['horses'])}頭）"
    )

else:
    st.info(
        f"💰 オッズ: {odds_status} "
        f"（{race_data['valid_odds_count']}"
        f"/{len(race_data['horses'])}頭）"
    )


# =========================================================
# 出走馬一覧
# =========================================================

st.subheader("出走馬・モデル評価")

st.dataframe(
    display_df,
    use_container_width=True,
    hide_index=True,
)


# =========================================================
# 上位馬
# =========================================================

ranked_horses = sorted(
    model_horses,
    key=lambda x: x.get(
        "モデルシェア",
        0,
    ),
    reverse=True,
)

if ranked_horses:

    top1 = ranked_horses[0]

    st.subheader("🎯 モデル上位")

    c1, c2, c3 = st.columns(3)

    with c1:
        st.metric(
            "本命",
            f"{top1['馬番']} "
            f"{top1['馬名']}",
        )

        st.caption(
            f"モデルシェア "
            f"{top1['モデルシェア']:.1%}"
        )

    if len(ranked_horses) >= 2:
        top2 = ranked_horses[1]

        with c2:
            st.metric(
                "相手1",
                f"{top2['馬番']} "
                f"{top2['馬名']}",
            )

            st.caption(
                f"モデルシェア "
                f"{top2['モデルシェア']:.1%}"
            )

    if len(ranked_horses) >= 3:
        top3 = ranked_horses[2]

        with c3:
            st.metric(
                "相手2",
                f"{top3['馬番']} "
                f"{top3['馬名']}",
            )

            st.caption(
                f"モデルシェア "
                f"{top3['モデルシェア']:.1%}"
            )


# =========================================================
# Value Index 上位
# =========================================================

valid_value_horses = [
    horse
    for horse in model_horses
    if isinstance(
        horse.get("Value Index"),
        (int, float),
    )
]

if valid_value_horses:

    value_sorted = sorted(
        valid_value_horses,
        key=lambda x: x["Value Index"],
        reverse=True,
    )

    st.subheader("💎 Value Index 上位")

    value_rows = []

    for horse in value_sorted[:5]:
        value_rows.append(
            {
                "馬番": horse["馬番"],
                "馬名": horse["馬名"],
                "オッズ": horse["オッズ"],
                "モデルシェア": (
                    f"{horse['モデルシェア']:.1%}"
                ),
                "Value Index": (
                    f"{horse['Value Index']:.2f}"
                ),
            }
        )

    st.dataframe(
        pd.DataFrame(value_rows),
        use_container_width=True,
        hide_index=True,
    )

else:
    st.info(
        "Value Indexは有効な単勝オッズ取得後に表示されます。"
    )


# =========================================================
# 50/30/20 購入配分
# =========================================================

st.subheader("💰 購入配分")

if len(ranked_horses) >= 3:

    purchase_rows = [
        {
            "区分": "本命",
            "馬番": ranked_horses[0]["馬番"],
            "馬名": ranked_horses[0]["馬名"],
            "割合": "50%",
            "金額": int(budget * 0.50),
        },
        {
            "区分": "相手1",
            "馬番": ranked_horses[1]["馬番"],
            "馬名": ranked_horses[1]["馬名"],
            "割合": "30%",
            "金額": int(budget * 0.30),
        },
        {
            "区分": "相手2",
            "馬番": ranked_horses[2]["馬番"],
            "馬名": ranked_horses[2]["馬名"],
            "割合": "20%",
            "金額": int(budget * 0.20),
        },
    ]

    st.dataframe(
        pd.DataFrame(purchase_rows),
        use_container_width=True,
        hide_index=True,
    )


# =========================================================
# 予測保存
# =========================================================

st.divider()

st.subheader("💾 予測を保存")

st.caption(
    "同じレースでも予測するたびに別ログとして保存されます。"
)

if st.button(
    "この予測を履歴に保存",
    type="primary",
):
    log_id = create_prediction_log_id(
        race_id
    )

    append_prediction_log(
        race_data=race_data,
        horses=model_horses,
        log_id=log_id,
        date_value=selected_date,
        track_type=track_type,
        distance=distance,
        condition=condition,
        budget=budget,
    )

    st.success(
        f"予測を保存しました。"
        f"ログID: {log_id}"
    )


# =========================================================
# 履歴・成績
# =========================================================

st.divider()

st.subheader("📊 予測履歴")

history_df = load_history_df()

if history_df.empty:

    st.info(
        "まだ予測履歴がありません。"
    )

else:

    total_predictions = (
        history_df["予測ログID"]
        .nunique()
    )

    confirmed_df = history_df[
        history_df["結果"].fillna("").astype(str).str.strip() != ""
    ]

    confirmed_logs = (
        confirmed_df["予測ログID"]
        .nunique()
    )

    total_profit = pd.to_numeric(
        history_df["収支"],
        errors="coerce",
    ).fillna(0).sum()

    m1, m2, m3 = st.columns(3)

    with m1:
        st.metric(
            "予測数",
            total_predictions,
        )

    with m2:
        st.metric(
            "確定数",
            confirmed_logs,
        )

    with m3:
        st.metric(
            "累計収支",
            f"{total_profit:,.0f}円",
        )

    st.dataframe(
        history_df.sort_values(
            "予測日時",
            ascending=False,
        ),
        use_container_width=True,
        hide_index=True,
    )

    csv_data = history_df.to_csv(
        index=False,
        encoding="utf-8-sig",
    )

    st.download_button(
        "📥 履歴CSVをダウンロード",
        data=csv_data,
        file_name=HISTORY_FILE,
        mime="text/csv",
    )


# =========================================================
# 結果入力
# =========================================================

st.divider()

st.subheader("📝 予測結果の確認")

if history_df.empty:

    st.info(
        "保存済みの予測がありません。"
    )

else:

    log_ids = (
        history_df["予測ログID"]
        .dropna()
        .astype(str)
        .unique()
        .tolist()
    )

    selected_log_id = st.selectbox(
        "予測ログID",
        log_ids,
    )

    selected_rows = history_df[
        history_df["予測ログID"].astype(str)
        == str(selected_log_id)
    ].copy()

    if not selected_rows.empty:

        st.dataframe(
            selected_rows[
                [
                    "順位",
                    "馬番",
                    "馬名",
                    "オッズ",
                    "モデルシェア",
                    "Value Index",
                    "推奨区分",
                    "購入額",
                    "結果",
                    "払戻",
                    "収支",
                ]
            ],
            use_container_width=True,
            hide_index=True,
        )

        result_options = [
            "",
            "的中",
            "不的中",
            "取消",
            "中止",
        ]

        result = st.selectbox(
            "結果",
            result_options,
        )

        payout = st.number_input(
            "払戻合計",
            min_value=0,
            value=0,
            step=100,
        )

        memo = st.text_area(
            "メモ",
        )

        if st.button(
            "結果を保存",
        ):

            history_df = load_history_df()

            mask = (
                history_df["予測ログID"].astype(str)
                == str(selected_log_id)
            )

            history_df.loc[
                mask,
                "結果"
            ] = result

            history_df.loc[
                mask,
                "払戻"
            ] = payout

            total_bet = pd.to_numeric(
                history_df.loc[
                    mask,
                    "購入額",
                ],
                errors="coerce",
            ).fillna(0).sum()

            profit = (
                payout
                - total_bet
            )

            history_df.loc[
                mask,
                "収支"
            ] = profit

            history_df.loc[
                mask,
                "メモ"
            ] = memo

            save_history_df(
                history_df
            )

            st.success(
                "結果を保存しました。"
            )

            st.rerun()


# =========================================================
# フッター
# =========================================================

st.divider()

st.caption(
    f"JRA Prediction Engine {VERSION} | "
    "モデルスコアと市場オッズを分離して管理"
)
