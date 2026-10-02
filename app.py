import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import re
import json
import numpy as np
import os
import hashlib
from datetime import datetime


# ============================================================
# 0. アプリ基本設定
# ============================================================

st.set_page_config(
    page_title="JRA AI予想 & 成績検証エンジン",
    page_icon="🏇",
    layout="wide",
)

VERSION = "Ver.2.14"
APP_TITLE = "🏇 JRA AI予想 & 成績検証エンジン"

JRA_VENUES = [
    "東京", "中山", "阪神", "京都", "中京",
    "新潟", "福島", "小倉", "札幌", "函館",
]

VENUE_CODE_MAP = {
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

VENUE_NAME_MAP = {v: k for k, v in VENUE_CODE_MAP.items()}

TURF_DISTANCES = [
    "1000m", "1200m", "1400m", "1500m", "1600m",
    "1800m", "2000m", "2200m", "2400m", "2500m",
    "2600m", "3000m", "3200m", "3400m", "3600m",
]

DIRT_DISTANCES = [
    "1000m", "1150m", "1200m", "1400m", "1600m",
    "1700m", "1800m", "2100m", "2400m", "2500m",
]

ALL_DISTANCES = sorted(
    list(set(TURF_DISTANCES + DIRT_DISTANCES)),
    key=lambda x: int(x.replace("m", "")),
)
ALL_DISTANCES_WITH_OTHER = ALL_DISTANCES + ["その他"]

CSV_FILENAME = "JRA_Prediction_History.csv"

CSV_COLUMNS = [
    "予測ログID", "レースID", "レース名", "開催日", "予想日時",
    "データ取得日時", "コース", "距離", "馬場状態", "出走頭数",
    "勝負度", "軸馬", "相手馬", "軸馬オッズ", "バイアス履歴",
    "モデルバージョン", "確定フラグ", "回収額", "収支", "メモ",
    "投資額", "オッズ状態", "オッズ取得率",
]


# ============================================================
# 1. セッション状態
# ============================================================

DEFAULT_SESSION_VALUES = {
    "authenticated": False,
    "fetched_info": None,
    "latest_prediction": None,
}

for key, value in DEFAULT_SESSION_VALUES.items():
    if key not in st.session_state:
        st.session_state[key] = value


# ============================================================
# 2. ログイン
# ============================================================

def check_password():
    if st.session_state["authenticated"]:
        return True

    st.title("🔒 ログイン")

    password_input = st.text_input(
        "パスワードを入力してください",
        type="password",
    )

    if st.button("ログイン", use_container_width=True):
        try:
            app_password = st.secrets.get("APP_PASSWORD", "")
        except Exception:
            app_password = ""

        if app_password and password_input == app_password:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("パスワードが正しくありません。")

    return False


if not check_password():
    st.stop()


# ============================================================
# 3. CSV管理
# ============================================================

def sanitize_df_types(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    numeric_cols = [
        "出走頭数", "軸馬オッズ", "回収額",
        "収支", "投資額", "オッズ取得率",
    ]

    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(
                df[col],
                errors="coerce",
            ).fillna(0)

    return df


def normalize_history_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    for col in CSV_COLUMNS:
        if col not in df.columns:
            df[col] = ""

    df = df[CSV_COLUMNS]
    return sanitize_df_types(df)


def load_history_df():
    if os.path.exists(CSV_FILENAME):
        try:
            df = pd.read_csv(
                CSV_FILENAME,
                encoding="utf-8-sig",
            )
            df = normalize_history_columns(df)
            st.session_state["history_df"] = df
            return df
        except Exception as e:
            st.warning(
                f"履歴CSVの読み込みに失敗しました。"
                f"新しい履歴として扱います: {e}"
            )

    if "history_df" not in st.session_state:
        st.session_state["history_df"] = pd.DataFrame(
            columns=CSV_COLUMNS
        )

    return st.session_state["history_df"]


def save_history_df(df: pd.DataFrame):
    df = normalize_history_columns(df)
    st.session_state["history_df"] = df

    try:
        df.to_csv(
            CSV_FILENAME,
            index=False,
            encoding="utf-8-sig",
        )
        return True
    except Exception as e:
        st.error(f"ファイル保存エラー: {str(e)}")
        return False


load_history_df()


# ============================================================
# 4. レースID
# ============================================================

def generate_jra_race_id(
    year: int,
    venue_name: str,
    kai: int,
    nichi: int,
    race_num: int,
) -> str:
    venue_code = VENUE_CODE_MAP.get(venue_name, "05")

    return (
        f"{year}"
        f"{venue_code}"
        f"{kai:02d}"
        f"{nichi:02d}"
        f"{race_num:02d}"
    )


def extract_race_id(value: str):
    if not value:
        return None

    match = re.search(r"(\d{12})", str(value))

    if not match:
        return None

    return match.group(1)


# ============================================================
# 5. 解析ヘルパー
# ============================================================

def normalize_text(value) -> str:
    if value is None:
        return ""

    return re.sub(
        r"\s+",
        " ",
        str(value).replace("\xa0", " "),
    ).strip()


def parse_distance_from_text(text: str) -> str:
    text = normalize_text(text)

    if not text:
        return "その他"

    match = re.search(
        r"(\d{3,4})\s*m",
        text,
        flags=re.IGNORECASE,
    )

    if match:
        dist = f"{match.group(1)}m"
        if dist in ALL_DISTANCES:
            return dist

    match = re.search(r"(\d{3,4})", text)

    if match:
        dist = f"{match.group(1)}m"
        if dist in ALL_DISTANCES:
            return dist

    return "その他"


def parse_odds(text):
    if text is None:
        return None

    text = normalize_text(text)

    if not text or text in {"---.-", "---", "-", "None", "nan"}:
        return None

    # オッズとして成立する小数を優先
    candidates = re.findall(r"\d+(?:\.\d+)?", text)

    for candidate in candidates:
        try:
            value = float(candidate)
        except Exception:
            continue

        if 1.0 <= value <= 9999.0:
            return value

    return None


def extract_class_text(element) -> str:
    if element is None:
        return ""

    return " ".join(
        element.get("class", [])
    )


def find_first_by_class(row, pattern):
    return row.find(
        lambda tag: (
            hasattr(tag, "get")
            and re.search(
                pattern,
                " ".join(tag.get("class", [])),
                flags=re.I,
            )
        )
    )


def extract_number_by_class(row, pattern):
    elem = find_first_by_class(row, pattern)

    if elem:
        match = re.search(
            r"\d+",
            normalize_text(elem.get_text(" ", strip=True)),
        )
        if match:
            return int(match.group())

    return 0


def extract_horse_name(row) -> str:
    selectors = [
        "span.HorseName",
        ".HorseName",
        "span[class*='HorseName']",
    ]

    for selector in selectors:
        elem = row.select_one(selector)

        if elem:
            text = normalize_text(
                elem.get_text(" ", strip=True)
            )
            if text:
                return text

    # 最後のフォールバック。
    # 父名等を拾わないよう、horse DBリンクのうち
    # 「馬名表示に近い要素」を優先する。
    links = row.select("a[href*='/horse/']")

    for link in links:
        text = normalize_text(link.get_text(" ", strip=True))
        if text:
            parent_text = normalize_text(
                link.parent.get_text(" ", strip=True)
            )
            if text in parent_text:
                return text

    return ""


def extract_jockey(row) -> str:
    elem = find_first_by_class(
        row,
        r"Jockey|JockeyName",
    )

    if elem:
        text = normalize_text(
            elem.get_text(" ", strip=True)
        )
        if text:
            return text

    # 騎手DBリンクをフォールバックにする
    for link in row.select("a[href*='/jockey/']"):
        text = normalize_text(link.get_text(" ", strip=True))
        if text:
            return text

    return ""


def extract_trainer(row) -> str:
    """出馬表から調教師名を取得する。"""
    elem = find_first_by_class(
        row,
        r"Trainer|TrainerName|Chokyo",
    )

    if elem:
        text = normalize_text(
            elem.get_text(" ", strip=True)
        )
        if text:
            return text

    for link in row.select("a[href*='/trainer/']"):
        text = normalize_text(link.get_text(" ", strip=True))
        if text:
            return text

    # netkeibaのHTML変更に備え、調教師らしいセルを最後に探索
    for td in row.find_all("td"):
        cls = " ".join(td.get("class", []))
        if re.search(r"Trainer|Chokyo", cls, re.I):
            text = normalize_text(td.get_text(" ", strip=True))
            if text:
                return text

    return ""


def normalize_person_name(name: str) -> str:
    """騎手・調教師名の照合用正規化。"""
    if name is None:
        return ""
    text = normalize_text(name)
    text = text.replace(" ", "").replace("　", "")
    text = text.replace("騎手", "").replace("調教師", "")
    return text


def extract_jockeys_from_past_row(row):
    """過去走行に含まれる騎手リンクを新しい順に返す。"""
    if row is None:
        return []

    names = []
    for link in row.select("a[href*='/jockey/']"):
        name = normalize_text(link.get_text(" ", strip=True))
        if name:
            names.append(name)

    # リンク構造が変わった場合のフォールバック
    if not names:
        for tag in row.find_all(True):
            cls = " ".join(tag.get("class", []))
            if re.search(r"Jockey|JockeyName", cls, re.I):
                name = normalize_text(tag.get_text(" ", strip=True))
                if name:
                    names.append(name)

    return names


def extract_previous_jockey(
    past_soup,
    horse_number: int,
    horse_name: str,
    current_jockey: str,
) -> str:
    """
    対象馬の過去走から直近騎手を取得。
    現在騎手と同一なら「継続騎乗」、異なれば「乗り替わり」の判定に使う。
    """
    if past_soup is None:
        return ""

    target_name = normalize_person_name(current_jockey)
    row = find_past_row(
        past_soup,
        horse_number,
        horse_name,
    )

    if row is None:
        return ""

    names = extract_jockeys_from_past_row(row)
    if not names:
        return ""

    # 過去走行の最初の騎手を直近騎乗騎手として扱う。
    for name in names:
        if normalize_person_name(name) != normalize_person_name("未定"):
            return name

    return names[0]


def extract_context_history(
    past_soup,
    horse_number: int,
    horse_name: str,
    jockey: str,
    venue: str,
    track_type: str,
    distance: str,
):
    """
    対象馬の過去走から「騎手×今回条件」の実績を補助特徴量として抽出。
    これは騎手全体の公式集計ではなく、当該馬の過去走に限定した観測値。
    """
    result = {
        "騎手×競馬場回数": 0,
        "騎手×距離回数": 0,
        "騎手×コース回数": 0,
    }

    if past_soup is None or not jockey:
        return result

    target_name = normalize_person_name(horse_name)
    jockey_key = normalize_person_name(jockey)
    venue_key = normalize_text(venue)
    distance_key = normalize_text(distance).replace(" ", "")
    course_key = "ダ" if track_type == "ダート" else "芝"

    rows = past_soup.find_all(
        "tr",
        class_=re.compile(r"HorseList"),
    )
    if not rows:
        rows = past_soup.find_all("tr")

    for row in rows:
        row_text = normalize_text(row.get_text(" ", strip=True))
        if not row_text:
            continue

        num = extract_number_by_class(row, r"Umaban")
        name_text = normalize_person_name(
            extract_horse_name(row)
        )

        if num != horse_number and (
            not target_name or target_name not in normalize_person_name(row_text)
        ) and name_text != target_name:
            continue

        row_jockeys = extract_jockeys_from_past_row(row)
        jockey_match = any(
            normalize_person_name(x) == jockey_key
            for x in row_jockeys
        )

        if not jockey_match and jockey_key not in normalize_person_name(row_text):
            continue

        if venue_key and venue_key in row_text:
            result["騎手×競馬場回数"] += 1

        if distance_key:
            compact = row_text.replace(" ", "")
            if distance_key in compact:
                result["騎手×距離回数"] += 1

        if course_key in row_text:
            result["騎手×コース回数"] += 1

    return result


def extract_kinryo(row):
    # 斤量は通常 52.0 / 55.0 / 56.0 のような値。
    # 年齢等の別数字を拾わないよう、40～70kg相当だけ採用。
    for td in row.find_all("td"):
        txt = normalize_text(td.get_text(" ", strip=True))
        match = re.fullmatch(r"(\d{2}(?:\.\d)?)", txt)

        if match:
            value = float(match.group(1))

            if 40.0 <= value <= 70.0:
                return value

    return None


def extract_odds_from_row(row):
    # まずクラス名に Odds が含まれる要素を探す
    candidates = []

    for tag in row.find_all(True):
        class_text = " ".join(
            tag.get("class", [])
        )

        if re.search(r"Odds|OddsTxt|Odds_Ninki", class_text, re.I):
            candidates.append(
                normalize_text(
                    tag.get_text(" ", strip=True)
                )
            )

    for text in candidates:
        odds = parse_odds(text)
        if odds is not None:
            return odds

    # data-* 属性に odds が入っているケース
    for tag in row.find_all(True):
        for attr, value in tag.attrs.items():
            if "odds" not in str(attr).lower():
                continue

            odds = parse_odds(value)

            if odds is not None:
                return odds

    return None


def extract_style_from_text(text: str) -> str:
    """netkeibaの「逃中2週」「先中3週」等から脚質を抽出する。"""
    text = normalize_text(text)

    if not text:
        return "不明"

    # 「Image先中13週」のような連結表記を最優先。
    match = re.search(
        r"Image(逃|先|差|追)(?:中\d+週|中\d+ヶ月|初出走|$)",
        text,
    )
    if match:
        return match.group(1)

    # 通常の脚質記号 + 休養期間
    match = re.search(
        r"(?:^|\s)(逃|先|差|追)(?:中\d+週|中\d+ヶ月|初出走|$)",
        text,
    )
    if match:
        return match.group(1)

    # 「逃中2週」のような連結文字列を直接検索
    match = re.search(
        r"(逃|先|差|追)中(?:\d+週|\d+ヶ月)",
        text,
    )
    if match:
        return match.group(1)

    # 新馬などで「初出走」と付く場合
    match = re.search(r"(逃|先|差|追)初出走", text)
    if match:
        return match.group(1)

    # 説明文や別形式へのフォールバック
    if re.search(r"逃げ", text):
        return "逃"
    if re.search(r"先行", text):
        return "先"
    if re.search(r"差し", text):
        return "差"
    if re.search(r"追込|追い込み", text):
        return "追"

    return "不明"


def extract_style_from_past_row(row) -> str:
    """1頭分の過去走表示行から脚質を抽出する。"""
    elem = find_first_by_class(
        row,
        r"Kyakushitsu|RunningStyle|Style",
    )

    if elem:
        style = extract_style_from_text(
            elem.get_text(" ", strip=True)
        )
        if style != "不明":
            return style

    return extract_style_from_text(
        row.get_text(" ", strip=True)
    )


def find_past_row(soup, horse_number: int, horse_name: str):
    if soup is None:
        return None

    rows = soup.find_all("tr", class_="HorseList")

    # 馬番一致を最優先
    for row in rows:
        num = extract_number_by_class(row, r"Umaban")
        if num == horse_number:
            return row

    # 馬名一致をフォールバック
    if horse_name:
        target = normalize_text(horse_name)
        for row in rows:
            name = normalize_text(extract_horse_name(row))
            if name == target:
                return row

    return None


def extract_style_from_past_page(
    soup,
    horse_number: int,
    horse_name: str,
) -> str:
    """
    過去走ページの1頭分から複数走の脚質を集計。
    「逃中2週」「先中3週」「差中9週」「追中11週」などを
    全件拾い、最頻値を代表脚質として返す。
    """
    row = find_past_row(soup, horse_number, horse_name)

    if row is None:
        return "不明"

    text = normalize_text(
        row.get_text(" ", strip=True)
    )

    if not text:
        return "不明"

    styles = []

    # 過去走欄の典型表記を全件取得
    for match in re.finditer(
        r"(逃|先|差|追)(?:中\d+週|中\d+ヶ月|初出走)",
        text,
    ):
        styles.append(match.group(1))

    # 脚質専用セルが存在する場合
    elem = find_first_by_class(
        row,
        r"Kyakushitsu|RunningStyle|Style",
    )

    if elem:
        elem_text = normalize_text(
            elem.get_text(" ", strip=True)
        )

        for match in re.finditer(
            r"(逃|先|差|追)(?:中\d+週|中\d+ヶ月|初出走)?",
            elem_text,
        ):
            styles.append(match.group(1))

        direct = extract_style_from_text(elem_text)

        if direct != "不明":
            styles.append(direct)

    # 通常の日本語表記にも対応
    for word, code in [
        ("逃げ", "逃"),
        ("先行", "先"),
        ("差し", "差"),
        ("追込", "追"),
        ("追い込み", "追"),
    ]:
        if word in text:
            styles.append(code)

    if not styles:
        return "不明"

    counts = {}

    for style in styles:
        counts[style] = counts.get(style, 0) + 1

    max_count = max(counts.values())

    # 同数の場合はHTML上で先に出た脚質を採用
    for style in styles:
        if counts.get(style, 0) == max_count:
            return style

    return "不明"


def infer_style_from_passing_order(text: str) -> str:
    """
    過去走の「通過」順位から脚質を推定する最終フォールバック。
    例: 1-1-1-1 / 2-3-4-5 / 10-10-9-8
    """
    text = normalize_text(text)
    if not text:
        return "不明"

    # 明示的な脚質表記があれば最優先
    explicit = extract_style_from_text(text)
    if explicit != "不明":
        return explicit

    patterns = [
        r"通過(?:順位)?[^0-9]{0,10}([0-9]+(?:[-－][0-9]+){1,3})",
        r"通過[^0-9]{0,10}([0-9]+(?:,[0-9]+){1,3})",
    ]

    for pattern in patterns:
        m = re.search(pattern, text)
        if not m:
            continue

        raw = m.group(1).replace("－", "-").replace(",", "-")
        try:
            positions = [
                int(x) for x in raw.split("-")
                if x.isdigit()
            ]
        except Exception:
            continue

        if not positions:
            continue

        # 最終コーナー寄りの位置を代表値にする
        pos = positions[-1]

        # 1～3番手は逃げ/先行寄り
        if pos <= 2:
            return "逃"
        if pos <= 5:
            return "先"

        # それ以降は差し/追込
        if pos <= 10:
            return "差"

        return "追"

    return "不明"


def extract_style_from_any_past_horse_text(
    soup,
    horse_number: int,
    horse_name: str,
) -> str:
    """
    HorseListのclass名が変わっても、馬番・馬名を手掛かりに
    過去走ページ全体から対象馬のテキストを探す。
    """
    if soup is None:
        return "不明"

    target_name = normalize_text(horse_name)

    # まずHorseList
    rows = soup.find_all("tr", class_=re.compile(r"HorseList"))
    candidates = list(rows)

    # HorseListが取れない場合はtable内のtrを広く探索
    if not candidates:
        candidates = soup.find_all("tr")

    matched = []

    for row in candidates:
        text = normalize_text(row.get_text(" ", strip=True))
        if not text:
            continue

        if target_name and target_name in text:
            matched.append(text)
            continue

        num = extract_number_by_class(row, r"Umaban")
        if num == horse_number:
            matched.append(text)

    # 対象馬の行をまとめて解析
    for text in matched:
        style = infer_style_from_passing_order(text)
        if style != "不明":
            return style

    # 行構造が取れない場合、ページ内の対象馬名周辺を直接探索
    if target_name:
        full_text = normalize_text(soup.get_text(" ", strip=True))
        start = full_text.find(target_name)

        if start >= 0:
            chunk = full_text[
                max(0, start - 500):
                start + 2500
            ]
            style = infer_style_from_passing_order(chunk)
            if style != "不明":
                return style

    return "不明"



def extract_odds_from_past_page(
    soup,
    horse_number: int,
    horse_name: str,
):
    row = find_past_row(soup, horse_number, horse_name)
    if row is None:
        return None

    return extract_odds_from_row(row)


def parse_odds_api_payload(response_text: str):
    """netkeibaオッズAPIのJSON/JSONPを辞書へ変換する。"""
    text = (response_text or "").strip()
    if not text:
        return None

    # JSONP: callback({...}); / xxx({...}); を許容
    candidates = [text]
    match = re.search(r"\((\{.*\})\)\s*;?\s*$", text, re.S)
    if match:
        candidates.append(match.group(1))

    for candidate in candidates:
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj
        except Exception:
            continue

    return None


def fetch_win_odds_api(session, race_id: str):
    """netkeibaの単勝オッズAPIから馬番別オッズを取得する。"""
    api_url = (
        "https://race.netkeiba.com/api/api_get_jra_odds.html"
    )

    params = {
        "pid": "api_get_jra_odds",
        "race_id": race_id,
        "type": "1",
        "action": "update",
        "sort": "odds",
        "compress": "0",
        "output": "json",
    }

    headers = dict(REQUEST_HEADERS)
    headers.update({
        "Referer": (
            "https://race.netkeiba.com/race/"
            f"shutuba.html?race_id={race_id}"
        ),
        "Accept": "application/json, text/javascript, */*; q=0.01",
    })

    try:
        response = session.get(
            api_url,
            params=params,
            headers=headers,
            timeout=15,
        )
        response.raise_for_status()

        payload = parse_odds_api_payload(response.text)

        # updateで空の場合はinitでもう一度取得する。
        if not payload:
            params["action"] = "init"
            retry = session.get(
                api_url,
                params=params,
                headers=headers,
                timeout=15,
            )
            retry.raise_for_status()
            payload = parse_odds_api_payload(retry.text)

        if not payload:
            return {}

        data = payload.get("data", payload)
        odds_root = data.get("odds", {}) if isinstance(data, dict) else {}
        win_rows = odds_root.get("1", {}) if isinstance(odds_root, dict) else {}

        result = {}

        if isinstance(win_rows, dict):
            for key, row in win_rows.items():
                try:
                    horse_no = int(str(key))
                except Exception:
                    continue

                odds = None
                if isinstance(row, (list, tuple)) and len(row) > 0:
                    odds = parse_odds(row[0])
                else:
                    odds = parse_odds(row)

                if odds is not None:
                    result[horse_no] = odds

        return result

    except Exception:
        return {}


# ============================================================
# 6. JRA公式リーディング取得
# ============================================================

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_jra_leading_data(year: int):
    """
    JRA公式の当年リーディング情報を取得。
    騎手・調教師とも全国/JRA成績の順位を利用する。
    """
    result = {
        "year": year,
        "jockey": {},
        "trainer": {},
        "source": "JRA公式",
    }

    def parse_table(soup, keywords):
        data = {}
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if not rows:
                continue

            header_text = normalize_text(
                rows[0].get_text(" ", strip=True)
            )
            if not any(k in header_text for k in keywords):
                continue

            for row in rows[1:]:
                cells = [
                    normalize_text(c.get_text(" ", strip=True))
                    for c in row.find_all(["th", "td"])
                ]
                if len(cells) < 2:
                    continue

                rank_match = re.search(r"^\d+$", cells[0])
                if not rank_match:
                    continue

                try:
                    rank = int(cells[0])
                except Exception:
                    continue

                name = cells[1]
                if not name:
                    continue

                data[normalize_person_name(name)] = {
                    "rank": rank,
                    "name": name,
                    "year": year,
                }
        return data

    for kind, filename, keywords in [
        (
            "jockey",
            f"j{year}.html",
            ["騎手名"],
        ),
        (
            "trainer",
            f"t{year}.html",
            ["調教師名"],
        ),
    ]:
        url = (
            "https://www.jra.go.jp/datafile/leading/"
            f"{filename}"
        )
        try:
            response = requests.get(
                url,
                headers=REQUEST_HEADERS,
                timeout=20,
            )
            response.raise_for_status()
            response.encoding = (
                response.apparent_encoding
                or response.encoding
                or "utf-8"
            )
            soup = BeautifulSoup(
                response.text,
                "html.parser",
            )
            result[kind] = parse_table(
                soup,
                keywords,
            )
        except Exception:
            result[kind] = {}

    # 現行年度ページが取得できない場合は前年を補助参照。
    if not result["jockey"] and not result["trainer"] and year > 2020:
        prev = fetch_jra_leading_data(year - 1)
        result = prev.copy()
        result["fallback_year"] = year - 1

    return result


def leading_multiplier(rank):
    """
    リーディング順位を過度に強くしないための緩やかな補正。
    1位=1.10、5位前後=1.06、10位前後=1.04、20位前後=1.02。
    """
    if not rank:
        return 1.0
    if rank <= 3:
        return 1.10
    if rank <= 5:
        return 1.07
    if rank <= 10:
        return 1.04
    if rank <= 20:
        return 1.02
    if rank <= 30:
        return 1.01
    return 1.0


def context_multiplier(count, cap=3, step=0.015):
    """
    当該馬の過去走から確認できる騎手×条件の回数を
    小さな補正に変換する。最大補正は抑制する。
    """
    count = max(0, min(int(count or 0), cap))
    return 1.0 + step * count


# ============================================================
# 7. netkeiba取得
# ============================================================

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/120.0.0.0 "
        "Safari/537.36"
    ),
    "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.8",
}


