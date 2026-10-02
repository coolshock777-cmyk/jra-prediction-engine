import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import re
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
    layout="wide"
)


# ============================================================
# 1. バージョン・マスター
# ============================================================

VERSION = "Ver.2.06"

APP_TITLE = "🏇 JRA AI予想 & 成績検証エンジン"

JRA_VENUES = [
    "東京", "中山", "阪神", "京都", "中京",
    "新潟", "福島", "小倉", "札幌", "函館"
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

VENUE_NAME_MAP = {
    v: k for k, v in VENUE_CODE_MAP.items()
}

TURF_DISTANCES = [
    "1000m", "1200m", "1400m", "1500m", "1600m",
    "1800m", "2000m", "2200m", "2400m", "2500m",
    "2600m", "3000m", "3200m", "3400m", "3600m"
]

DIRT_DISTANCES = [
    "1000m", "1150m", "1200m", "1400m", "1600m",
    "1700m", "1800m", "2100m", "2400m", "2500m"
]

ALL_DISTANCES = sorted(
    list(set(TURF_DISTANCES + DIRT_DISTANCES)),
    key=lambda x: int(x.replace("m", ""))
)

ALL_DISTANCES_WITH_OTHER = ALL_DISTANCES + ["その他"]

CSV_FILENAME = "JRA_Prediction_History.csv"

CSV_COLUMNS = [
    "予測ログID",
    "レースID",
    "レース名",
    "開催日",
    "予想日時",
    "データ取得日時",
    "コース",
    "距離",
    "馬場状態",
    "出走頭数",
    "勝負度",
    "軸馬",
    "相手馬",
    "軸馬オッズ",
    "バイアス履歴",
    "モデルバージョン",
    "確定フラグ",
    "回収額",
    "収支",
    "メモ",
    "投資額",
    "オッズ状態",
    "オッズ取得率",
]


# ============================================================
# 2. セッション状態
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
# 3. 共通HTTP設定
# ============================================================

NETKEIBA_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/120.0.0.0 "
        "Safari/537.36"
    ),
    "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7",
}


# ============================================================
# 4. ログイン
# ============================================================

def check_password():

    if st.session_state["authenticated"]:
        return True

    st.title("🔒 ログイン")

    password_input = st.text_input(
        "パスワードを入力してください",
        type="password"
    )

    if st.button(
        "ログイン",
        use_container_width=True
    ):

        app_password = st.secrets.get(
            "APP_PASSWORD",
            ""
        )

        if (
            app_password
            and password_input == app_password
        ):

            st.session_state[
                "authenticated"
            ] = True

            st.rerun()

        else:

            st.error(
                "パスワードが正しくありません。"
            )

    return False


if not check_password():
    st.stop()


# ============================================================
# 5. CSV管理
# ============================================================

def sanitize_df_types(
    df: pd.DataFrame
) -> pd.DataFrame:

    df = df.copy()

    numeric_cols = [
        "出走頭数",
        "軸馬オッズ",
        "回収額",
        "収支",
        "投資額",
        "オッズ取得率",
    ]

    for col in numeric_cols:

        if col in df.columns:

            df[col] = pd.to_numeric(
                df[col],
                errors="coerce"
            ).fillna(0)

    return df


def normalize_history_columns(
    df: pd.DataFrame
) -> pd.DataFrame:

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
                encoding="utf-8-sig"
            )

            df = normalize_history_columns(df)

            st.session_state[
                "history_df"
            ] = df

            return df

        except Exception as e:

            st.warning(
                "履歴CSVの読み込みに失敗しました。"
                f"新しい履歴として扱います: {e}"
            )

    if "history_df" not in st.session_state:

        st.session_state[
            "history_df"
        ] = pd.DataFrame(
            columns=CSV_COLUMNS
        )

    return st.session_state[
        "history_df"
    ]


def save_history_df(
    df: pd.DataFrame
):

    df = normalize_history_columns(df)

    st.session_state[
        "history_df"
    ] = df

    try:

        df.to_csv(
            CSV_FILENAME,
            index=False,
            encoding="utf-8-sig"
        )

        return True

    except Exception as e:

        st.error(
            f"ファイル保存エラー: {str(e)}"
        )

        return False


load_history_df()


# ============================================================
# 6. レースID
# ============================================================

def generate_jra_race_id(
    year: int,
    venue_name: str,
    kai: int,
    nichi: int,
    race_num: int
) -> str:

    venue_code = VENUE_CODE_MAP.get(
        venue_name,
        "05"
    )

    return (
        f"{year}"
        f"{venue_code}"
        f"{kai:02d}"
        f"{nichi:02d}"
        f"{race_num:02d}"
    )


def extract_race_id(
    value: str
):

    if not value:
        return None

    match = re.search(
        r"(\d{12})",
        str(value)
    )

    if not match:
        return None

    return match.group(1)


# ============================================================
# 7. 距離解析
# ============================================================

def parse_distance_from_text(
    text: str
) -> str:

    if not text:
        return "その他"

    text = str(text)

    match = re.search(
        r"(\d{3,4})\s*m",
        text,
        flags=re.IGNORECASE
    )

    if match:

        dist = f"{match.group(1)}m"

        if dist in ALL_DISTANCES:
            return dist

    match = re.search(
        r"(\d{3,4})",
        text
    )

    if match:

        dist = f"{match.group(1)}m"

        if dist in ALL_DISTANCES:
            return dist

    return "その他"


# ============================================================
# 8. オッズ抽出
# ============================================================