@st.cache_data(
    ttl=30,
    show_spinner=False,
)
def fetch_netkeiba_race_data_cached(race_id: str):
    url = (
        "https://race.netkeiba.com/race/"
        f"shutuba.html?race_id={race_id}"
    )

    past_url = (
        "https://race.netkeiba.com/race/"
        f"shutuba_past_9.html?race_id={race_id}"
    )

    try:
        session = requests.Session()
        session.headers.update(REQUEST_HEADERS)

        response = session.get(
            url,
            headers={**REQUEST_HEADERS, "Referer": "https://race.netkeiba.com/"},
            timeout=20,
        )

        response.raise_for_status()

        response.encoding = (
            response.apparent_encoding
            or response.encoding
            or "euc-jp"
        )

        soup = BeautifulSoup(
            response.text,
            "html.parser",
        )

        race_data_01 = soup.find(
            "div",
            class_="RaceData01",
        )

        if race_data_01 is None:
            return None, (
                f"レース(ID:{race_id})が取得できませんでした。"
                "レースID、開催日、開催回、日目をご確認ください。"
            )

        race_id = str(race_id)
        venue_code = race_id[4:6]

        detected_venue = VENUE_NAME_MAP.get(
            venue_code,
            "不明",
        )

        try:
            race_num = int(race_id[-2:])
        except Exception:
            race_num = 0

        extracted = {
            "race_id": race_id,
            "venue": detected_venue,
            "race_num": race_num,
            "track_type": "芝",
            "distance": "その他",
            "condition": "良",
            "race_name": f"レース_{race_id}",
            "race_date": datetime.now(),
            "horses": [],
            "kyaku_count": 0,
            "front_runner_count": 0,
            "odds_coverage": 0.0,
            "odds_status": "未取得（発売前等）",
            "fetched_at": datetime.now(),
        }

        # ----------------------------------------------------
        # レース日
        # ----------------------------------------------------
        race_data_02 = soup.find(
            "div",
            class_="RaceData02",
        )

        if race_data_02:
            date_text = normalize_text(
                race_data_02.get_text(" ", strip=True)
            )

            date_match = re.search(
                r"(\d{4})年(\d{1,2})月(\d{1,2})日",
                date_text,
            )

            if date_match:
                y, m, d = map(
                    int,
                    date_match.groups(),
                )
                extracted["race_date"] = datetime(y, m, d)

        # ----------------------------------------------------
        # レース名
        # ----------------------------------------------------
        race_title = soup.find(
            "div",
            class_="RaceName",
        )

        if race_title:
            name = normalize_text(
                race_title.get_text(" ", strip=True)
            )
            if name:
                extracted["race_name"] = name

        # ----------------------------------------------------
        # コース・距離・馬場
        # ----------------------------------------------------
        race_text = normalize_text(
            race_data_01.get_text(" ", strip=True)
        )

        if "ダ" in race_text:
            extracted["track_type"] = "ダート"
        elif "障" in race_text:
            extracted["track_type"] = "障害"
        else:
            extracted["track_type"] = "芝"

        parsed_distance = parse_distance_from_text(race_text)

        if parsed_distance != "その他":
            extracted["distance"] = parsed_distance

        if "不良" in race_text:
            extracted["condition"] = "不良"
        elif "稍重" in race_text:
            extracted["condition"] = "稍重"
        elif "重" in race_text:
            extracted["condition"] = "重"
        else:
            extracted["condition"] = "良"

        # ----------------------------------------------------
        # 脚質・オッズ補完用の過去走ページ
        # ----------------------------------------------------
        past_soup = None

        try:
            past_response = session.get(
                past_url,
                headers={**REQUEST_HEADERS, "Referer": url},
                timeout=20,
            )
            past_response.raise_for_status()
            past_response.encoding = (
                past_response.apparent_encoding
                or past_response.encoding
                or "euc-jp"
            )
            past_soup = BeautifulSoup(
                past_response.text,
                "html.parser",
            )

            if not past_soup.find("tr"):
                fallback_past_url = (
                    "https://race.netkeiba.com/race/"
                    f"shutuba_past.html?race_id={race_id}"
                )
                fallback_response = session.get(
                    fallback_past_url,
                    headers={**REQUEST_HEADERS, "Referer": url},
                    timeout=20,
                )
                fallback_response.raise_for_status()
                fallback_response.encoding = (
                    fallback_response.apparent_encoding
                    or fallback_response.encoding
                    or "euc-jp"
                )
                past_soup = BeautifulSoup(
                    fallback_response.text,
                    "html.parser",
                )
        except Exception:
            past_soup = None

        # オッズはHTMLの ---.- プレースホルダではなく、
        # netkeibaの単勝オッズJSON APIから取得する。
        api_odds_map = fetch_win_odds_api(session, race_id)

        # ----------------------------------------------------
        # JRA公式リーディング
        # ----------------------------------------------------
        leading_data = fetch_jra_leading_data(
            extracted["race_date"].year
        )

        # ----------------------------------------------------
        # 出走馬
        # ----------------------------------------------------
        horse_rows = soup.find_all(
            "tr",
            class_="HorseList",
        )

        if not horse_rows:
            return None, (
                "出走馬テーブルが見つかりませんでした。"
            )

        # 同じ馬番の重複行が出るケースがあるため、
        # 馬番をキーに統合する。
        horse_map = {}

        for row in horse_rows:
            try:
                waku = extract_number_by_class(
                    row,
                    r"Waku",
                )

                uma = extract_number_by_class(
                    row,
                    r"Umaban",
                )

                if uma <= 0:
                    continue

                horse_name = extract_horse_name(row)

                if not horse_name:
                    continue

                jockey = extract_jockey(row)
                trainer = extract_trainer(row)
                kinryo = extract_kinryo(row)

                jockey_rank_info = leading_data.get(
                    "jockey", {}
                ).get(
                    normalize_person_name(jockey),
                    {}
                )
                trainer_rank_info = leading_data.get(
                    "trainer", {}
                ).get(
                    normalize_person_name(trainer),
                    {}
                )

                previous_jockey = extract_previous_jockey(
                    past_soup,
                    uma,
                    horse_name,
                    jockey,
                )

                if previous_jockey:
                    rider_change_status = (
                        "継続騎乗"
                        if normalize_person_name(previous_jockey)
                        == normalize_person_name(jockey)
                        else "乗り替わり"
                    )
                else:
                    rider_change_status = "判定不能"

                # HTML側のオッズは ---.- のプレースホルダになるため、
                # 実値はAPIを最優先する。
                odds = api_odds_map.get(uma)
                if odds is None:
                    odds = extract_odds_from_row(row)

                # 現在の出馬表の馬行から脚質を最優先で取得。
                # netkeibaでは「Image先中13週」のように
                # 馬行内へ現在脚質が直接記載される。
                style = extract_style_from_text(
                    row.get_text(" ", strip=True)
                )
                style_source = (
                    "出馬表・脚質欄"
                    if style != "不明"
                    else "未取得"
                )

                # 第2段階: 過去走ページの標準構造から取得。
                if style == "不明" and past_soup is not None:
                    past_style = extract_style_from_past_page(
                        past_soup,
                        uma,
                        horse_name,
                    )

                    if past_style != "不明":
                        style = past_style
                        style_source = "過去走"

                # 第3段階: class名やHTML構造が変わっていても、
                # 馬名/馬番を手掛かりに過去走テキストを広く探索。
                if style == "不明" and past_soup is not None:
                    inferred_style = (
                        extract_style_from_any_past_horse_text(
                            past_soup,
                            uma,
                            horse_name,
                        )
                    )

                    if inferred_style != "不明":
                        style = inferred_style
                        style_source = "過去走・通過順位推定"

                style_display = {
                    "逃": "逃げ",
                    "先": "先行",
                    "差": "差し",
                    "追": "追込",
                }.get(style, "不明")

                context_stats = extract_context_history(
                    past_soup=past_soup,
                    horse_number=uma,
                    horse_name=horse_name,
                    jockey=jockey,
                    venue=detected_venue,
                    track_type=extracted["track_type"],
                    distance=extracted["distance"],
                )

                candidate = {
                    "枠番": int(waku),
                    "馬番": int(uma),
                    "馬名": horse_name,
                    "騎手": jockey,
                    "調教師": trainer,
                    "斤量": kinryo,
                    "脚質": style,
                    "脚質表示": style_display,
                    "脚質取得元": style_source,
                    "オッズ": odds,
                    "前走騎手": previous_jockey,
                    "騎乗形態": rider_change_status,
                    "騎手リーディング順位": jockey_rank_info.get("rank"),
                    "調教師リーディング順位": trainer_rank_info.get("rank"),
                    "騎手×競馬場回数": context_stats["騎手×競馬場回数"],
                    "騎手×距離回数": context_stats["騎手×距離回数"],
                    "騎手×コース回数": context_stats["騎手×コース回数"],
                }

                # 重複した馬番があった場合、
                # 情報量の多い行を優先する。
                quality = 0

                if candidate["騎手"]:
                    quality += 3
                if candidate["斤量"] is not None:
                    quality += 2
                if candidate["脚質"] != "不明":
                    quality += 2
                if candidate["オッズ"] is not None:
                    quality += 2
                if candidate["枠番"] > 0:
                    quality += 1

                old = horse_map.get(uma)

                if old is None or quality > old["_quality"]:
                    candidate["_quality"] = quality
                    horse_map[uma] = candidate

            except Exception:
                continue

        horses = []

        for uma in sorted(horse_map.keys()):
            item = horse_map[uma].copy()
            item.pop("_quality", None)

            # Noneを内部データとして許容するが、
            # 画面表示時には必ず「-」にする。
            horses.append(item)

        if not horses:
            return None, (
                "出走馬データの抽出件数が0件です。"
            )

        # ----------------------------------------------------
        # 脚質統計
        # ----------------------------------------------------
        kyaku_count = sum(
            1
            for h in horses
            if h.get("脚質") in {"逃", "先", "差", "追"}
        )

        front_runner_count = sum(
            1
            for h in horses
            if h.get("脚質") in {"逃", "先"}
        )

        # ----------------------------------------------------
        # オッズ統計
        # ----------------------------------------------------
        valid_odds_count = sum(
            1
            for h in horses
            if h.get("オッズ") is not None
        )

        odds_coverage = (
            valid_odds_count / len(horses)
            if horses
            else 0.0
        )

        if odds_coverage >= 0.99:
            odds_status = "当日確定"
        elif odds_coverage >= 0.70:
            odds_status = "当日一部取得"
        elif odds_coverage > 0:
            odds_status = "一部取得"
        else:
            odds_status = "未取得（発売前等）"

        extracted["horses"] = horses
        extracted["leading_data"] = leading_data
        extracted["leading_year"] = leading_data.get("year")
        extracted["kyaku_count"] = kyaku_count
        extracted["front_runner_count"] = front_runner_count
        extracted["odds_coverage"] = odds_coverage
        extracted["odds_status"] = odds_status

        return extracted, None

    except requests.exceptions.RequestException as e:
        return None, f"通信エラー: {str(e)}"

    except Exception as e:
        return None, f"解析エラー: {str(e)}"