def parse_odds(
    text: str
):

    if not text:
        return None

    text = str(text).strip()

    # --------------------------------------------------------
    # 取消・除外・発走前などはNone
    # --------------------------------------------------------

    invalid_words = [
        "---",
        "取消",
        "除外",
        "中止",
        "欠",
    ]

    if any(
        word in text
        for word in invalid_words
    ):
        return None

    # --------------------------------------------------------
    # 例:
    # 3.8
    # 12.5
    # 12
    # --------------------------------------------------------

    match = re.search(
        r"(\d+(?:\.\d+)?)",
        text
    )

    if not match:
        return None

    try:

        value = float(
            match.group(1)
        )

        if value >= 1.0:
            return value

    except Exception:
        pass

    return None


# ============================================================
# 9. 脚質抽出
# ============================================================

def extract_running_style(
    row
):

    style_patterns = [
        ("追い込み", "追込"),
        ("追込み", "追込"),
        ("追込", "追込"),
        ("逃げ", "逃げ"),
        ("先行", "先行"),
        ("差し", "差し"),
        ("自在", "自在"),
    ]

    # --------------------------------------------------------
    # ① クラス名から探す
    # --------------------------------------------------------

    candidate_elements = row.find_all(
        [
            "td",
            "span",
            "div",
            "p"
        ],
        class_=re.compile(
            "Kyakushitsu|"
            "RunningStyle|"
            "Style|"
            "Running|"
            "Kyakushi",
            re.IGNORECASE
        )
    )

    for element in candidate_elements:

        text = element.get_text(
            " ",
            strip=True
        )

        if not text:
            continue

        for source, result in style_patterns:

            if source in text:
                return result

    # --------------------------------------------------------
    # ② data属性などに入っている可能性を確認
    # --------------------------------------------------------

    for element in row.find_all(
        True
    ):

        attrs = " ".join(
            [
                str(v)
                for k, v in element.attrs.items()
                if k in [
                    "class",
                    "data-style",
                    "data-kakushitsu",
                    "data-running-style"
                ]
            ]
        )

        if not attrs:
            continue

        for source, result in style_patterns:

            if source in attrs:
                return result

    # --------------------------------------------------------
    # ③ 行全体のテキストから明確な脚質表記を探す
    #
    # ※ 騎手名・馬名などの偶然の文字列による誤認を避けるため
    #    脚質欄らしい短いテキストを優先する
    # --------------------------------------------------------

    for element in row.find_all(
        ["td", "span"]
    ):

        text = element.get_text(
            " ",
            strip=True
        )

        if len(text) > 8:
            continue

        for source, result in style_patterns:

            if text == source:
                return result

    return "不明"


# ============================================================
# 10. 単勝オッズ取得
# ============================================================

@st.cache_data(
    ttl=10,
    show_spinner=False
)
def fetch_netkeiba_odds(
    race_id: str
):

    """
    netkeibaの単勝オッズページから
    {馬番: オッズ} の辞書を取得する。

    例:
        {
            1: 3.8,
            2: 7.2,
            3: 12.5
        }
    """

    url = (
        "https://race.netkeiba.com/odds/index.html"
        f"?race_id={race_id}&type=b1"
    )

    odds_map = {}

    try:

        response = requests.get(
            url,
            headers=NETKEIBA_HEADERS,
            timeout=15
        )

        response.raise_for_status()

        response.encoding = (
            response.apparent_encoding
            or "utf-8"
        )

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

        # ----------------------------------------------------
        # 単勝テーブル候補を探す
        # ----------------------------------------------------

        target_tables = []

        for table in soup.find_all("table"):

            table_text = table.get_text(
                " ",
                strip=True
            )

            if (
                "単勝" in table_text
                and "オッズ" in table_text
            ):

                target_tables.append(
                    table
                )

        # ----------------------------------------------------
        # 候補テーブルを順番に解析
        # ----------------------------------------------------

        for table in target_tables:

            rows = table.find_all("tr")

            for row in rows:

                cells = [
                    cell.get_text(
                        " ",
                        strip=True
                    )
                    for cell in row.find_all(
                        ["th", "td"]
                    )
                ]

                if len(cells) < 3:
                    continue

                # --------------------------------------------
                # 馬番を探す
                # --------------------------------------------

                horse_no = None

                # まず2桁以内の単独数字を探す
                for cell in cells:

                    normalized = (
                        cell
                        .replace(
                            "　",
                            ""
                        )
                        .strip()
                    )

                    if re.fullmatch(
                        r"\d{1,2}",
                        normalized
                    ):

                        try:

                            number = int(
                                normalized
                            )

                            if 1 <= number <= 18:

                                horse_no = number
                                break

                        except Exception:
                            pass

                if horse_no is None:
                    continue

                # --------------------------------------------
                # オッズを探す
                # --------------------------------------------

                odds = None

                # オッズは行の後半にあることが多いため
                # 後ろから確認
                for cell in reversed(cells):

                    parsed = parse_odds(
                        cell
                    )

                    if parsed is not None:

                        odds = parsed
                        break

                if odds is not None:

                    odds_map[
                        horse_no
                    ] = odds

            # 十分取れたら終了
            if len(odds_map) >= 2:
                break

        return odds_map

    except requests.exceptions.RequestException:
        return {}

    except Exception:
        return {}


# ============================================================
# 11. netkeibaデータ取得
# ============================================================