def fetch_netkeiba_race_data(race_id_or_url):
    race_id = extract_race_id(race_id_or_url)

    if not race_id:
        return None, (
            "有効な12桁のレースIDが見つかりません。"
        )

    return fetch_netkeiba_race_data_cached(race_id)


# ============================================================
# 7. モデル
# ============================================================

def safe_log_multiplier(multiplier: float) -> float:
    return np.log(max(multiplier, 0.01))


def calculate_model_score(
    horse,
    fetched_info,
    track_type,
    distance,
    front_bias,
    inside_bias,
    outer_bias,
    green_belt,
    g1_mode,
    jockey_trainer_mode=True,
):
    score = 0.0

    horse_num = int(horse.get("馬番", 0) or 0)
    waku_num = int(horse.get("枠番", 0) or 0)

    jockey = str(
        horse.get("騎手", "")
    ).replace(" ", "").replace("　", "")

    style = str(horse.get("脚質", "不明"))

    total_horses = len(fetched_info["horses"])

    kyaku_rate = (
        fetched_info["kyaku_count"]
        / max(total_horses, 1)
    )

    if "ルメール" in jockey:
        score += safe_log_multiplier(1.15)
    elif "川田" in jockey:
        score += safe_log_multiplier(1.15)

    if (
        front_bias
        and kyaku_rate >= 0.70
        and style in {"逃", "先"}
    ):
        score += safe_log_multiplier(1.08)

    if inside_bias:
        if waku_num in [1, 2]:
            score += safe_log_multiplier(1.05)
        elif waku_num == 0 and horse_num <= 2:
            score += safe_log_multiplier(1.05)

    if outer_bias:
        if waku_num in [7, 8]:
            score += safe_log_multiplier(1.05)
        elif (
            waku_num == 0
            and horse_num >= total_horses - 2
        ):
            score += safe_log_multiplier(1.05)

    if track_type == "ダート" and distance == "1200m":
        if waku_num in [1, 2]:
            score += safe_log_multiplier(1.08)

    if green_belt and horse_num == 1:
        score += safe_log_multiplier(1.06)

    if g1_mode and horse_num in [1, 3, 7]:
        score += safe_log_multiplier(1.03)

    # --------------------------------------------------------
    # 新規: 騎手・調教師特徴量
    # --------------------------------------------------------
    if jockey_trainer_mode:
        jockey_rank = horse.get("騎手リーディング順位")
        trainer_rank = horse.get("調教師リーディング順位")

        # JRA公式当年リーディング
        score += safe_log_multiplier(
            leading_multiplier(jockey_rank)
        )
        score += safe_log_multiplier(
            leading_multiplier(trainer_rank)
        )

        # 継続騎乗 / 乗り替わり
        rider_status = horse.get("騎乗形態")
        if rider_status == "継続騎乗":
            score += safe_log_multiplier(1.04)
        elif rider_status == "乗り替わり":
            previous = normalize_person_name(
                horse.get("前走騎手", "")
            )
            current = normalize_person_name(
                horse.get("騎手", "")
            )

            prev_rank = (
                fetched_info.get("leading_data", {})
                .get("jockey", {})
                .get(previous, {})
                .get("rank")
            )
            current_rank = (
                fetched_info.get("leading_data", {})
                .get("jockey", {})
                .get(current, {})
                .get("rank")
            )

            if current_rank and prev_rank:
                if current_rank < prev_rank:
                    score += safe_log_multiplier(1.05)
                elif current_rank > prev_rank:
                    score += safe_log_multiplier(0.98)
                else:
                    score += safe_log_multiplier(1.00)
            else:
                # 前走騎手が取れない場合は、乗り替わり自体を
                # 強いマイナスにはしない。
                score += safe_log_multiplier(0.995)

        # 騎手×競馬場 / 距離 / コース
        score += safe_log_multiplier(
            context_multiplier(
                horse.get("騎手×競馬場回数", 0),
                cap=3,
                step=0.015,
            )
        )
        score += safe_log_multiplier(
            context_multiplier(
                horse.get("騎手×距離回数", 0),
                cap=3,
                step=0.012,
            )
        )
        score += safe_log_multiplier(
            context_multiplier(
                horse.get("騎手×コース回数", 0),
                cap=3,
                step=0.010,
            )
        )

    return score