@st.cache_data(
    ttl=30,
    show_spinner=False
)
def fetch_netkeiba_race_data_cached(
    race_id: str
):

    url = (
        "https://race.netkeiba.com/race/"
        f"shutuba.html?race_id={race_id}"
    )

    try:

        response = requests.get(
            url,
            headers=NETKEIBA_HEADERS,
            timeout=15
        )

        response.raise_for_status()

        response.encoding = (
            response.apparent_encoding
            or "utf-8"
        )

        html = response.text

        soup = BeautifulSoup(
            html,
            "html.parser"
        )

        race_data_01 = soup.find(
            "div",
            class_="RaceData01"
        )

        if race_data_01 is None:

            return None, (
                f"レース(ID:{race_id})が取得できませんでした。"
                "レースID、開催日、開催回、日目をご確認ください。"
            )

        venue_code = race_id[4:6]

        detected_venue = VENUE_NAME_MAP.get(
            venue_code,
            "不明"
        )

        race_num = int(
            race_id[-2:]
        )

        extracted = {
            "race_id": race_id,
            "venue": detected_venue,
            "race_num": race_num,
            "track_type": "芝",
            "distance": "その他",
            "condition": "良",
            "race_name": f"レース_{race_id}",
            "race_date": None,
            "horses": [],
            "kyaku_count": 0,
            "front_runner_count": 0,
            "odds_coverage": 0.0,
            "odds_status": "前日暫定",
            "fetched_at": datetime.now(),
        }

        # ====================================================
        # レース日
        # ====================================================

        race_data_02 = soup.find(
            "div",
            class_="RaceData02"
        )

        if race_data_02:

            date_match = re.search(
                r"(\d{4})年(\d{1,2})月(\d{1,2})日",
                race_data_02.get_text(
                    " ",
                    strip=True
                )
            )

            if date_match:

                y, m, d = map(
                    int,
                    date_match.groups()
                )

                extracted[
                    "race_date"
                ] = datetime(
                    y,
                    m,
                    d
                )

        if extracted[
            "race_date"
        ] is None:

            extracted[
                "race_date"
            ] = datetime.now()

        # ====================================================
        # レース名
        # ====================================================

        race_title = soup.find(
            "div",
            class_="RaceName"
        )

        if race_title:

            name = race_title.get_text(
                " ",
                strip=True
            )

            if name:
                extracted[
                    "race_name"
                ] = name

        # ====================================================
        # コース・距離・馬場
        # ====================================================

        race_text = race_data_01.get_text(
            " ",
            strip=True
        )

        if "ダ" in race_text:

            extracted[
                "track_type"
            ] = "ダート"

        elif "障" in race_text:

            extracted[
                "track_type"
            ] = "障害"

        else:

            extracted[
                "track_type"
            ] = "芝"

        parsed_distance = (
            parse_distance_from_text(
                race_text
            )
        )

        if parsed_distance != "その他":

            extracted[
                "distance"
            ] = parsed_distance

        if "不良" in race_text:

            extracted[
                "condition"
            ] = "不良"

        elif "稍重" in race_text:

            extracted[
                "condition"
            ] = "稍重"

        elif "重" in race_text:

            extracted[
                "condition"
            ] = "重"

        else:

            extracted[
                "condition"
            ] = "良"

        # ====================================================
        # 出走馬
        # ====================================================

        horse_rows = soup.find_all(
            "tr",
            class_="HorseList"
        )

        if not horse_rows:

            return None, (
                "出走馬テーブルが見つかりませんでした。"
            )

        horses = []

        kyaku_count = 0
        front_runner_count = 0

        # ====================================================
        # HorseList解析
        # ====================================================

        for row in horse_rows:

            try:

                # ------------------------------------------------
                # 枠番
                # ------------------------------------------------

                waku_elem = row.find(
                    "td",
                    class_=re.compile(
                        "Waku"
                    )
                )

                waku_text = (
                    waku_elem.get_text(
                        strip=True
                    )
                    if waku_elem
                    else ""
                )

                waku_match = re.search(
                    r"\d+",
                    waku_text
                )

                waku = (
                    int(
                        waku_match.group()
                    )
                    if waku_match
                    else 0
                )

                # ------------------------------------------------
                # 馬番
                # ------------------------------------------------

                uma_elem = row.find(
                    "td",
                    class_=re.compile(
                        "Umaban"
                    )
                )

                uma_text = (
                    uma_elem.get_text(
                        strip=True
                    )
                    if uma_elem
                    else ""
                )

                uma_match = re.search(
                    r"\d+",
                    uma_text
                )

                uma = (
                    int(
                        uma_match.group()
                    )
                    if uma_match
                    else 0
                )

                # ------------------------------------------------
                # 馬名
                # ------------------------------------------------

                horse_elem = row.find(
                    "span",
                    class_="HorseName"
                )

                if horse_elem is None:

                    horse_elem = row.find(
                        class_=re.compile(
                            "HorseName"
                        )
                    )

                horse_name = (
                    horse_elem.get_text(
                        " ",
                        strip=True
                    )
                    if horse_elem
                    else ""
                )

                # ------------------------------------------------
                # 騎手
                # ------------------------------------------------

                jockey_elem = row.find(
                    "td",
                    class_="Jockey"
                )

                if jockey_elem is None:

                    jockey_elem = row.find(
                        class_=re.compile(
                            "Jockey"
                        )
                    )

                jockey = (
                    jockey_elem.get_text(
                        " ",
                        strip=True
                    )
                    if jockey_elem
                    else ""
                )

                # ------------------------------------------------
                # 斤量
                # ------------------------------------------------

                kinryo = 55.0

                td_elems = row.find_all(
                    "td"
                )

                for td in td_elems:

                    td_txt = td.get_text(
                        strip=True
                    )

                    m_kin = re.match(
                        r"^(\d{2}\.\d)$",
                        td_txt
                    )

                    if m_kin:

                        kinryo = float(
                            m_kin.group(1)
                        )

                        break

                # ------------------------------------------------
                # 脚質
                # ------------------------------------------------

                kyakushitsu = (
                    extract_running_style(
                        row
                    )
                )

                if kyakushitsu != "不明":

                    kyaku_count += 1

                    if (
                        "逃" in kyakushitsu
                        or "先" in kyakushitsu
                    ):

                        front_runner_count += 1

                # ------------------------------------------------
                # オッズ
                #
                # ★ ここでは取得しない
                # ★ 後で単勝オッズページから取得する
                # ------------------------------------------------

                odds = None

                # ------------------------------------------------
                # 馬データ登録
                # ------------------------------------------------

                if (
                    horse_name
                    and uma > 0
                ):

                    horses.append(
                        {
                            "枠番": waku,
                            "馬番": uma,
                            "馬名": horse_name,
                            "騎手": jockey,
                            "斤量": kinryo,
                            "脚質": kyakushitsu,
                            "オッズ": odds,
                        }
                    )

            except Exception:
                continue

        if not horses:

            return None, (
                "出走馬データの抽出件数が0件です。"
            )

        # ====================================================
        # ★ 単勝オッズを別ページから取得
        # ====================================================

        odds_map = fetch_netkeiba_odds(
            race_id
        )

        # 馬番をキーにして結合
        for horse in horses:

            horse_no = horse.get(
                "馬番"
            )

            horse["オッズ"] = (
                odds_map.get(
                    horse_no
                )
            )

        # ====================================================
        # オッズ取得率
        # ====================================================

        valid_odds_count = sum(
            1
            for h in horses
            if h.get("オッズ") is not None
        )

        odds_coverage = (
            valid_odds_count
            / len(horses)
            if len(horses) > 0
            else 0.0
        )

        if odds_coverage >= 0.99:

            odds_status = "当日確定"

        elif odds_coverage >= 0.70:

            odds_status = "当日一部欠損"

        else:

            odds_status = "前日暫定"

        # ====================================================
        # 結果格納
        # ====================================================

        extracted[
            "horses"
        ] = horses

        extracted[
            "kyaku_count"
        ] = kyaku_count

        extracted[
            "front_runner_count"
        ] = front_runner_count

        extracted[
            "odds_coverage"
        ] = odds_coverage

        extracted[
            "odds_status"
        ] = odds_status

        return extracted, None

    except requests.exceptions.RequestException as e:

        return None, (
            f"通信エラー: {str(e)}"
        )

    except Exception as e:

        return None, (
            f"解析エラー: {str(e)}"
        )


def fetch_netkeiba_race_data(
    race_id_or_url
):

    race_id = extract_race_id(
        race_id_or_url
    )

    if not race_id:

        return None, (
            "有効な12桁のレースIDが見つかりません。"
        )

    return fetch_netkeiba_race_data_cached(
        race_id
    )


# ============================================================
# 12. モデル補正
# ============================================================

def safe_log_multiplier(
    multiplier: float
) -> float:

    return np.log(
        max(
            multiplier,
            0.01
        )
    )


def calculate_model_score(
    horse,
    fetched_info,
    track_type,
    distance,
    front_bias,
    inside_bias,
    outer_bias,
    green_belt,
    g1_mode
):

    score = 0.0

    horse_num = int(
        horse.get(
            "馬番",
            0
        )
    )

    waku_num = int(
        horse.get(
            "枠番",
            0
        )
    )

    jockey = str(
        horse.get(
            "騎手",
            ""
        )
    ).replace(
        " ",
        ""
    ).replace(
        "　",
        ""
    )

    style = str(
        horse.get(
            "脚質",
            ""
        )
    )

    total_horses = len(
        fetched_info["horses"]
    )

    kyaku_rate = (
        fetched_info[
            "kyaku_count"
        ]
        / max(
            total_horses,
            1
        )
    )

    # --------------------------------------------------------
    # 騎手補正
    # --------------------------------------------------------

    if "ルメール" in jockey:

        score += safe_log_multiplier(
            1.15
        )

    elif "川田" in jockey:

        score += safe_log_multiplier(
            1.15
        )

    # --------------------------------------------------------
    # 前残り補正
    # --------------------------------------------------------

    if (
        front_bias
        and kyaku_rate >= 0.70
        and (
            "逃" in style
            or "先" in style
        )
    ):

        score += safe_log_multiplier(
            1.08
        )

    # --------------------------------------------------------
    # 内枠補正
    # --------------------------------------------------------

    if inside_bias:

        if waku_num in [1, 2]:

            score += safe_log_multiplier(
                1.05
            )

        elif (
            waku_num == 0
            and horse_num <= 2
        ):

            score += safe_log_multiplier(
                1.05
            )

    # --------------------------------------------------------
    # 外差し補正
    # --------------------------------------------------------

    if outer_bias:

        if waku_num in [7, 8]:

            score += safe_log_multiplier(
                1.05
            )

        elif (
            waku_num == 0
            and horse_num >= total_horses - 2
        ):

            score += safe_log_multiplier(
                1.05
            )

    # --------------------------------------------------------
    # ダート1200m内枠補正
    # --------------------------------------------------------

    if (
        track_type == "ダート"
        and distance == "1200m"
    ):

        if waku_num in [1, 2]:

            score += safe_log_multiplier(
                1.08
            )

    # --------------------------------------------------------
    # グリーンベルト
    # --------------------------------------------------------

    if (
        green_belt
        and horse_num == 1
    ):

        score += safe_log_multiplier(
            1.06
        )

    # --------------------------------------------------------
    # G1サイン
    # --------------------------------------------------------

    if (
        g1_mode
        and horse_num in [1, 3, 7]
    ):

        score += safe_log_multiplier(
            1.03
        )

    return score