def scores_to_probabilities(scores):
    scores = np.asarray(scores, dtype=float)

    if len(scores) == 0:
        return np.array([])

    max_score = np.max(scores)
    exp_scores = np.exp(scores - max_score)
    total = np.sum(exp_scores)

    if total <= 0:
        return np.ones(len(scores)) / len(scores)

    return exp_scores / total


def calculate_value_index(
    model_probability,
    odds,
    odds_status,
):
    if odds_status not in {
        "当日確定",
        "当日一部取得",
        "一部取得",
    }:
        return None

    if odds is None:
        return None

    try:
        odds = float(odds)
    except Exception:
        return None

    if odds <= 0:
        return None

    market_implied_probability = 1.0 / odds

    if market_implied_probability <= 0:
        return None

    value_index = (
        model_probability
        / market_implied_probability
    )

    return round(float(value_index), 2)


def create_prediction_log_id(
    race_id,
    prediction_time,
):
    raw = (
        f"{race_id}|"
        f"{prediction_time}|"
        f"{VERSION}"
    )

    digest = hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()[:12]

    return f"{race_id}-{digest}"


# ============================================================
# 8. 表示用整形
# ============================================================

def build_display_horse_df(horses):
    rows = []

    for horse in horses:
        odds = horse.get("オッズ")
        kinryo = horse.get("斤量")

        rows.append({
            "枠番": int(horse.get("枠番", 0) or 0),
            "馬番": int(horse.get("馬番", 0) or 0),
            "馬名": str(horse.get("馬名", "")),
            "騎手": str(horse.get("騎手") or "不明"),
            "調教師": str(horse.get("調教師") or "不明"),
            "斤量": (
                f"{float(kinryo):.1f}"
                if kinryo is not None
                else "-"
            ),
            "脚質": str(
                horse.get(
                    "脚質表示",
                    {
                        "逃": "逃げ",
                        "先": "先行",
                        "差": "差し",
                        "追": "追込",
                    }.get(
                        horse.get("脚質"),
                        "不明",
                    ),
                )
            ),
            "脚質取得元": str(
                horse.get("脚質取得元") or "未取得"
            ),
            "オッズ": (
                f"{float(odds):.1f}"
                if odds is not None
                else "-"
            ),
        })

    return pd.DataFrame(rows)