# ============================================================
# 13. モデル確率化
# ============================================================

def scores_to_probabilities(
    scores
):

    scores = np.asarray(
        scores,
        dtype=float
    )

    if len(scores) == 0:

        return np.array([])

    max_score = np.max(
        scores
    )

    exp_scores = np.exp(
        scores - max_score
    )

    total = np.sum(
        exp_scores
    )

    if total <= 0:

        return (
            np.ones(
                len(scores)
            )
            / len(scores)
        )

    return (
        exp_scores
        / total
    )


# ============================================================
# 14. Value Index
# ============================================================

def calculate_value_index(
    model_probability,
    odds,
    odds_status
):

    if odds_status not in [
        "当日確定",
        "当日一部欠損"
    ]:

        return None

    if odds is None:
        return None

    try:

        odds = float(
            odds
        )

    except Exception:

        return None

    if odds <= 0:
        return None

    market_implied_probability = (
        1.0 / odds
    )

    if market_implied_probability <= 0:
        return None

    value_index = (
        model_probability
        / market_implied_probability
    )

    return round(
        float(value_index),
        2
    )


# ============================================================
# 15. 予測ログID
# ============================================================

def create_prediction_log_id(
    race_id,
    prediction_time
):

    raw = (
        f"{race_id}|"
        f"{prediction_time}|"
        f"{VERSION}"
    )

    digest = hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()[:12]

    return (
        f"{race_id}-{digest}"
    )


# ============================================================
# 16. サイドバー
# ============================================================

st.sidebar.title(
    "🏇 JRA AI予想 engine"
)

st.sidebar.caption(
    f"モデル: **{VERSION}**"
)

mode = st.sidebar.radio(
    "機能メニュー",
    [
        "🏇 リアルタイム予想",
        "📊 成績ダッシュボード・結果入力",
    ]
)


# ============================================================
# 17. リアルタイム予想
# ============================================================

if mode == "🏇 リアルタイム予想":

    st.header(
        "🏇 リアルタイム予想 & スコアリング"
    )

    st.info(
        "このモデルは、出走馬情報・騎手・斤量・脚質・枠・"
        "コース条件などからモデル内相対評価を算出し、"
        "その後に市場オッズと比較してValue Indexを計算します。"
    )

    st.markdown(
        "### 📅 対象レース"
    )

    c1, c2, c3, c4 = st.columns(4)

    with c1:

        selected_date = st.date_input(
            "開催日",
            value=pd.Timestamp.now()
        )

    with c2:

        selected_venue = st.selectbox(
            "競馬場",
            JRA_VENUES
        )

    with c3:

        kai_val = st.number_input(
            "開催回",
            min_value=1,
            max_value=6,
            value=4
        )

    with c4:

        nichi_val = st.number_input(
            "日目",
            min_value=1,
            max_value=12,
            value=8
        )

    c5, c6 = st.columns([1, 2])

    with c5:

        race_num_val = st.selectbox(
            "レース番号",
            [
                f"{i}R"
                for i in range(1, 13)
            ],
            index=10
        )

        race_num = int(
            race_num_val.replace(
                "R",
                ""
            )
        )

    auto_race_id = generate_jra_race_id(
        selected_date.year,
        selected_venue,
        kai_val,
        nichi_val,
        race_num
    )

    with c6:

        manual_input = st.text_input(
            "直接URL / 12桁レースID",
            placeholder=auto_race_id
        )

    target_race_id = (
        extract_race_id(
            manual_input
        )
        if manual_input.strip()
        else auto_race_id
    )

    if st.button(
        "🔄 出走表・最新データ取得",
        use_container_width=True
    ):

        with st.spinner(
            f"レースID {target_race_id} を取得中..."
        ):

            info, error = (
                fetch_netkeiba_race_data(
                    target_race_id
                )
            )

        if error:

            st.error(
                error
            )

        else:

            st.session_state[
                "fetched_info"
            ] = info

            st.session_state[
                "latest_prediction"
            ] = None

            st.rerun()

    fetched_info = (
        st.session_state.get(
            "fetched_info"
        )
    )

    if fetched_info:

        kyaku_rate = (
            fetched_info[
                "kyaku_count"
            ]
            / max(
                len(
                    fetched_info[
                        "horses"
                    ]
                ),
                1
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
            f"{len(fetched_info['horses'])}頭"
        )

        m2.metric(
            "脚質取得率",
            f"{kyaku_rate:.0%}"
        )

        m3.metric(
            "オッズ取得率",
            f"{fetched_info['odds_coverage']:.0%}"
        )

        m4.metric(
            "オッズ状態",
            fetched_info[
                "odds_status"
            ]
        )

        if kyaku_rate < 0.70:

            st.warning(
                "脚質取得率が70%未満です。"
                "前残り補正を使う場合は注意してください。"
            )

        if (
            fetched_info[
                "odds_status"
            ]
            == "前日暫定"
        ):

            st.info(
                "現在は前日暫定モードです。"
                "市場オッズとのValue Index比較は行いません。"
            )

        elif (
            fetched_info[
                "odds_status"
            ]
            == "当日一部欠損"
        ):

            st.warning(
                "オッズの一部が取得できていません。"
                "取得できた馬のみValue Indexを表示します。"
            )

        with st.expander(
            "🐎 取得した出走馬データを確認（斤量・脚質・オッズ）"
        ):

            horse_preview = pd.DataFrame(
                fetched_info[
                    "horses"
                ]
            )

            st.dataframe(
                horse_preview,
                use_container_width=True,
                hide_index=True
            )

    st.markdown(
        "### ⚙️ モデル条件"
    )

    c1, c2, c3 = st.columns(3)

    default_track = (
        fetched_info[
            "track_type"
        ]
        if fetched_info
        else "芝"
    )

    with c1:

        st.selectbox_options = [
            "芝",
            "ダート",
            "障害"
        ]

        track_type = st.selectbox(
            "コース種別",
            st.selectbox_options,
            index=(
                st.selectbox_options.index(
                    default_track
                )
                if default_track
                in st.selectbox_options
                else 0
            )
        )

    if track_type == "芝":

        distance_options = (
            TURF_DISTANCES
            + ["その他"]
        )

    elif track_type == "ダート":

        distance_options = (
            DIRT_DISTANCES
            + ["その他"]
        )

    else:

        distance_options = (
            ALL_DISTANCES_WITH_OTHER
        )

    default_distance = (
        fetched_info[
            "distance"
        ]
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
                if default_distance
                in distance_options
                else len(
                    distance_options
                ) - 1
            )
        )

    if distance == "その他":

        custom_distance = st.text_input(
            "手動距離",
            value=""
        )

        final_distance = (
            custom_distance
            if custom_distance
            else "その他"
        )

    else:

        final_distance = distance

    default_condition = (
        fetched_info[
            "condition"
        ]
        if fetched_info
        else "良"
    )

    with c3:

        condition = st.selectbox(
            "馬場状態",
            [
                "良",
                "稍重",
                "重",
                "不良"
            ],
            index=(
                [
                    "良",
                    "稍重",
                    "重",
                    "不良"
                ].index(
                    default_condition
                )
                if default_condition
                in [
                    "良",
                    "稍重",
                    "重",
                    "不良"
                ]
                else 0
            )
        )

    st.markdown(
        "### 🎛️ 補正設定"
    )

    b1, b2, b3 = st.columns(3)

    with b1:

        front_bias = st.checkbox(
            "前残り補正",
            value=True
        )

        inside_bias = st.checkbox(
            "内枠補正",
            value=False
        )

    with b2:

        outer_bias = st.checkbox(
            "外差し補正",
            value=False
        )

        green_belt = st.checkbox(
            "グリーンベルト補正",
            value=False
        )

    with b3:

        g1_mode = st.checkbox(
            "G1サインモード",
            value=False
        )

        confidence = st.select_slider(
            "勝負度",
            options=[
                "★☆☆",
                "★★☆",
                "★★★"
            ],
            value="★★☆"
        )

    budget = st.number_input(
        "参考予算",
        min_value=100,
        value=1000,
        step=100
    )

    if not fetched_info:

        st.info(
            "まず出走表を取得してください。"
        )

        run_disabled = True

    else:

        run_disabled = False

    if st.button(
        "🚀 モデル予想を実行",
        disabled=run_disabled,
        use_container_width=True
    ):

        horses = fetched_info[
            "horses"
        ]

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
                g1_mode=g1_mode
            )

            scores.append(
                score
            )

        probabilities = (
            scores_to_probabilities(
                scores
            )
        )

        result_rows = []

        for i, horse in enumerate(
            horses
        ):

            model_probability = (
                probabilities[i]
            )

            odds = horse.get(
                "オッズ"
            )

            value_index = (
                calculate_value_index(
                    model_probability,
                    odds,
                    fetched_info[
                        "odds_status"
                    ]
                )
            )

            result_rows.append(
                {
                    "枠番": int(
                        horse.get(
                            "枠番",
                            0
                        )
                    ),
                    "馬番": int(
                        horse["馬番"]
                    ),
                    "馬名": str(
                        horse["馬名"]
                    ),
                    "騎手": str(
                        horse.get(
                            "騎手",
                            ""
                        )
                    ),
                    "斤量": float(
                        horse.get(
                            "斤量",
                            55.0
                        )
                    ),
                    "脚質": str(
                        horse.get(
                            "脚質",
                            "不明"
                        )
                    ),
                    "単勝オッズ": (
                        float(
                            odds
                        )
                        if odds is not None
                        else "-"
                    ),
                    "モデル確率(%)": round(
                        model_probability
                        * 100,
                        2
                    ),
                    "Value Index": (
                        value_index
                        if value_index is not None
                        else "-"
                    ),
                }
            )

        result_df = pd.DataFrame(
            result_rows
        )

        result_df = (
            result_df
            .sort_values(
                by="モデル確率(%)",
                ascending=False
            )
            .reset_index(
                drop=True
            )
        )

        top_horse = (
            result_df.iloc[0][
                "馬名"
            ]
        )

        partners = (
            result_df.iloc[
                1:3
            ][
                "馬名"
            ].tolist()
        )

        partner_horses = (
            ", ".join(
                partners
            )
        )

        top_odds = (
            result_df.iloc[0][
                "単勝オッズ"
            ]
        )

        if top_odds == "-":

            top_odds_value = 0.0

        else:

            top_odds_value = float(
                top_odds
            )

        active_biases = []

        if front_bias:
            active_biases.append(
                "前残り"
            )

        if inside_bias:
            active_biases.append(
                "内枠"
            )

        if outer_bias:
            active_biases.append(
                "外差し"
            )

        if green_belt:
            active_biases.append(
                "グリーンベルト"
            )

        if g1_mode:
            active_biases.append(
                "G1サイン"
            )

        bias_text = (
            ",".join(
                active_biases
            )
            if active_biases
            else "なし"
        )

        prediction_time = (
            datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        )

        prediction_log_id = (
            create_prediction_log_id(
                fetched_info[
                    "race_id"
                ],
                prediction_time
            )
        )

        st.session_state[
            "latest_prediction"
        ] = {

            "prediction_log_id":
                prediction_log_id,

            "race_id":
                fetched_info[
                    "race_id"
                ],

            "race_name":
                fetched_info[
                    "race_name"
                ],

            "race_date":
                fetched_info[
                    "race_date"
                ].strftime(
                    "%Y-%m-%d"
                ),

            "predict_time":
                prediction_time,

            "data_fetched_at":
                fetched_info[
                    "fetched_at"
                ].strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),

            "course":
                f"{fetched_info['venue']}"
                f"{track_type}",

            "distance":
                final_distance,

            "condition":
                condition,

            "head_count":
                len(horses),

            "confidence":
                confidence,

            "result_df":
                result_df,

            "top_horse":
                top_horse,

            "partner_horses":
                partner_horses,

            "top_odds":
                top_odds_value,

            "budget":
                budget,

            "bias_text":
                bias_text,

            "odds_status":
                fetched_info[
                    "odds_status"
                ],

            "odds_coverage":
                fetched_info[
                    "odds_coverage"
                ],
        }

        st.success(
            "予想計算が完了しました。"
        )

    latest = (
        st.session_state.get(
            "latest_prediction"
        )
    )

    if latest:

        st.markdown(
            "### 📊 モデル評価"
        )

        st.caption(
            "モデル確率は市場オッズを計算材料に使用せず、"
            "モデル内部のスコアを正規化した値です。"
            "Value Indexは、そのモデル確率と単勝オッズから"
            "後段で計算しています。"
        )

        st.dataframe(
            latest[
                "result_df"
            ],
            use_container_width=True,
            hide_index=True
        )

        r1, r2, r3 = st.columns(3)

        with r1:

            st.subheader(
                f"◎ モデル1位: "
                f"{latest['top_horse']}"
            )

            st.write(
                f"相手: "
                f"{latest['partner_horses']}"
            )

        with r2:

            st.subheader(
                "📡 データ状態"
            )

            st.write(
                f"オッズ: "
                f"{latest['odds_status']}"
            )

            st.write(
                f"取得率: "
                f"{latest['odds_coverage']:.0%}"
            )

        with r3:

            st.subheader(
                "⚙️ 設定"
            )

            st.write(
                f"勝負度: "
                f"{latest['confidence']}"
            )

            st.write(
                f"補正: "
                f"{latest['bias_text']}"
            )

        st.markdown(
            "### 💎 Value Index候補"
        )

        display_df = latest[
            "result_df"
        ].copy()

        value_numeric = pd.to_numeric(
            display_df[
                "Value Index"
            ],
            errors="coerce"
        )

        value_candidates = (
            display_df[
                value_numeric > 1
            ].copy()
        )

        if value_candidates.empty:

            st.info(
                "Value Index > 1.00 の馬はありません。"
            )

        else:

            st.dataframe(
                value_candidates,
                use_container_width=True,
                hide_index=True
            )

        st.warning(
            "⚠️ Value Index > 1.00 は、"
            "モデル評価と市場オッズの比率が1を超えていることを示すだけで、"
            "的中・利益・期待値を保証するものではありません。"
        )

        st.markdown(
            "### 💰 参考資金配分"
        )

        budget_amount = float(
            latest[
                "budget"
            ]
        )

        model_df = (
            latest[
                "result_df"
            ]
            .head(3)
            .copy()
        )

        if len(model_df) > 0:

            raw_weights = np.array(
                [
                    0.50,
                    0.30,
                    0.20
                ][:len(model_df)],
                dtype=float
            )

            raw_weights = (
                raw_weights
                / raw_weights.sum()
            )

            allocation_rows = []

            for i, (_, row) in enumerate(
                model_df.iterrows()
            ):

                allocation_rows.append(
                    {
                        "順位":
                            i + 1,

                        "馬名":
                            row["馬名"],

                        "モデル確率":
                            row[
                                "モデル確率(%)"
                            ],

                        "参考配分":
                            int(
                                budget_amount
                                * raw_weights[i]
                            )
                    }
                )

            allocation_df = pd.DataFrame(
                allocation_rows
            )

            st.dataframe(
                allocation_df,
                use_container_width=True,
                hide_index=True
            )

        st.caption(
            "※ 上記はモデル順位に基づく固定比率の参考表示です。"
            "Value Indexから利益を保証する資金配分ではありません。"
        )

        st.markdown(
            "---"
        )

        if st.button(
            "📥 この予想ログを保存",
            use_container_width=True
        ):

            history_df = (
                load_history_df()
            )

            new_record = {

                "予測ログID":
                    latest[
                        "prediction_log_id"
                    ],

                "レースID":
                    latest[
                        "race_id"
                    ],

                "レース名":
                    latest[
                        "race_name"
                    ],

                "開催日":
                    latest[
                        "race_date"
                    ],

                "予想日時":
                    latest[
                        "predict_time"
                    ],

                "データ取得日時":
                    latest[
                        "data_fetched_at"
                    ],

                "コース":
                    latest[
                        "course"
                    ],

                "距離":
                    latest[
                        "distance"
                    ],

                "馬場状態":
                    latest[
                        "condition"
                    ],

                "出走頭数":
                    latest[
                        "head_count"
                    ],

                "勝負度":
                    latest[
                        "confidence"
                    ],

                "軸馬":
                    latest[
                        "top_horse"
                    ],

                "相手馬":
                    latest[
                        "partner_horses"
                    ],

                "軸馬オッズ":
                    latest[
                        "top_odds"
                    ],

                "バイアス履歴":
                    latest[
                        "bias_text"
                    ],

                "モデルバージョン":
                    VERSION,

                "確定フラグ":
                    "未確定",

                "回収額":
                    0,

                "収支":
                    0,

                "メモ":
                    "",

                "投資額":
                    latest[
                        "budget"
                    ],

                "オッズ状態":
                    latest[
                        "odds_status"
                    ],

                "オッズ取得率":
                    latest[
                        "odds_coverage"
                    ],
            }

            if (
                not history_df.empty
                and (
                    history_df[
                        "予測ログID"
                    ]
                    .astype(str)
                    == str(
                        latest[
                            "prediction_log_id"
                        ]
                    )
                ).any()
            ):

                st.warning(
                    "この予測ログは既に保存されています。"
                )

            else:

                updated_df = pd.concat(
                    [
                        history_df,
                        pd.DataFrame(
                            [new_record]
                        )
                    ],
                    ignore_index=True
                )

                if save_history_df(
                    updated_df
                ):

                    st.success(
                        "✅ 予測ログを保存しました。"
                    )