# ============================================================
# 9. サイドバー
# ============================================================

st.sidebar.title("🏇 JRA AI予想 engine")
st.sidebar.caption(f"モデル: **{VERSION}**")

mode = st.sidebar.radio(
    "機能メニュー",
    [
        "🏇 リアルタイム予想",
        "📊 成績ダッシュボード・結果入力",
    ],
)


# ============================================================
# 10. リアルタイム予想
# ============================================================

if mode == "🏇 リアルタイム予想":

    st.header("🏇 リアルタイム予想 & スコアリング")

    st.info(
        "出走馬・騎手・斤量・脚質・枠・コース条件などから"
        "モデル内相対評価を算出し、その後に市場オッズと"
        "比較してValue Indexを計算します。"
    )

    st.markdown("### 📅 対象レース")

    c1, c2, c3, c4 = st.columns(4)

    with c1:
        selected_date = st.date_input(
            "開催日",
            value=pd.Timestamp.now(),
        )

    with c2:
        selected_venue = st.selectbox(
            "競馬場",
            JRA_VENUES,
        )

    with c3:
        kai_val = st.number_input(
            "開催回",
            min_value=1,
            max_value=6,
            value=4,
        )

    with c4:
        nichi_val = st.number_input(
            "日目",
            min_value=1,
            max_value=12,
            value=1,
        )

    c5, c6 = st.columns([1, 2])

    with c5:
        race_num_val = st.selectbox(
            "レース番号",
            [f"{i}R" for i in range(1, 13)],
            index=0,
        )

        race_num = int(
            race_num_val.replace("R", "")
        )

    auto_race_id = generate_jra_race_id(
        selected_date.year,
        selected_venue,
        kai_val,
        nichi_val,
        race_num,
    )

    with c6:
        manual_input = st.text_input(
            "直接URL / 12桁レースID",
            placeholder=auto_race_id,
        )

    target_race_id = (
        extract_race_id(manual_input)
        if manual_input.strip()
        else auto_race_id
    )

    if st.button(
        "🔄 出走表・最新データ取得",
        use_container_width=True,
    ):
        with st.spinner(
            f"レースID {target_race_id} を取得中..."
        ):
            info, error = fetch_netkeiba_race_data(
                target_race_id
            )

        if error:
            st.error(error)
        else:
            st.session_state["fetched_info"] = info
            st.session_state["latest_prediction"] = None
            st.rerun()

    fetched_info = st.session_state.get(
        "fetched_info"
    )

    if fetched_info:

        kyaku_rate = (
            fetched_info["kyaku_count"]
            / max(
                len(fetched_info["horses"]),
                1,
            )
        )

        st.success(
            f"✅ **{fetched_info['race_name']}** "
            f"| {fetched_info['venue']}"
            f"{fetched_info['race_num']}R "
            f"| {fetched_info['track_type']}"
            f"{fetched_info['distance']} "
            f"| {fetched_info['condition']} "
            f"| {fetched_info['race_date'].strftime('%Y/%m/%d')}"
        )

        m1, m2, m3, m4 = st.columns(4)

        m1.metric(
            "出走頭数",
            f"{len(fetched_info['horses'])}頭",
        )

        m2.metric(
            "脚質取得率",
            f"{kyaku_rate:.0%}",
        )

        m3.metric(
            "オッズ取得率",
            f"{fetched_info['odds_coverage']:.0%}",
        )

        m4.metric(
            "オッズ状態",
            fetched_info["odds_status"],
        )

        with st.expander("🔎 脚質・オッズ取得診断"):
            diag_df = pd.DataFrame(fetched_info["horses"])

            if "脚質取得元" in diag_df.columns:
                source_summary = (
                    diag_df["脚質取得元"]
                    .value_counts()
                    .rename_axis("脚質取得元")
                    .reset_index(name="頭数")
                )

                st.dataframe(
                    source_summary,
                    use_container_width=True,
                    hide_index=True,
                )

            st.caption(
                "脚質は過去走の複数レースから「逃・先・差・追」を集計し、"
                "最頻値を代表脚質として採用します。"
            )

        if kyaku_rate < 0.70:
            st.warning(
                "脚質取得率が70%未満です。"
                "取得できた脚質だけを前残り補正に使用します。"
            )

        if fetched_info["odds_status"] == "未取得（発売前等）":
            st.info(
                "現在オッズを取得できていません。"
                "発売前などでオッズが未掲載の場合は「-」表示になります。"
            )
        elif fetched_info["odds_status"] in {
            "当日一部取得",
            "一部取得",
        }:
            st.warning(
                "オッズの一部が取得できていません。"
                "取得できた馬のみValue Indexを表示します。"
            )

        with st.expander(
            "🔎 データ取得診断"
        ):
            st.write(
                f"脚質取得: "
                f"{fetched_info['kyaku_count']}/"
                f"{len(fetched_info['horses'])}頭"
            )
            st.write(
                f"オッズ取得: "
                f"{sum(h.get('オッズ') is not None for h in fetched_info['horses'])}/"
                f"{len(fetched_info['horses'])}頭"
            )
            st.caption(
                "同一馬番の重複行は自動統合し、"
                "騎手・斤量・脚質・オッズなど情報量の多い行を採用します。"
            )

        with st.expander(
            "🐎 取得した出走馬データを確認"
        ):
            horse_preview = build_display_horse_df(
                fetched_info["horses"]
            )

            st.dataframe(
                horse_preview,
                use_container_width=True,
                hide_index=True,
            )

        with st.expander("👤 騎手・調教師評価診断"):
            diag_rows = []
            for h in fetched_info["horses"]:
                diag_rows.append({
                    "馬番": h.get("馬番"),
                    "馬名": h.get("馬名"),
                    "騎手": h.get("騎手") or "不明",
                    "調教師": h.get("調教師") or "不明",
                    "騎手リーディング": h.get("騎手リーディング順位") or "-",
                    "調教師リーディング": h.get("調教師リーディング順位") or "-",
                    "騎乗形態": h.get("騎乗形態") or "判定不能",
                    "前走騎手": h.get("前走騎手") or "-",
                    "騎手×競馬場": h.get("騎手×競馬場回数", 0),
                    "騎手×距離": h.get("騎手×距離回数", 0),
                    "騎手×コース": h.get("騎手×コース回数", 0),
                })
            st.dataframe(
                pd.DataFrame(diag_rows),
                use_container_width=True,
                hide_index=True,
            )
            leading_year = fetched_info.get("leading_year")
            if leading_year:
                st.caption(
                    f"リーディング参照年: {leading_year}年 / JRA公式。"
                    "リーディング順位が取得できない人物は補正0です。"
                )
            st.caption(
                "騎手×競馬場・距離・コースは、現状では当該馬の過去走から"
                "確認できた同騎手の出走履歴を補助特徴量として利用します。"
            )

    st.markdown("### ⚙️ モデル条件")

    c1, c2, c3 = st.columns(3)

    default_track = (
        fetched_info["track_type"]
        if fetched_info
        else "芝"
    )

    with c1:
        track_type = st.selectbox(
            "コース種別",
            ["芝", "ダート", "障害"],
            index=[
                "芝", "ダート", "障害"
            ].index(default_track),
        )

    if track_type == "芝":
        distance_options = TURF_DISTANCES + ["その他"]
    elif track_type == "ダート":
        distance_options = DIRT_DISTANCES + ["その他"]
    else:
        distance_options = ALL_DISTANCES_WITH_OTHER

    default_distance = (
        fetched_info["distance"]
        if fetched_info
        else "1600m"
    )

    with c2:
        distance = st.selectbox(
            "距離",
            distance_options,
            index=(
                distance_options.index(
                    default_distance
                )
                if default_distance in distance_options
                else len(distance_options) - 1
            ),
        )

    if distance == "その他":
        custom_distance = st.text_input(
            "手動距離",
            value="",
        )
        final_distance = (
            custom_distance
            if custom_distance
            else "その他"
        )
    else:
        final_distance = distance

    default_condition = (
        fetched_info["condition"]
        if fetched_info
        else "良"
    )

    with c3:
        condition = st.selectbox(
            "馬場状態",
            ["良", "稍重", "重", "不良"],
            index=(
                ["良", "稍重", "重", "不良"].index(
                    default_condition
                )
                if default_condition in [
                    "良", "稍重", "重", "不良"
                ]
                else 0
            ),
        )

    st.markdown("### 🎛️ 補正設定")

    b1, b2, b3 = st.columns(3)

    with b1:
        front_bias = st.checkbox(
            "前残り補正",
            value=True,
        )

        inside_bias = st.checkbox(
            "内枠補正",
            value=False,
        )

    with b2:
        outer_bias = st.checkbox(
            "外差し補正",
            value=False,
        )

        green_belt = st.checkbox(
            "グリーンベルト補正",
            value=False,
        )

    with b3:
        g1_mode = st.checkbox(
            "G1サインモード",
            value=False,
        )

        jockey_trainer_mode = st.checkbox(
            "騎手・調教師評価",
            value=True,
            help=(
                "JRA公式リーディング、継続騎乗/乗り替わり、"
                "当該馬の過去走から確認できる騎手×条件をスコアへ反映します。"
            ),
        )

        confidence = st.select_slider(
            "勝負度",
            options=[
                "★☆☆",
                "★★☆",
                "★★★",
            ],
            value="★★☆",
        )

    budget = st.number_input(
        "参考予算",
        min_value=100,
        value=1000,
        step=100,
    )

    run_disabled = fetched_info is None

    if run_disabled:
        st.info("まず出走表を取得してください。")

    if st.button(
        "🚀 モデル予想を実行",
        disabled=run_disabled,
        use_container_width=True,
    ):

        horses = fetched_info["horses"]

        scores = []

        for horse in horses:
            score = calculate_model_score(
                horse=horse,
                fetched_info=fetched_info,
                track_type=track_type,
                distance=final_distance,
                front_bias=front_bias,
                inside_bias=inside_bias,
                outer_bias=outer_bias,
                green_belt=green_belt,
                g1_mode=g1_mode,
                jockey_trainer_mode=jockey_trainer_mode,
            )
            scores.append(score)

        probabilities = scores_to_probabilities(
            scores
        )

        result_rows = []

        for i, horse in enumerate(horses):

            model_probability = probabilities[i]
            odds = horse.get("オッズ")

            value_index = calculate_value_index(
                model_probability,
                odds,
                fetched_info["odds_status"],
            )

            result_rows.append({
                "枠番": int(horse.get("枠番", 0) or 0),
                "馬番": int(horse.get("馬番", 0) or 0),
                "馬名": str(horse.get("馬名", "")),
                "騎手": str(horse.get("騎手") or "不明"),
                "調教師": str(horse.get("調教師") or "不明"),
                "騎乗形態": str(horse.get("騎乗形態") or "判定不能"),
                "騎手リーディング": (
                    int(horse["騎手リーディング順位"])
                    if horse.get("騎手リーディング順位") is not None
                    else np.nan
                ),
                "調教師リーディング": (
                    int(horse["調教師リーディング順位"])
                    if horse.get("調教師リーディング順位") is not None
                    else np.nan
                ),
                "騎手×競馬場": int(horse.get("騎手×競馬場回数", 0) or 0),
                "騎手×距離": int(horse.get("騎手×距離回数", 0) or 0),
                "騎手×コース": int(horse.get("騎手×コース回数", 0) or 0),
                "斤量": (
                    float(horse["斤量"])
                    if horse.get("斤量") is not None
                    else np.nan
                ),
                "脚質": str(horse.get("脚質") or "不明"),
                "単勝オッズ": (
                    float(odds)
                    if odds is not None
                    else np.nan
                ),
                "モデル確率(%)": round(
                    model_probability * 100,
                    2,
                ),
                "Value Index": (
                    value_index
                    if value_index is not None
                    else np.nan
                ),
            })

        result_df = pd.DataFrame(result_rows)

        result_df = result_df.sort_values(
            by="モデル確率(%)",
            ascending=False,
        ).reset_index(drop=True)

        # 馬番が重複していないことを最後にも確認
        result_df = result_df.drop_duplicates(
            subset=["馬番"],
            keep="first",
        ).reset_index(drop=True)

        top_horse = result_df.iloc[0]["馬名"]

        partners = result_df.iloc[
            1:3
        ]["馬名"].tolist()

        partner_horses = ", ".join(partners)

        top_odds = result_df.iloc[0]["単勝オッズ"]

        top_odds_value = (
            float(top_odds)
            if pd.notna(top_odds)
            else 0.0
        )

        active_biases = []

        if front_bias:
            active_biases.append("前残り")
        if inside_bias:
            active_biases.append("内枠")
        if outer_bias:
            active_biases.append("外差し")
        if green_belt:
            active_biases.append("グリーンベルト")
        if g1_mode:
            active_biases.append("G1サイン")

        bias_text = (
            ",".join(active_biases)
            if active_biases
            else "なし"
        )

        prediction_time = datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        prediction_log_id = create_prediction_log_id(
            fetched_info["race_id"],
            prediction_time,
        )

        st.session_state["latest_prediction"] = {
            "prediction_log_id": prediction_log_id,
            "race_id": fetched_info["race_id"],
            "race_name": fetched_info["race_name"],
            "race_date": fetched_info["race_date"].strftime(
                "%Y-%m-%d"
            ),
            "predict_time": prediction_time,
            "data_fetched_at": fetched_info["fetched_at"].strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            "course": (
                f"{fetched_info['venue']}{track_type}"
            ),
            "distance": final_distance,
            "condition": condition,
            "head_count": len(horses),
            "confidence": confidence,
            "result_df": result_df,
            "top_horse": top_horse,
            "partner_horses": partner_horses,
            "top_odds": top_odds_value,
            "budget": budget,
            "bias_text": bias_text,
            "odds_status": fetched_info["odds_status"],
            "odds_coverage": fetched_info["odds_coverage"],
        }

        st.success("予想計算が完了しました。")

    latest = st.session_state.get(
        "latest_prediction"
    )

    if latest:

        st.markdown("### 📊 出走馬・モデル評価")

        st.caption(
            "オッズ未取得の場合は「-」で表示します。"
            "脚質は通常出馬表に加えて過去走表示ページから補完します。"
            "騎手・調教師評価はJRA公式リーディングと騎乗履歴を別特徴量として反映します。"
        )

        display_df = latest["result_df"].copy()

        # None / NaNを画面上で「-」に統一
        display_df["斤量"] = display_df["斤量"].apply(
            lambda x: f"{float(x):.1f}"
            if pd.notna(x)
            else "-"
        )

        display_df["単勝オッズ"] = display_df[
            "単勝オッズ"
        ].apply(
            lambda x: f"{float(x):.1f}"
            if pd.notna(x)
            else "-"
        )

        display_df["Value Index"] = display_df[
            "Value Index"
        ].apply(
            lambda x: f"{float(x):.2f}"
            if pd.notna(x)
            else "-"
        )

        st.dataframe(
            display_df,
            use_container_width=True,
            hide_index=True,
        )

        r1, r2, r3 = st.columns(3)

        with r1:
            st.subheader(
                f"◎ モデル1位: {latest['top_horse']}"
            )
            st.write(
                f"相手: {latest['partner_horses']}"
            )

        with r2:
            st.subheader("📡 データ状態")
            st.write(
                f"オッズ: {latest['odds_status']}"
            )
            st.write(
                f"取得率: {latest['odds_coverage']:.0%}"
            )

        with r3:
            st.subheader("⚙️ 設定")
            st.write(
                f"勝負度: {latest['confidence']}"
            )
            st.write(
                f"補正: {latest['bias_text']}"
            )

        st.markdown("### 💎 Value Index候補")

        raw_value = pd.to_numeric(
            latest["result_df"]["Value Index"],
            errors="coerce",
        )

        value_candidates = latest[
            "result_df"
        ].loc[
            raw_value > 1
        ].copy()

        if value_candidates.empty:
            st.info(
                "Value Index > 1.00 の馬はありません。"
            )
        else:
            value_display = value_candidates.copy()

            value_display["斤量"] = value_display[
                "斤量"
            ].apply(
                lambda x: f"{float(x):.1f}"
                if pd.notna(x)
                else "-"
            )

            value_display["単勝オッズ"] = value_display[
                "単勝オッズ"
            ].apply(
                lambda x: f"{float(x):.1f}"
                if pd.notna(x)
                else "-"
            )

            value_display["Value Index"] = value_display[
                "Value Index"
            ].apply(
                lambda x: f"{float(x):.2f}"
                if pd.notna(x)
                else "-"
            )

            st.dataframe(
                value_display,
                use_container_width=True,
                hide_index=True,
            )

        st.warning(
            "⚠️ Value Index > 1.00 は、モデル評価と市場オッズの"
            "比率が1を超えていることを示すだけで、"
            "的中・利益・期待値を保証するものではありません。"
        )

        st.markdown("### 💰 参考資金配分")

        budget_amount = float(
            latest["budget"]
        )

        model_df = latest[
            "result_df"
        ].head(3).copy()

        if len(model_df) > 0:

            raw_weights = np.array(
                [0.50, 0.30, 0.20][:len(model_df)],
                dtype=float,
            )

            raw_weights = (
                raw_weights
                / raw_weights.sum()
            )

            allocation_rows = []

            for i, (_, row) in enumerate(
                model_df.iterrows()
            ):
                allocation_rows.append({
                    "順位": i + 1,
                    "馬名": row["馬名"],
                    "モデル確率": row["モデル確率(%)"],
                    "参考配分": int(
                        budget_amount
                        * raw_weights[i]
                    ),
                })

            allocation_df = pd.DataFrame(
                allocation_rows
            )

            st.dataframe(
                allocation_df,
                use_container_width=True,
                hide_index=True,
            )

        st.caption(
            "※ 上記はモデル順位に基づく固定比率の参考表示です。"
        )

        st.markdown("---")

        if st.button(
            "📥 この予想ログを保存",
            use_container_width=True,
        ):

            history_df = load_history_df()

            new_record = {
                "予測ログID": latest["prediction_log_id"],
                "レースID": latest["race_id"],
                "レース名": latest["race_name"],
                "開催日": latest["race_date"],
                "予想日時": latest["predict_time"],
                "データ取得日時": latest["data_fetched_at"],
                "コース": latest["course"],
                "距離": latest["distance"],
                "馬場状態": latest["condition"],
                "出走頭数": latest["head_count"],
                "勝負度": latest["confidence"],
                "軸馬": latest["top_horse"],
                "相手馬": latest["partner_horses"],
                "軸馬オッズ": latest["top_odds"],
                "バイアス履歴": latest["bias_text"],
                "モデルバージョン": VERSION,
                "確定フラグ": "未確定",
                "回収額": 0,
                "収支": 0,
                "メモ": "",
                "投資額": latest["budget"],
                "オッズ状態": latest["odds_status"],
                "オッズ取得率": latest["odds_coverage"],
            }

            already_exists = (
                not history_df.empty
                and (
                    history_df["予測ログID"]
                    .astype(str)
                    == str(
                        latest["prediction_log_id"]
                    )
                ).any()
            )

            if already_exists:
                st.warning(
                    "この予測ログは既に保存されています。"
                )
            else:
                updated_df = pd.concat(
                    [
                        history_df,
                        pd.DataFrame([new_record]),
                    ],
                    ignore_index=True,
                )

                if save_history_df(updated_df):
                    st.success(
                        "✅ 予測ログを保存しました。"
                    )