# ============================================================
# 18. 成績ダッシュボード
# ============================================================

elif mode == "📊 成績ダッシュボード・結果入力":

    st.header(
        "📊 成績ダッシュボード"
    )

    df = load_history_df()

    if df.empty:

        st.info(
            "まだ予測履歴がありません。"
        )

        st.stop()

    df = sanitize_df_types(
        df
    )

    confirmed_df = df[
        df[
            "確定フラグ"
        ] == "確定"
    ].copy()

    total_logs = len(df)

    confirmed_logs = len(
        confirmed_df
    )

    total_investment = (
        confirmed_df[
            "投資額"
        ]
        .astype(float)
        .sum()
        if not confirmed_df.empty
        else 0
    )

    total_return = (
        confirmed_df[
            "回収額"
        ]
        .astype(float)
        .sum()
        if not confirmed_df.empty
        else 0
    )

    total_balance = (
        total_return
        - total_investment
    )

    recovery_rate = (
        total_return
        / total_investment
        * 100
        if total_investment > 0
        else 0
    )

    m1, m2, m3, m4 = st.columns(4)

    m1.metric(
        "予測ログ数",
        f"{total_logs:,}"
    )

    m2.metric(
        "確定ログ数",
        f"{confirmed_logs:,}"
    )

    m3.metric(
        "回収率",
        f"{recovery_rate:.1f}%"
    )

    m4.metric(
        "累計収支",
        f"{int(total_balance):+,}円"
    )

    st.markdown(
        "### 📡 オッズ状態別ログ"
    )

    if not df.empty:

        odds_summary = (
            df.groupby(
                "オッズ状態"
            )
            .size()
            .reset_index(
                name="件数"
            )
        )

        st.dataframe(
            odds_summary,
            use_container_width=True,
            hide_index=True
        )

    st.markdown(
        "---"
    )

    st.subheader(
        "📝 未確定ログの結果入力"
    )

    unconfirmed_df = df[
        df[
            "確定フラグ"
        ] == "未確定"
    ].copy()

    if unconfirmed_df.empty:

        st.info(
            "現在、未確定ログはありません。"
        )

    else:

        with st.form(
            "result_update_form"
        ):

            options = []

            for _, row in (
                unconfirmed_df.iterrows()
            ):

                options.append(
                    f"{row['予測ログID']} | "
                    f"{row['開催日']} | "
                    f"{row['レース名']} | "
                    f"{row['予想日時']} | "
                    f"{row['オッズ状態']}"
                )

            selected_option = st.selectbox(
                "結果を入力する予測ログ",
                options
            )

            selected_position = (
                options.index(
                    selected_option
                )
            )

            selected_row = (
                unconfirmed_df
                .iloc[
                    selected_position
                ]
            )

            st.caption(
                f"レース: "
                f"**{selected_row['レース名']}** "
                f"| 軸: "
                f"**{selected_row['軸馬']}** "
                f"| 投資: "
                f"**{selected_row['投資額']}円**"
            )

            input_return = st.number_input(
                "回収額 / 払戻金",
                min_value=0,
                value=0,
                step=100
            )

            input_memo = st.text_input(
                "メモ",
                value=""
            )

            submit = st.form_submit_button(
                "確定成績を保存"
            )

            if submit:

                target_log_id = (
                    selected_row[
                        "予測ログID"
                    ]
                )

                match_idx = df[
                    df[
                        "予測ログID"
                    ]
                    .astype(str)
                    ==
                    str(
                        target_log_id
                    )
                ].index

                if match_idx.empty:

                    st.error(
                        "対象ログが見つかりません。"
                    )

                else:

                    idx = match_idx[0]

                    investment = float(
                        df.loc[
                            idx,
                            "投資額"
                        ]
                    )

                    df.loc[
                        idx,
                        "確定フラグ"
                    ] = "確定"

                    df.loc[
                        idx,
                        "回収額"
                    ] = input_return

                    df.loc[
                        idx,
                        "収支"
                    ] = (
                        input_return
                        - investment
                    )

                    df.loc[
                        idx,
                        "メモ"
                    ] = input_memo

                    if save_history_df(
                        df
                    ):

                        st.success(
                            "✅ 確定成績を保存しました。"
                        )

                        st.rerun()

    st.markdown(
        "---"
    )

    st.subheader(
        "📋 全予測ログ"
    )

    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True
    )

    st.download_button(
        label="📥 CSVをダウンロード",
        data=df.to_csv(
            index=False,
            encoding="utf-8-sig"
        ),
        file_name=CSV_FILENAME,
        mime="text/csv",
        use_container_width=True
    )