# ============================================================
# 11. 成績ダッシュボード
# ============================================================

elif mode == "📊 成績ダッシュボード・結果入力":

    st.header("📊 成績ダッシュボード")

    df = load_history_df()

    if df.empty:
        st.info("まだ予測履歴がありません。")
        st.stop()

    df = sanitize_df_types(df)

    confirmed_df = df[
        df["確定フラグ"] == "確定"
    ].copy()

    total_logs = len(df)
    confirmed_logs = len(confirmed_df)

    total_investment = (
        confirmed_df["投資額"].astype(float).sum()
        if not confirmed_df.empty
        else 0
    )

    total_return = (
        confirmed_df["回収額"].astype(float).sum()
        if not confirmed_df.empty
        else 0
    )

    total_balance = (
        total_return - total_investment
    )

    recovery_rate = (
        total_return / total_investment * 100
        if total_investment > 0
        else 0
    )

    m1, m2, m3, m4 = st.columns(4)

    m1.metric(
        "予測ログ数",
        f"{total_logs:,}",
    )

    m2.metric(
        "確定ログ数",
        f"{confirmed_logs:,}",
    )

    m3.metric(
        "回収率",
        f"{recovery_rate:.1f}%",
    )

    m4.metric(
        "累計収支",
        f"{int(total_balance):+,}円",
    )

    st.markdown("### 📡 オッズ状態別ログ")

    odds_summary = (
        df.groupby("オッズ状態")
        .size()
        .reset_index(name="件数")
    )

    st.dataframe(
        odds_summary,
        use_container_width=True,
        hide_index=True,
    )

    st.markdown("---")
    st.subheader("📝 未確定ログの結果入力")

    unconfirmed_df = df[
        df["確定フラグ"] == "未確定"
    ].copy()

    if unconfirmed_df.empty:

        st.info("現在、未確定ログはありません。")

    else:

        with st.form("result_update_form"):

            options = []

            for _, row in unconfirmed_df.iterrows():
                options.append(
                    f"{row['予測ログID']} | "
                    f"{row['開催日']} | "
                    f"{row['レース名']} | "
                    f"{row['予想日時']} | "
                    f"{row['オッズ状態']}"
                )

            selected_option = st.selectbox(
                "結果を入力する予測ログ",
                options,
            )

            selected_position = options.index(
                selected_option
            )

            selected_row = unconfirmed_df.iloc[
                selected_position
            ]

            st.caption(
                f"レース: **{selected_row['レース名']}** "
                f"| 軸: **{selected_row['軸馬']}** "
                f"| 投資: **{selected_row['投資額']}円**"
            )

            input_return = st.number_input(
                "回収額 / 払戻金",
                min_value=0,
                value=0,
                step=100,
            )

            input_memo = st.text_input(
                "メモ",
                value="",
            )

            submit = st.form_submit_button(
                "確定成績を保存"
            )

            if submit:

                target_log_id = selected_row[
                    "予測ログID"
                ]

                match_idx = df[
                    df["予測ログID"].astype(str)
                    == str(target_log_id)
                ].index

                if match_idx.empty:
                    st.error("対象ログが見つかりません。")
                else:
                    idx = match_idx[0]

                    investment = float(
                        df.loc[idx, "投資額"]
                    )

                    df.loc[idx, "確定フラグ"] = "確定"
                    df.loc[idx, "回収額"] = input_return
                    df.loc[idx, "収支"] = (
                        input_return - investment
                    )
                    df.loc[idx, "メモ"] = input_memo

                    if save_history_df(df):
                        st.success(
                            "✅ 確定成績を保存しました。"
                        )
                        st.rerun()

    st.markdown("---")
    st.subheader("📋 全予測ログ")

    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
    )

    st.download_button(
        label="📥 CSVをダウンロード",
        data=df.to_csv(
            index=False,
            encoding="utf-8-sig",
        ),
        file_name=CSV_FILENAME,
        mime="text/csv",
        use_container_width=True,
    )
