import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import re
import json
import numpy as np
import os
import hashlib
import io
from datetime import datetime

# ================================================================
# Ver.2.43 新馬・初出走判定
# ================================================================
def detect_first_start_v241(past_soup, horse_name="", race_name="", race_soup=None):
    """新馬・初出走を脚質取得より優先して判定する。"""
    try:
        blobs = []
        for x in (race_name, horse_name):
            if x:
                blobs.append(normalize_text(str(x)))

        if race_soup is not None:
            try:
                blobs.append(normalize_text(race_soup.get_text(" ", strip=True)))
                for tag in race_soup.find_all(["h1","h2","h3","div","span","p"], limit=500):
                    t = normalize_text(tag.get_text(" ", strip=True))
                    if t:
                        blobs.append(t)
            except Exception:
                pass

        blob = " ".join(blobs)
        if re.search(r"(?:新馬戦?|メイクデビュー)", blob, re.I):
            return True

        target = normalize_text(str(horse_name or ""))
        if past_soup is not None and target:
            for row in past_soup.find_all("tr"):
                txt = normalize_text(row.get_text(" ", strip=True))
                if target in txt and re.search(
                    r"初出走|未出走|出走経験なし|戦績なし|出走歴なし",
                    txt
                ):
                    return True
    except Exception:
        pass
    return False

def extract_style_from_all_attributes_v239(node) -> str:
    """HTML属性・class・style・script文字列から脚質記号を抽出する。"""
    if node is None:
        return "不明"

    pattern = re.compile(
        r"(?:Image|image|脚質|kyakushitsu|running[_-]?style)?"
        r"[^逃先差追]{0,25}(逃|先|差|追)"
        r"(?:中\d+週|中\d+ヶ月|初出走)?",
        re.I
    )

    def scan(value):
        if value is None:
            return "不明"
        s = str(value)
        # HTMLエンティティ等を含む場合も、そのまま正規化して検索
        s2 = normalize_text(s)
        for text in (s, s2):
            m = pattern.search(text)
            if m:
                return m.group(1)
            m = re.search(r"(?:逃げ|先行|差し|追込|追い込み)", text)
            if m:
                return {
                    "逃げ": "逃",
                    "先行": "先",
                    "差し": "差",
                    "追込": "追",
                    "追い込み": "追",
                }[m.group(0)]
        return "不明"

    try:
        for tag in [node] + list(node.find_all(True)):
            for key, value in tag.attrs.items():
                style = scan(value)
                if style != "不明":
                    return style
            style = scan(" ".join(tag.get("class", [])))
            if style != "不明":
                return style
            style = scan(tag.get_text(" ", strip=True))
            if style != "不明":
                return style
    except Exception:
        pass

    return "不明"


def extract_style_from_current_race_row_v239(row) -> str:
    """現在の出馬表の対象馬行だけから脚質を総当たり抽出。"""
    if row is None:
        return "不明"

    style = extract_style_from_all_attributes_v239(row)
    if style != "不明":
        return style

    # 行内のscript/json、SVG、画像URL、data属性も確認
    try:
        raw = str(row)
        for chunk in (raw, normalize_text(raw)):
            m = re.search(r"(?:逃|先|差|追)(?:中\d+週|中\d+ヶ月|初出走)?", chunk)
            if m:
                return m.group(0)[0]
    except Exception:
        pass

    return "不明"


# ================================================================
# Ver.2.43 脚質最終フォールバック
# ================================================================
def _final_kyakushitsu_fallback(row, horse_name="", horse_url=""):
    """
    既存の脚質取得結果を最優先し、それでも未取得の馬だけを対象に
    行HTML・馬名リンク・画像属性・data属性・周辺テキストを調べる。
    馬番順に処理することで特定の馬番だけ落ちる問題を避ける。
    戻り値: (脚質, 取得元)
    """
    try:
        current = row.get_text(" ", strip=True) if row is not None else ""

        # 既に明確な脚質があるなら変更しない
        known = ("逃げ", "先行", "差し", "追込", "追い込み", "自在")
        for k in known:
            if k in current:
                return k, "出馬表"

        # 行全体の属性を探索
        nodes = []
        if row is not None:
            nodes.append(row)
            nodes.extend(row.find_all(["img", "span", "div", "a", "td"]))

        texts = []
        for node in nodes:
            try:
                texts.append(node.get_text(" ", strip=True))
                for key in ("alt", "title", "aria-label", "data-style",
                            "data-kyakushitsu", "data-running-style"):
                    val = node.get(key)
                    if val:
                        texts.append(str(val))
            except Exception:
                pass

        blob = " ".join(texts)

        mapping = [
            ("追い込み", "追込"),
            ("追込", "追込"),
            ("差し", "差し"),
            ("先行", "先行"),
            ("逃げ", "逃げ"),
            ("自在", "自在"),
        ]
        for needle, value in mapping:
            if needle in blob:
                return value, "出馬表"

        # 馬名リンクがある場合、そのhrefを取得元候補として残す。
        # ここでは外部アクセスを勝手に行わず、既存の過去走取得処理を
        # 優先するため、未取得なら未取得のまま返す。
        return "不明", "未取得"

    except Exception:
        return "不明", "未取得"



# ============================================================
# 0. アプリ基本設定
# ============================================================

st.set_page_config(
    page_title="JRA AI予想 & 成績検証エンジン",
    page_icon="🏇",
    layout="wide",
)

VERSION = "Ver.2.48"
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
BET_CSV_FILENAME = "JRA_Bet_History.csv"

CSV_COLUMNS = [
    "予測ログID", "レースID", "レース名", "開催日", "予想日時",
    "データ取得日時", "コース", "距離", "馬場状態", "出走頭数",
    "勝負度", "軸馬", "相手馬", "軸馬オッズ", "バイアス履歴",
    "モデルバージョン", "確定フラグ", "回収額", "収支", "メモ",
    "投資額", "オッズ状態", "オッズ取得率",
]

BET_CSV_COLUMNS = [
    "買い目保存ID", "予測ログID", "レースID", "レース名", "開催日",
    "競馬場", "レース番号", "モデルバージョン", "保存日時",
    "◎軸", "○本線", "▲本線", "☆特注穴馬", "特注穴馬ポイント",
    "特注穴馬根拠", "⚠危険な人気馬", "危険人気馬モデル評価シェア",
    "危険人気馬判定根拠", "単勝買い目", "馬連買い目", "ワイド買い目",
    "馬単買い目", "三連複買い目", "三連単買い目",
    "コピペ用買い目", "買い目総額", "買い目保存フラグ",
    "結果", "回収額", "収支", "メモ",
]


# ============================================================
# 1. セッション状態
# ============================================================

DEFAULT_SESSION_VALUES = {
    "authenticated": False,
    "fetched_info": None,
    "latest_prediction": None,
    "latest_bet": None,
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

def _force_text_columns(df: pd.DataFrame, columns) -> pd.DataFrame:
    """CSV再読込時にメモ等へ文字列を安全に代入できる型へ統一する。"""
    df = df.copy()
    for col in columns:
        if col in df.columns:
            # object型にしてから欠損を空文字へ。pandas 2.x の LossySetitemError を防止。
            df[col] = df[col].astype("object")
            df[col] = df[col].where(df[col].notna(), "")
    return df


def sanitize_df_types(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    numeric_cols = [
        "出走頭数", "軸馬オッズ", "回収額",
        "収支", "投資額", "オッズ取得率",
    ]
    text_cols = [
        "予測ログID", "レースID", "レース名", "開催日", "予想日時",
        "データ取得日時", "コース", "距離", "馬場状態", "勝負度",
        "軸馬", "相手馬", "バイアス履歴", "モデルバージョン",
        "確定フラグ", "メモ", "オッズ状態",
    ]

    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    return _force_text_columns(df, text_cols)


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
            df = pd.read_csv(CSV_FILENAME, encoding="utf-8-sig", low_memory=False)
            df = normalize_history_columns(df)
            st.session_state["history_df"] = df
            return df
        except Exception as e:
            st.warning(f"履歴CSVの読み込みに失敗しました。新しい履歴として扱います: {e}")

    if "history_df" not in st.session_state:
        st.session_state["history_df"] = pd.DataFrame(columns=CSV_COLUMNS)
    return st.session_state["history_df"]


def _excel_safe_csv_df(df: pd.DataFrame) -> pd.DataFrame:
    """Excelで直接開いたときの文字コード・長いIDの自動変換を防ぐための出力用整形。

    アプリ内部のDataFrameは変更せず、CSV出力時だけ識別子をExcelの文字列
    として扱わせる。これにより 202605040101... のような長いIDが
    2.03E+11 等の指数表記になったり末尾が丸められたりするのを防ぐ。
    """
    out = df.copy()
    id_cols = [
        "予測ログID", "レースID", "買い目保存ID",
    ]
    for col in id_cols:
        if col in out.columns:
            out[col] = out[col].fillna("").astype(str).map(
                lambda x: f'=\"{x.replace(chr(34), chr(34) * 2)}\"' if x else ""
            )
    return out


def _csv_bytes_for_download(df: pd.DataFrame) -> bytes:
    """Excel向けUTF-8 BOM付きCSVをbytesで生成する。

    to_csv() の戻り値はstrなので、encoding='utf-8-sig'を指定するだけでは
    BOMが付かない。ここで明示的にUTF-8-SIGへエンコードする。
    """
    text = _excel_safe_csv_df(df).to_csv(index=False, lineterminator="\r\n")
    return text.encode("utf-8-sig")


def _write_csv_atomic(df: pd.DataFrame, filename: str):
    """CSVを一時ファイル経由で保存し、途中失敗で既存ファイルを壊さない。"""
    tmp = filename + ".tmp"
    # ローカル保存はアプリが再読込する生データ。UTF-8 BOMのみ付け、IDは生文字列で保持する。
    df.to_csv(tmp, index=False, encoding="utf-8-sig", lineterminator="\r\n")
    os.replace(tmp, filename)


def save_history_df(df: pd.DataFrame):
    df = normalize_history_columns(df)
    st.session_state["history_df"] = df
    try:
        _write_csv_atomic(df, CSV_FILENAME)
        sync_csv_to_google_drive(CSV_FILENAME)
        return True
    except Exception as e:
        st.error(f"ファイル保存エラー: {str(e)}")
        return False


def normalize_bet_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in BET_CSV_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    df = df[BET_CSV_COLUMNS]

    numeric_cols = [
        "特注穴馬ポイント", "危険人気馬モデル評価シェア",
        "買い目総額", "回収額", "収支",
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    text_cols = [c for c in BET_CSV_COLUMNS if c not in numeric_cols]
    return _force_text_columns(df, text_cols)


def load_bet_history_df():
    if os.path.exists(BET_CSV_FILENAME):
        try:
            df = pd.read_csv(BET_CSV_FILENAME, encoding="utf-8-sig", low_memory=False)
            df = normalize_bet_columns(df)
            st.session_state["bet_history_df"] = df
            return df
        except Exception as e:
            st.warning(f"買い目履歴CSVの読み込みに失敗しました: {e}")

    if "bet_history_df" not in st.session_state:
        st.session_state["bet_history_df"] = pd.DataFrame(columns=BET_CSV_COLUMNS)
    return st.session_state["bet_history_df"]


def _sync_result_to_related_bet(prediction_log_id, result_value, return_amount, profit, memo):
    """予測履歴の結果確定時に、同じ予測ログの買い目履歴も同期する。"""
    try:
        bet_df = load_bet_history_df()
        if bet_df.empty:
            return False

        mask = bet_df["予測ログID"].astype(str) == str(prediction_log_id)
        if not mask.any():
            return False

        bet_df = normalize_bet_columns(bet_df)
        for idx in bet_df.index[mask]:
            bet_df.loc[idx, "結果"] = str(result_value)
            bet_df.loc[idx, "回収額"] = float(return_amount)
            bet_df.loc[idx, "収支"] = float(profit)
            bet_df.loc[idx, "メモ"] = str(memo or "")

        return save_bet_history_df(bet_df)
    except Exception as e:
        st.warning(f"買い目履歴への結果反映に失敗しました（予測履歴は保存済み）: {e}")
        return False


def _sync_result_to_related_prediction(bet_row, result_value, return_amount, profit, memo):
    """買い目履歴の結果確定時に、同じ予測ログの予測履歴も同期する。"""
    try:
        prediction_log_id = str(bet_row.get("予測ログID", ""))
        if not prediction_log_id:
            return False

        history_df = load_history_df()
        if history_df.empty:
            return False

        mask = history_df["予測ログID"].astype(str) == prediction_log_id
        if not mask.any():
            return False

        history_df = normalize_history_columns(history_df)
        for idx in history_df.index[mask]:
            history_df.loc[idx, "確定フラグ"] = "確定"
            history_df.loc[idx, "回収額"] = float(return_amount)
            history_df.loc[idx, "収支"] = float(profit)
            history_df.loc[idx, "メモ"] = str(memo or "")

        return save_history_df(history_df)
    except Exception as e:
        st.warning(f"予測履歴への結果反映に失敗しました（買い目履歴は保存済み）: {e}")
        return False


def save_bet_history_df(df: pd.DataFrame):
    df = normalize_bet_columns(df)
    st.session_state["bet_history_df"] = df
    try:
        _write_csv_atomic(df, BET_CSV_FILENAME)
        sync_csv_to_google_drive(BET_CSV_FILENAME)
        return True
    except Exception as e:
        st.error(f"買い目履歴保存エラー: {str(e)}")
        return False


def _get_google_drive_secret(name: str, default=""):
    """Streamlit secrets からDrive Web App設定を安全に取得する。"""
    try:
        value = st.secrets.get(name, default)
        if value is None:
            return default
        return value
    except Exception:
        return default


def _google_drive_configured():
    """サービスアカウント鍵を使わず、Google Apps Script Web App経由でDriveへ同期する。"""
    try:
        url = str(_get_google_drive_secret("GOOGLE_DRIVE_WEBAPP_URL", "")).strip()
        token = str(_get_google_drive_secret("GOOGLE_DRIVE_WEBAPP_TOKEN", "")).strip()
        return bool(url and token)
    except Exception:
        return False


def _set_drive_sync_status(filename: str, ok: bool, message: str = ""):
    status = st.session_state.setdefault("drive_sync_status", {})
    status[filename] = {
        "ok": bool(ok),
        "message": str(message or ""),
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def sync_csv_to_google_drive(filename: str):
    """ローカルCSVをGoogle Apps Script Web App経由でGoogle Driveへ同期する。

    サービスアカウントJSONやGoogle Cloudの鍵は使用しない。
    Web App側がユーザーのGoogle Drive権限で同名CSVを更新/新規作成する。
    """
    if not _google_drive_configured():
        _set_drive_sync_status(
            filename,
            False,
            "Google Drive Web App設定がありません",
        )
        return False

    try:
        webapp_url = str(
            _get_google_drive_secret("GOOGLE_DRIVE_WEBAPP_URL", "")
        ).strip()
        token = str(
            _get_google_drive_secret("GOOGLE_DRIVE_WEBAPP_TOKEN", "")
        ).strip()

        with open(filename, "rb") as fh:
            content_b64 = __import__("base64").b64encode(fh.read()).decode("ascii")

        payload = {
            "token": token,
            "filename": str(filename),
            "content_base64": content_b64,
        }

        response = requests.post(
            webapp_url,
            json=payload,
            timeout=30,
        )
        response.raise_for_status()

        try:
            result = response.json()
        except Exception:
            result = {}

        if not result.get("ok"):
            message = str(
                result.get("message")
                or result.get("error")
                or f"Web Appから正常応答を取得できませんでした（HTTP {response.status_code}）"
            )
            raise RuntimeError(message)

        msg = str(result.get("message") or f"Google Driveへ同期しました: {filename}")
        _set_drive_sync_status(filename, True, msg)
        return True

    except Exception as e:
        msg = f"Google Drive同期エラー: {type(e).__name__}: {e}"
        _set_drive_sync_status(filename, False, msg)
        # Drive側の失敗でローカルCSVまで消さない。
        st.warning(f"⚠️ {msg}（ローカルCSVは保存済み）")
        return False


load_history_df()
load_bet_history_df()


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


def extract_waku_uma_fallback(row):
    """class名が変わった出馬表行から枠番・馬番を補完する。"""
    nums = []
    for td in row.find_all("td"):
        txt = normalize_text(td.get_text(" ", strip=True))
        if re.fullmatch(r"\d{1,2}", txt):
            n = int(txt)
            if 1 <= n <= 18:
                nums.append(n)
    if len(nums) >= 2:
        return nums[0], nums[1]
    return 0, 0


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
    # 1) 調教師専用class
    elem = find_first_by_class(
        row,
        r"Trainer|TrainerName|Chokyo|Tochaku",
    )
    if elem:
        text = normalize_text(elem.get_text(" ", strip=True))
        text = re.sub(r"^(美浦|栗東)[：:]?", "", text)
        if text:
            return text

    # 2) trainer DBへのリンク
    for link in row.select("a[href*='/trainer/'], a[href*='/trainerresult/']"):
        text = normalize_text(link.get_text(" ", strip=True))
        if text:
            return text

    # 3) 美浦/栗東を含むセルから名前部分を抽出
    for td in row.find_all("td"):
        text = normalize_text(td.get_text(" ", strip=True))
        if re.search(r"(美浦|栗東)", text):
            cleaned = re.sub(r"(美浦|栗東)", "", text).strip(" /・,，")
            cleaned = re.sub(r"^[：:]", "", cleaned).strip()
            if cleaned and len(cleaned) <= 30:
                return cleaned

    return ""


def normalize_person_name(value) -> str:
    text = normalize_text(value)
    return re.sub(r"[()（）【】\[\] ]", "", text)


def extract_last_jockey_from_past_page(soup, horse_number: int, horse_name: str) -> str:
    row = find_past_row(soup, horse_number, horse_name)
    if row is None:
        return ""
    jockey = extract_jockey(row)
    if jockey:
        return jockey
    for link in row.select("a[href*='/jockey/']"):
        text = normalize_text(link.get_text(" ", strip=True))
        if text:
            return text
    return ""


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



def extract_style_by_exact_horse_name_v240(soup, horse_name: str) -> str:
    """
    過去走ページで対象馬名を含む行を直接探し、
    「Image先中15週」のような脚質表記を抽出する。
    馬番検索に依存しないため、馬番13のような取りこぼしを防ぐ。
    """
    if soup is None or not horse_name:
        return "不明"

    target = normalize_text(horse_name)
    if not target:
        return "不明"

    # まず馬名リンク、次にテキストノード、最後にtr全体。
    candidate_rows = []

    for a in soup.find_all("a", href=True):
        txt = normalize_text(a.get_text(" ", strip=True))
        if txt == target or target in txt or txt in target:
            row = a.find_parent("tr")
            if row is not None:
                candidate_rows.append(row)

    for row in soup.find_all("tr"):
        txt = normalize_text(row.get_text(" ", strip=True))
        if target in txt:
            candidate_rows.append(row)

    seen = set()
    for row in candidate_rows:
        if id(row) in seen:
            continue
        seen.add(id(row))

        values = [row.get_text(" ", strip=True), str(row)]

        for tag in row.find_all(True):
            values.append(tag.get_text(" ", strip=True))
            for attr in ("alt", "title", "aria-label"):
                val = tag.get(attr)
                if val:
                    values.append(str(val))

            for attr, val in tag.attrs.items():
                if (
                    "style" in str(attr).lower()
                    or "kyaku" in str(attr).lower()
                    or "running" in str(attr).lower()
                    or str(attr).lower().startswith("data-")
                ):
                    values.append(str(val))

        for value in values:
            value = normalize_text(value)
            if not value:
                continue

            # netkeibaの代表表記
            m = re.search(
                r"Image(逃|先|差|追)(?:中\d+週|中\d+ヶ月|初出走)",
                value
            )
            if m:
                return m.group(1)

            # Imageが消えている場合
            m = re.search(
                r"(逃|先|差|追)中\d+(?:週|ヶ月)",
                value
            )
            if m:
                return m.group(1)

            m = re.search(r"(逃|先|差|追)初出走", value)
            if m:
                return m.group(1)

            # 通過順位しかない場合も補完
            m = re.search(
                r"(?<!\d)(\d{1,2})[-－](\d{1,2})[-－]"
                r"(\d{1,2})[-－](\d{1,2})(?!\d)",
                value
            )
            if m:
                pos = int(m.group(4))
                if pos <= 2:
                    return "逃"
                if pos <= 5:
                    return "先"
                if pos <= 10:
                    return "差"
                return "追"

    return "不明"


def extract_style_from_text(text: str) -> str:
    """
    netkeibaの脚質表記を抽出する。

    現行の出馬表では「Image先中13週」のように、
    脚質が画面上のテキストではなく img の alt/title 等の属性に
    入っているケースがあるため、呼び出し側で属性値も渡せるようにする。
    """
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


def extract_style_from_row(row) -> str:
    """
    現在の出馬表1行から脚質を最優先で取得する。

    netkeibaのHTMLでは「Image先中2週」等が img の alt、title、
    aria-label、data-* 属性に入る場合があるため、row.get_text()だけに
    依存しない。
    """
    if row is None:
        return "不明"

    # 1) 脚質専用classを最優先
    elem = find_first_by_class(
        row,
        r"Kyakushitsu|RunningStyle|Style",
    )
    if elem:
        candidates = [
            elem.get_text(" ", strip=True),
            elem.get("alt", ""),
            elem.get("title", ""),
            elem.get("aria-label", ""),
        ]
        for value in candidates:
            style = extract_style_from_text(value)
            if style != "不明":
                return style

    # 2) row配下の全属性を探索。特にimg alt/titleを重視
    for tag in row.find_all(True):
        candidates = [
            tag.get_text(" ", strip=True),
            tag.get("alt", ""),
            tag.get("title", ""),
            tag.get("aria-label", ""),
        ]

        for attr, value in tag.attrs.items():
            attr_name = str(attr).lower()
            if (
                attr_name.startswith("data-")
                or attr_name in {"alt", "title", "aria-label", "data-original-title"}
            ):
                if isinstance(value, (list, tuple)):
                    candidates.extend(str(v) for v in value)
                else:
                    candidates.append(str(value))

        for value in candidates:
            style = extract_style_from_text(value)
            if style != "不明":
                return style

    # 3) 最後に行全体の表示文字列
    return extract_style_from_text(
        row.get_text(" ", strip=True)
    )


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
    """過去走ページから対象馬の行を馬番・馬名・リンク情報で強力に特定する。"""
    if soup is None:
        return None

    rows = soup.find_all("tr")
    target = normalize_text(horse_name)

    # 1. 専用の馬番セル
    for row in rows:
        num = extract_number_by_class(row, r"Umaban")
        if num == horse_number:
            return row

    # 2. 馬名リンクを最優先。
    # netkeibaでは同一馬名が複数箇所に出ることがあるため、
    # /horse/ を含むリンクを優先する。
    if target:
        for a in soup.find_all("a", href=True):
            href = str(a.get("href", ""))
            txt = normalize_text(a.get_text(" ", strip=True))
            if "/horse/" in href and txt and target == txt:
                row = a.find_parent("tr")
                if row is not None:
                    return row

    # 3. 馬名一致 -> 最も近いtr
    if target:
        for node in soup.find_all(string=re.compile(re.escape(target), re.I)):
            parent = node.parent
            for _ in range(8):
                if parent is None:
                    break
                if getattr(parent, "name", None) == "tr":
                    txt = normalize_text(parent.get_text(" ", strip=True))
                    if target in txt:
                        return parent
                parent = parent.parent

    # 4. row単位の馬名一致
    if target:
        for row in rows:
            row_text = normalize_text(row.get_text(" ", strip=True))
            if target in row_text and len(row_text) < 8000:
                return row

    # 5. 馬番 + 馬名の組合せ
    for row in rows:
        row_text = normalize_text(row.get_text(" ", strip=True))
        num_hit = bool(
            re.search(
                rf"(?<!\d){re.escape(str(horse_number))}(?!\d)",
                row_text
            )
        )
        name_hit = bool(target and target in row_text)
        if num_hit and name_hit:
            return row

    # 6. 最後のフォールバック：馬番だけ。ただしセルの境界を厳密に確認
    for row in rows:
        for td in row.find_all(["td", "th"]):
            txt = normalize_text(td.get_text(" ", strip=True))
            if txt == str(horse_number):
                return row

    return None


def extract_style_from_past_html_robust(
    html: str,
    horse_number: int,
    horse_name: str,
) -> str:
    """
    過去走ページから脚質を強制的に拾う最終フォールバック。

    現行netkeibaでは脚質が画面テキストではなく、imgのalt/titleや
    「Image先中13週」のような属性文字列に入るケースがあるため、
    BeautifulSoupのget_text()だけに依存しない。
    馬名・馬番の周辺HTMLを調べ、属性を含む生HTMLから抽出する。
    """
    if not html:
        return "不明"

    raw = str(html)
    soup = BeautifulSoup(raw, "html.parser")
    target_name = normalize_text(horse_name)

    style_pattern = re.compile(
        r"(?:Image)?(逃|先|差|追)(?:中\d+週|中\d+ヶ月|初出走|\b)",
        re.I,
    )

    def search_chunk(chunk: str) -> str:
        chunk = normalize_text(chunk)
        if not chunk:
            return "不明"
        m = style_pattern.search(chunk)
        if m:
            return m.group(1)
        return extract_style_from_text(chunk)

    # 1) 馬名を含む最小要素（tr -> div -> td 等）を優先。
    name_candidates = []
    if target_name:
        for tag in soup.find_all(string=re.compile(re.escape(target_name), re.I)):
            parent = tag.parent
            for _ in range(6):
                if parent is None:
                    break
                txt = normalize_text(parent.get_text(" ", strip=True))
                if target_name in txt and len(txt) <= 5000:
                    name_candidates.append(parent)
                parent = parent.parent

    for node in name_candidates:
        # ノード自身 + 祖先の属性を含めて調べる。
        html_text = str(node)
        style = search_chunk(html_text)
        if style != "不明":
            return style
        for tag in node.find_all(True):
            values = [tag.get("alt", ""), tag.get("title", ""), tag.get("aria-label", "")]
            for attr, value in tag.attrs.items():
                if str(attr).lower().startswith("data-"):
                    values.append(str(value))
            for value in values:
                style = search_chunk(str(value))
                if style != "不明":
                    return style

    # 2) 馬番に一致する行。class名が変わってもテキスト/属性を総当たり。
    rows = soup.find_all("tr")
    for row in rows:
        text = normalize_text(row.get_text(" ", strip=True))
        num_hit = bool(re.search(rf"(?:^|\s){re.escape(str(horse_number))}(?:\s|$)", text))
        name_hit = bool(target_name and target_name in text)
        if not (num_hit or name_hit):
            continue

        style = search_chunk(str(row))
        if style != "不明":
            return style

        for tag in row.find_all(True):
            values = [tag.get("alt", ""), tag.get("title", ""), tag.get("aria-label", "")]
            for attr, value in tag.attrs.items():
                if str(attr).lower().startswith("data-"):
                    values.append(str(value))
            for value in values:
                style = search_chunk(str(value))
                if style != "不明":
                    return style

    # 3) 生HTML上で馬名/馬番の前後を直接検索。
    anchors = []
    if target_name:
        anchors.extend(m.start() for m in re.finditer(re.escape(target_name), normalize_text(raw), re.I))
    anchors.extend(m.start() for m in re.finditer(rf"(?:^|\s){re.escape(str(horse_number))}(?:\s|$)", normalize_text(raw)))

    normalized_raw = normalize_text(raw)
    for pos in anchors:
        chunk = normalized_raw[max(0, pos - 1000): pos + 4000]
        style = search_chunk(chunk)
        if style != "不明":
            return style

    return "不明"


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

    # Ver.2.43: 対象rowの生HTML・属性を最優先で調査。
    # 「Image先中13週」等がget_text()に出ない場合にも対応。
    raw_row_v237 = str(row)
    for pat in (
        r"(?:Image)?(逃|先|差|追)(?:中\d+週|中\d+ヶ月|初出走)?",
        r"(?:脚質|脚質名)[^逃先差追]{0,30}(逃|先|差|追)",
    ):
        mm = re.search(pat, raw_row_v237, re.I)
        if mm:
            return mm.group(1)

    for tag in row.find_all(True):
        vals = [
            tag.get("alt", ""),
            tag.get("title", ""),
            tag.get("aria-label", ""),
        ]
        vals += [
            str(v)
            for a, v in tag.attrs.items()
            if (
                "style" in str(a).lower()
                or "kyaku" in str(a).lower()
                or "running" in str(a).lower()
            )
        ]
        for val in vals:
            sval = str(val)
            mm = re.search(
                r"(?:Image)?(逃|先|差|追)(?:中\d+週|中\d+ヶ月|初出走)?",
                sval,
                re.I
            )
            if mm:
                return mm.group(1)

    # 属性値に「Image先中13週」等が入っているケースを先に確認。
    raw_row = str(row)
    raw_style = extract_style_from_text(raw_row)
    if raw_style != "不明":
        return raw_style

    for tag in row.find_all(True):
        values = [tag.get("alt", ""), tag.get("title", ""), tag.get("aria-label", "")]
        for attr, value in tag.attrs.items():
            if str(attr).lower().startswith("data-"):
                values.append(str(value))
        for value in values:
            raw_style = extract_style_from_text(str(value))
            if raw_style != "不明":
                return raw_style

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



def extract_style_from_horse_context(html: str, horse_number: int, horse_name: str) -> str:
    """対象馬の周辺HTMLから脚質記号を拾う最終強化フォールバック。"""
    if not html:
        return "不明"
    raw = normalize_text(str(html))
    target = normalize_text(horse_name)
    patterns = [
        r"(?:Image)?(逃|先|差|追)(?:中\d+週|中\d+ヶ月|初出走)?",
        r"(?:脚質|脚質名)[^逃先差追]{0,20}(逃|先|差|追)",
        r"(?:runningstyle|running_style|kyakushitsu)[^逃先差追]{0,50}(逃|先|差|追)",
    ]
    def scan(chunk):
        for pat in patterns:
            m = re.search(pat, chunk, re.I)
            if m:
                return m.group(1)
        return "不明"

    soup = BeautifulSoup(str(html), "html.parser")
    nodes=[]
    if target:
        for txt in soup.find_all(string=re.compile(re.escape(target), re.I)):
            parent=txt.parent
            for _ in range(8):
                if parent is None: break
                nodes.append(parent)
                parent=parent.parent
    for node in nodes:
        chunk=str(node)
        style=scan(normalize_text(chunk))
        if style!="不明": return style
        for tag in node.find_all(True):
            vals=[tag.get("alt",""),tag.get("title",""),tag.get("aria-label","")]
            vals += [str(v) for a,v in tag.attrs.items() if "style" in str(a).lower() or "kyaku" in str(a).lower() or "running" in str(a).lower()]
            for val in vals:
                style=scan(normalize_text(str(val)))
                if style!="不明": return style
    # 馬番周辺の生HTML
    norm=normalize_text(str(html))
    for needle in [str(horse_number), target]:
        if not needle: continue
        for m in re.finditer(re.escape(needle), norm, re.I):
            chunk=norm[max(0,m.start()-1500):m.end()+5000]
            style=scan(chunk)
            if style!="不明": return style
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
# 6. JRA公式リーディング
# ============================================================

def parse_jra_leading_tables(html: str):
    soup = BeautifulSoup(html, "html.parser")
    result = {"jockey": {}, "trainer": {}, "year": None, "source": ""}
    page_text = normalize_text(soup.get_text(" ", strip=True))

    year_match = re.search(r"(20\d{2})年", page_text)
    if year_match:
        result["year"] = int(year_match.group(1))

    for table in soup.find_all("table"):
        table_text = normalize_text(table.get_text(" ", strip=True))
        is_jockey = ("騎手" in table_text or "ジョッキー" in table_text)
        is_trainer = ("調教師" in table_text or "トレーナー" in table_text)
        if not (is_jockey or is_trainer):
            continue

        for row in table.find_all("tr"):
            cells = [
                normalize_text(c.get_text(" ", strip=True))
                for c in row.find_all(["th","td"])
            ]
            if len(cells) < 2:
                continue
            m = re.match(r"^(\d{1,3})", cells[0])
            if not m:
                continue
            rank = int(m.group(1))
            if not 1 <= rank <= 100:
                continue

            name = ""
            for cell in cells[1:6]:
                candidate = re.sub(r"[\d,.\-%]+", "", cell).strip()
                if 2 <= len(candidate) <= 20 and not re.search(
                    r"(勝|着|率|賞金|回|騎乗|出走|獲得)",
                    candidate,
                ):
                    name = normalize_person_name(candidate)
                    break
            if not name:
                continue

            if is_jockey and not is_trainer:
                result["jockey"][name] = rank
            elif is_trainer and not is_jockey:
                result["trainer"][name] = rank

    return result


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_jra_official_leading(year: int):
    urls = [
        f"https://www.jra.go.jp/datafile/leading/j{year}.html",
        f"https://www.jra.go.jp/datafile/leading/{year}.html",
        "https://www.jra.go.jp/datafile/leading/",
    ]
    last_error = ""
    for url in urls:
        try:
            r = requests.get(url, headers=REQUEST_HEADERS, timeout=20)
            r.raise_for_status()
            r.encoding = r.apparent_encoding or r.encoding or "utf-8"
            parsed = parse_jra_leading_tables(r.text)
            if parsed["jockey"] or parsed["trainer"]:
                parsed["year"] = year
                parsed["source"] = url
                return parsed, None
            last_error = "JRA公式ページの表を検出できませんでした。"
        except Exception as e:
            last_error = str(e)
    return {"jockey": {}, "trainer": {}, "year": year, "source": ""}, last_error


def leading_rank_score(rank):
    if not rank:
        return 0.0
    rank = int(rank)
    if rank == 1:
        return np.log(1.12)
    if rank <= 3:
        return np.log(1.09)
    if rank <= 5:
        return np.log(1.07)
    if rank <= 10:
        return np.log(1.05)
    if rank <= 15:
        return np.log(1.03)
    if rank <= 20:
        return np.log(1.015)
    return 0.0


def calculate_special_logic_features(horse, leading_data, track_type, distance):
    jockey = normalize_person_name(horse.get("騎手", ""))
    trainer = normalize_person_name(horse.get("調教師", ""))
    prev_jockey = normalize_person_name(horse.get("前走騎手", ""))

    jockey_rank = leading_data.get("jockey", {}).get(jockey)
    trainer_rank = leading_data.get("trainer", {}).get(trainer)

    if prev_jockey and jockey:
        if prev_jockey == jockey:
            ride_status = "継続騎乗"
            continuity = np.log(1.035)
            change = 0.0
        else:
            ride_status = "乗り替わり"
            continuity = 0.0
            change = np.log(1.02)
    else:
        ride_status = "判定不可"
        continuity = 0.0
        change = 0.0

    jockey_context = np.log(1.01) if jockey_rank and distance != "その他" else 0.0
    trainer_context = np.log(1.01) if trainer_rank and distance != "その他" else 0.0

    return {
        "騎手順位": jockey_rank or "",
        "調教師順位": trainer_rank or "",
        "騎手評価": leading_rank_score(jockey_rank),
        "調教師評価": leading_rank_score(trainer_rank),
        "継続騎乗評価": continuity,
        "乗り替わり評価": change,
        "騎手×競馬場": jockey_context,
        "騎手×距離": jockey_context,
        "騎手×コース": jockey_context,
        "調教師×競馬場": trainer_context,
        "調教師×コース": trainer_context,
        "騎乗判定": ride_status,
    }


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


def detect_race_status(session, race_id: str, race_date=None):
    """レース終了状態を安全側で判定する。

    1) netkeibaの結果ページで着順・払戻等の結果表示を確認
    2) 結果ページで判定できない場合、開催日が今日より前なら終了扱い
    3) 同日で結果未掲載なら「未確定」とする
    """
    try:
        result_url = (
            "https://race.netkeiba.com/race/"
            f"result.html?race_id={race_id}"
        )
        response = session.get(
            result_url,
            headers={**REQUEST_HEADERS, "Referer": "https://race.netkeiba.com/"},
            timeout=10,
        )
        if response.ok:
            response.encoding = (
                response.apparent_encoding
                or response.encoding
                or "euc-jp"
            )
            result_soup = BeautifulSoup(response.text, "html.parser")
            text = normalize_text(result_soup.get_text(" ", strip=True))

            # 結果ページに実着順が掲載されている場合だけ終了とする。
            rank_nodes = result_soup.select(
                ".Result_Num, .Result_Table .Rank, .Race_Result .Result_Num"
            )
            has_rank = len(rank_nodes) >= 3
            has_result_words = (
                ("着順" in text and ("払戻" in text or "タイム" in text))
                or "レース結果" in text
            )
            if has_rank and has_result_words:
                return "終了", "結果ページで着順を確認"

            # CSS構造が変わった場合の文字ベースフォールバック。
            if "着順" in text and "払戻" in text:
                return "終了", "結果ページで着順・払戻を確認"
    except Exception:
        pass

    try:
        if race_date is not None:
            race_day = pd.Timestamp(race_date).date()
            today = pd.Timestamp.now().date()
            if race_day < today:
                return "終了", "開催日が経過"
    except Exception:
        pass

    return "未確定", "結果未掲載"


@st.cache_data(
    ttl=30,
    show_spinner=False,
)
def fetch_netkeiba_race_data_cached(race_id: str, requested_date: str = "", refresh_token: str = ""):
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

        # race_id先頭4桁を開催年として使う。
        # RaceData02に年が表示されない場合でも、サーバー時刻ではなく
        # レースIDから正しい開催日を復元できるようにする。
        try:
            race_year = int(race_id[:4])
        except Exception:
            race_year = datetime.now().year

        extracted = {
            "race_id": race_id,
            "venue": detected_venue,
            "race_num": race_num,
            "track_type": "芝",
            "distance": "その他",
            "condition": "良",
            "race_name": f"レース_{race_id}",
            "race_date": (datetime.strptime(requested_date, "%Y-%m-%d") if requested_date else datetime(race_year, 1, 1)),
            "horses": [],
            "kyaku_count": 0,
            "front_runner_count": 0,
            "odds_coverage": 0.0,
            "odds_status": "未取得（発売前等）",
            "race_status": "未確定",
            "race_status_reason": "結果未掲載",
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

            # 年あり表記を優先。
            date_match = re.search(
                r"(\d{4})年(\d{1,2})月(\d{1,2})日",
                date_text,
            )

            if date_match:
                y, m, d = map(int, date_match.groups())
                extracted["race_date"] = datetime(y, m, d)
            else:
                # JRA/netkeibaでは「10月3日(土)」のように年が省略される
                # ことがある。年をサーバー時刻から取ると日本時間との
                # 日付境界で前日になるため、race_idの年を使う。
                md_match = re.search(
                    r"(\d{1,2})月(\d{1,2})日",
                    date_text,
                )
                if md_match:
                    m, d = map(int, md_match.groups())
                    extracted["race_date"] = datetime(race_year, m, d)

        if extracted["race_date"].month == 1 and extracted["race_date"].day == 1:
            page_text = normalize_text(soup.get_text(" ", strip=True))
            for dm in [re.search(r"(20\d{2})[/-](\d{1,2})[/-](\d{1,2})", page_text), re.search(r"(20\d{2})年(\d{1,2})月(\d{1,2})日", page_text)]:
                if dm:
                    y, m, d = map(int, dm.groups())
                    extracted["race_date"] = datetime(y, m, d)
                    break

        # ----------------------------------------------------
        # レース終了状態
        # ----------------------------------------------------
        race_status, race_status_reason = detect_race_status(
            session,
            race_id,
            extracted.get("race_date"),
        )
        extracted["race_status"] = race_status
        extracted["race_status_reason"] = race_status_reason

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
            past_urls = [
                f"https://race.netkeiba.com/race/shutuba_past_9.html?race_id={race_id}",
                f"https://race.netkeiba.com/race/shutuba_past_5.html?race_id={race_id}",
                f"https://race.netkeiba.com/race/shutuba_past.html?race_id={race_id}",
            ]
            for candidate_url in past_urls:
                try:
                    past_response = session.get(
                        candidate_url,
                        headers={**REQUEST_HEADERS, "Referer": url},
                        timeout=20,
                    )
                    past_response.raise_for_status()
                    past_response.encoding = (
                        past_response.apparent_encoding
                        or past_response.encoding
                        or "euc-jp"
                    )
                    candidate_soup = BeautifulSoup(past_response.text, "html.parser")
                    candidate_text = normalize_text(candidate_soup.get_text(" ", strip=True))
                    if candidate_soup.find("tr") and re.search(r"(逃|先|差|追)中\d+週", candidate_text):
                        past_soup = candidate_soup
                        break
                    if past_soup is None and candidate_soup.find("tr"):
                        past_soup = candidate_soup
                except Exception:
                    continue
        except Exception:
            past_soup = None

        # オッズはHTMLの ---.- プレースホルダではなく、
        # netkeibaの単勝オッズJSON APIから取得する。
        api_odds_map = fetch_win_odds_api(session, race_id)

        # ----------------------------------------------------
        # 出走馬（Ver.2.33.1 統合版）
        # ----------------------------------------------------
        # 15頭取得を維持しつつ、Ver.2.30で動作していた
        # 脚質・脚質取得元・APIオッズ取得ロジックを復元する。
        horse_rows = []
        for tr in soup.find_all("tr"):
            cls = " ".join(tr.get("class", []))
            if (
                "HorseList" in cls
                or tr.find("td", class_=re.compile(r"Umaban", re.I))
                or tr.find("a", href=re.compile(r"/horse/"))
            ):
                horse_rows.append(tr)

        # 重複行除去
        unique_rows = []
        seen = set()
        for tr in horse_rows:
            key = id(tr)
            if key not in seen:
                seen.add(key)
                unique_rows.append(tr)
        horse_rows = unique_rows

        if not horse_rows:
            return None, "出走馬テーブルが見つかりませんでした。"

        horse_map = {}

        for row in horse_rows:
            try:
                waku = extract_number_by_class(row, r"Waku")
                uma = extract_number_by_class(row, r"Umaban")

                if uma <= 0:
                    fb_waku, fb_uma = extract_waku_uma_fallback(row)
                    if fb_uma > 0:
                        waku = fb_waku
                        uma = fb_uma

                if uma <= 0 or uma > 18:
                    continue

                horse_name = extract_horse_name(row)
                if not horse_name:
                    continue

                jockey = extract_jockey(row)
                trainer = extract_trainer(row)
                kinryo = extract_kinryo(row)

                # ------------------------------------------------
                # オッズ：APIを最優先
                # ------------------------------------------------
                odds = api_odds_map.get(uma)
                if odds is None:
                    odds = extract_odds_from_row(row)

                # ------------------------------------------------
                # 脚質：現在出馬表 -> 過去走 -> HTML総当たり -> 馬詳細
                # ------------------------------------------------
                style = extract_style_from_row(row)
                style_source = (
                    "出馬表・脚質欄"
                    if style != "不明" else "未取得"
                )

                if style == "不明" and past_soup is not None:
                    try:
                        past_style = extract_style_from_past_page(
                            past_soup, uma, horse_name
                        )
                    except Exception:
                        past_style = "不明"
                    if past_style != "不明":
                        style = past_style
                        style_source = "過去走"

                # Ver.2.43: 馬名一致による過去走HTML直接検索
                if style == "不明" and past_soup is not None:
                    try:
                        exact_name_style = extract_style_by_exact_horse_name_v240(
                            past_soup,
                            horse_name,
                        )
                    except Exception:
                        exact_name_style = "不明"

                    if exact_name_style != "不明":
                        style = exact_name_style
                        style_source = "過去走・馬名一致"

                if style == "不明" and past_soup is not None:
                    try:
                        robust_style = extract_style_from_past_html_robust(
                            str(past_soup), uma, horse_name
                        )
                    except Exception:
                        robust_style = "不明"
                    if robust_style != "不明":
                        style = robust_style
                        style_source = "過去走・脚質表記"

                if style == "不明" and past_soup is not None:
                    try:
                        context_style = extract_style_from_horse_context(
                            str(past_soup), uma, horse_name
                        )
                    except Exception:
                        context_style = "不明"
                    if context_style != "不明":
                        style = context_style
                        style_source = "過去走・馬別HTML"

                if style == "不明" and past_soup is not None:
                    try:
                        inferred_style = extract_style_from_any_past_horse_text(
                            past_soup, uma, horse_name
                        )
                    except Exception:
                        inferred_style = "不明"
                    if inferred_style != "不明":
                        style = inferred_style
                        style_source = "過去走・通過順位推定"

                if style == "不明" and past_soup is not None:
                    try:
                        exact_name_style = extract_style_by_exact_horse_name_v240(
                            past_soup,
                            horse_name,
                        )
                    except Exception:
                        exact_name_style = "不明"

                    if exact_name_style != "不明":
                        style = exact_name_style
                        style_source = "過去走・馬名一致"

                if style == "不明":
                    try:
                        detail_style = extract_style_from_current_race_row_v239(row)
                    except Exception:
                        detail_style = "不明"
                    if detail_style != "不明":
                        style = detail_style
                        style_source = "馬詳細・過去成績"

                # Ver.2.43: 新馬・初出走は「不明」のままにせず明示。
                # 実際の脚質を推定するのではなく、脚質特徴量を中立扱いにする。
                if style == "不明":
                    try:
                        if detect_first_start_v241(
                            past_soup,
                            horse_name,
                            extracted.get("race_name", ""),
                            race_soup=soup,
                        ):
                            style = "初出走"
                            style_source = "初出走"
                    except Exception:
                        pass

                # Ver.2.43:
                # 初出走判定を最終的に最優先する。
                # これより前に過去走から「先行」等が取得されても、
                # 初出走なら必ず「初出走」に戻す。
                try:
                    if detect_first_start_v241(
                        past_soup,
                        horse_name,
                        extracted.get("race_name", ""),
                        race_soup=soup,
                    ):
                        style = "初出走"
                        style_source = "初出走"
                except Exception:
                    pass

                style_display = {
                    "逃": "逃げ",
                    "先": "先行",
                    "差": "差し",
                    "追": "追込",
                    "初出走": "初出走",
                }.get(style, "不明")

                # 前走騎手：継続/乗り替わりロジック用
                try:
                    previous_jockey = extract_last_jockey_from_past_page(
                        past_soup, uma, horse_name
                    )
                except Exception:
                    previous_jockey = ""

                # 馬詳細URLを保持。脚質未取得馬の最終フォールバックで使用する。
                horse_url = ""
                try:
                    for _a in row.find_all("a", href=True):
                        _href = str(_a.get("href", "")).strip()
                        if "/horse/" in _href:
                            horse_url = _href
                            if _href.startswith("/"):
                                horse_url = "https://db.netkeiba.com" + _href
                            elif _href.startswith("http"):
                                horse_url = _href
                            break
                except Exception:
                    horse_url = ""

                candidate = {
                    "枠番": int(waku),
                    "馬番": int(uma),
                    "馬名": horse_name,
                    "騎手": jockey,
                    "調教師": trainer,
                    "前走騎手": previous_jockey,
                    "斤量": kinryo,
                    "脚質": style,
                    "脚質表示": style_display,
                    "脚質取得元": style_source,
                    "オッズ": odds,
                    "馬URL": horse_url,
                }

                # 情報量の多い行を優先
                quality = 0
                if candidate["騎手"]:
                    quality += 3
                if candidate["調教師"]:
                    quality += 2
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
            horses.append(item)

        # Ver.2.43: 脚質が取れていない馬だけ最終補完
        for _horse in horses:
            _style = str(_horse.get("脚質", "") or "").strip()
            _source = str(_horse.get("脚質取得元", "") or "").strip()

            if _style in ("", "不明", "None", "nan") or _source in ("", "未取得"):
                try:
                    if detect_first_start_v241(
                        None,
                        _horse.get("馬名", ""),
                        extracted.get("race_name", ""),
                        race_soup=soup,
                    ):
                        _horse["脚質"] = "初出走"
                        _horse["脚質表示"] = "初出走"
                        _horse["脚質取得元"] = "初出走"
                        continue
                except Exception:
                    pass
                try:
                    _style2, _source2 = _final_kyakushitsu_fallback(
                        None,
                        _horse.get("馬名", ""),
                        _horse.get("馬URL", _horse.get("url", ""))
                    )
                    if _style2 != "不明":
                        _horse["脚質"] = _style2
                        _horse["脚質取得元"] = _source2
                except Exception:
                    pass


        if not horses:
            return None, "出走馬データの抽出件数が0件です。"

        # Ver.2.43: 脚質が1頭だけ落ちるケースへの個別馬ページ補完
        # 既存の脚質取得を優先し、未取得馬だけ実行する。
        for _horse in horses:
            _style = str(_horse.get("脚質", "") or "").strip()
            if _style not in ("", "不明", "None", "nan"):
                continue

            _url = str(_horse.get("馬URL", "") or "").strip()
            if not _url:
                continue

            try:
                _horse_soup = None
                _resp = session.get(
                    _url,
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/120.0 Safari/537.36"
                        )
                    },
                    timeout=12,
                )
                _resp.raise_for_status()
                _resp.encoding = _resp.apparent_encoding or "euc-jp"
                _horse_soup = BeautifulSoup(_resp.text, "html.parser")

                _style2 = extract_style_from_any_past_horse_text(
                    _horse_soup,
                    int(_horse.get("馬番", 0) or 0),
                    str(_horse.get("馬名", "")),
                )

                if _style2 == "不明":
                    _style2 = extract_style_from_past_page(
                        _horse_soup,
                        int(_horse.get("馬番", 0) or 0),
                        str(_horse.get("馬名", "")),
                    )

                if _style2 != "不明":
                    _horse["脚質"] = _style2
                    _horse["脚質表示"] = {
                        "逃": "逃げ",
                        "先": "先行",
                        "差": "差し",
                        "追": "追込",
                    }.get(_style2, _style2)
                    _horse["脚質取得元"] = "馬詳細・過去走"
            except Exception:
                pass

        # Ver.2.43: 現在の出馬表rowから脚質を最終取得。
        # 過去走側の馬番検索に失敗しても、出馬表側に脚質情報があれば復元する。
        for _horse in horses:
            _style = str(_horse.get("脚質", "") or "").strip()
            if _style not in ("", "不明", "None", "nan"):
                continue

            _num = int(_horse.get("馬番", 0) or 0)
            _name = str(_horse.get("馬名", "") or "").strip()

            try:
                _race_rows = soup.find_all("tr")
                _found_row = None

                # 馬名リンク一致を最優先
                if _name:
                    for _a in soup.find_all("a", href=True):
                        _txt = normalize_text(_a.get_text(" ", strip=True))
                        if _txt == normalize_text(_name):
                            _found_row = _a.find_parent("tr")
                            if _found_row is not None:
                                break

                # 馬番セル一致
                if _found_row is None:
                    for _r in _race_rows:
                        for _td in _r.find_all(["td", "th"]):
                            if normalize_text(_td.get_text(" ", strip=True)) == str(_num):
                                _found_row = _r
                                break
                        if _found_row is not None:
                            break

                # 馬番＋馬名の行一致
                if _found_row is None:
                    for _r in _race_rows:
                        _rt = normalize_text(_r.get_text(" ", strip=True))
                        if str(_num) in _rt and _name and normalize_text(_name) in _rt:
                            _found_row = _r
                            break

                _style2 = extract_style_from_current_race_row_v239(_found_row)
                if _style2 != "不明":
                    _horse["脚質"] = _style2
                    _horse["脚質表示"] = {
                        "逃": "逃げ",
                        "先": "先行",
                        "差": "差し",
                        "追": "追込",
                    }.get(_style2, _style2)
                    _horse["脚質取得元"] = "出馬表・最終補完"
            except Exception:
                pass

        # Ver.2.43: 全馬について初出走判定を最後に再適用。
        for _horse in horses:
            try:
                if detect_first_start_v241(
                    None,
                    _horse.get("馬名", ""),
                    extracted.get("race_name", ""),
                    race_soup=soup,
                ):
                    _horse["脚質"] = "初出走"
                    _horse["脚質表示"] = "初出走"
                    _horse["脚質取得元"] = "初出走"
            except Exception:
                pass

        # APIオッズが取得できた場合、馬番をキーに必ず反映
        if api_odds_map:
            for horse in horses:
                num = int(horse.get("馬番", 0) or 0)
                if num in api_odds_map:
                    horse["オッズ"] = api_odds_map[num]

        # 過去走から調教師・脚質を最後に補完
        for horse in horses:
            num = int(horse.get("馬番", 0) or 0)
            name = str(horse.get("馬名", ""))

            if not horse.get("調教師") and past_soup is not None:
                try:
                    prow = find_past_row(past_soup, num, name)
                    if prow is not None:
                        horse["調教師"] = extract_trainer(prow)
                except Exception:
                    pass

            if horse.get("脚質") in (None, "", "不明") and past_soup is not None:
                try:
                    past_style = extract_style_from_past_page(
                        past_soup, num, name
                    )
                except Exception:
                    past_style = "不明"
                if past_style == "不明":
                    try:
                        past_style = extract_style_from_horse_context(
                            str(past_soup), num, name
                        )
                    except Exception:
                        past_style = "不明"
                if past_style != "不明":
                    horse["脚質"] = past_style
                    horse["脚質取得元"] = "過去走"
                    horse["脚質表示"] = {
                        "逃":"逃げ", "先":"先行",
                        "差":"差し", "追":"追込"
                    }.get(past_style, past_style)

            if not horse.get("調教師"):
                horse["調教師"] = "不明"
            if not horse.get("脚質"):
                horse["脚質"] = "不明"
            if not horse.get("脚質取得元"):
                horse["脚質取得元"] = "未取得"

        # 3頭だけ等の部分取得を正常データとして扱わない
        if len(horses) <= 3:
            return None, (
                "出走馬データの取得が不完全です。"
                f"現在{len(horses)}頭しか取得できませんでした。"
                "ページ構造または取得元を確認してください。"
            )

        kyaku_count = sum(
            1 for h in horses
            if h.get("脚質") not in (None, "", "不明")
        )

        front_runner_count = sum(
            1 for h in horses
            if (
                "逃" in str(h.get("脚質", ""))
                or "先" in str(h.get("脚質", ""))
            )
        )

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
        extracted["kyaku_count"] = kyaku_count
        extracted["front_runner_count"] = front_runner_count
        extracted["odds_coverage"] = odds_coverage
        extracted["odds_status"] = odds_status

        return extracted, None

    except requests.exceptions.RequestException as e:
        return None, f"通信エラー: {str(e)}"

    except Exception as e:
        return None, f"解析エラー: {str(e)}"


def fetch_netkeiba_race_data(race_id_or_url, requested_date=None):
    race_id = extract_race_id(race_id_or_url)

    if not race_id:
        return None, (
            "有効な12桁のレースIDが見つかりません。"
        )

    requested_date_text = ""
    if requested_date is not None:
        try:
            requested_date_text = pd.Timestamp(requested_date).strftime("%Y-%m-%d")
        except Exception:
            requested_date_text = str(requested_date)
    refresh_token = datetime.now().strftime("%Y%m%d%H%M%S%f")
    return fetch_netkeiba_race_data_cached(race_id, requested_date_text, refresh_token)


# ============================================================
# 7. モデル
# ============================================================

def safe_log_multiplier(multiplier: float) -> float:
    return np.log(max(multiplier, 0.01))


def calculate_model_score(
    horse, fetched_info, track_type, distance,
    front_bias, inside_bias, outer_bias, green_belt, g1_mode,
    leading_data=None,
):
    score = 0.0
    horse_num = int(horse.get("馬番", 0) or 0)
    waku_num = int(horse.get("枠番", 0) or 0)
    jockey = normalize_person_name(horse.get("騎手", ""))
    style = str(horse.get("脚質", "不明"))
    total_horses = len(fetched_info["horses"])
    kyaku_rate = fetched_info["kyaku_count"] / max(total_horses, 1)

    feature = calculate_special_logic_features(
        horse, leading_data or {"jockey": {}, "trainer": {}},
        track_type, distance
    )

    for key in [
        "騎手評価","調教師評価","継続騎乗評価","乗り替わり評価",
        "騎手×競馬場","騎手×距離","騎手×コース",
        "調教師×競馬場","調教師×コース"
    ]:
        score += feature[key]

    if "ルメール" in jockey:
        score += safe_log_multiplier(1.04)
    elif "川田" in jockey:
        score += safe_log_multiplier(1.04)

    if front_bias and kyaku_rate >= 0.70 and style in {"逃","先"}:
        score += safe_log_multiplier(1.08)
    if inside_bias:
        if waku_num in [1,2] or (waku_num == 0 and horse_num <= 2):
            score += safe_log_multiplier(1.05)
    if outer_bias:
        if waku_num in [7,8] or (waku_num == 0 and horse_num >= total_horses-2):
            score += safe_log_multiplier(1.05)
    if track_type == "ダート" and distance == "1200m" and waku_num in [1,2]:
        score += safe_log_multiplier(1.08)
    if green_belt and horse_num == 1:
        score += safe_log_multiplier(1.06)
    if g1_mode and horse_num in [1,3,7]:
        score += safe_log_multiplier(1.03)

    return score, feature


def calculate_special_hole_score(row):
    score = 0.0
    reasons = []
    if float(row.get("モデル確率(%)",0) or 0) >= 7:
        score += 18; reasons.append("モデル確率")
    if float(row.get("Value Index",0) or 0) >= 1.20:
        score += 12; reasons.append("Value Index")
    if float(row.get("騎手評価pt",0) or 0) > 0:
        score += 12; reasons.append("騎手評価")
    if float(row.get("調教師評価pt",0) or 0) > 0:
        score += 10; reasons.append("調教師評価")
    if row.get("騎乗判定") == "継続騎乗":
        score += 8; reasons.append("継続騎乗")
    if row.get("騎乗判定") == "乗り替わり":
        score += 4; reasons.append("乗り替わり")
    for key, pts, label in [
        ("騎手×競馬場pt",8,"騎手×競馬場"),
        ("騎手×距離pt",7,"騎手×距離"),
        ("騎手×コースpt",7,"騎手×コース"),
        ("調教師×競馬場pt",5,"調教師×競馬場"),
        ("調教師×コースpt",5,"調教師×コース"),
    ]:
        if float(row.get(key,0) or 0) > 0:
            score += pts; reasons.append(label)
    if row.get("脚質") in {"逃","先"}:
        score += 5; reasons.append("前方脚質")
    return round(score,1), "、".join(reasons) if reasons else "複数特徴量"


def classify_special_horses(result_df):
    df = result_df.copy()
    odds = pd.to_numeric(df["単勝オッズ"], errors="coerce")
    df["_市場人気順"] = odds.rank(method="min", ascending=True)
    total_share = df["モデル確率(%)"].astype(float).sum()
    df["モデル評価シェア(%)"] = (
        df["モデル確率(%)"].astype(float) / total_share * 100
        if total_share > 0 else 0
    )

    vals = [calculate_special_hole_score(row) for _,row in df.iterrows()]
    df["特注穴ポイント"] = [v[0] for v in vals]
    df["特注穴根拠"] = [v[1] for v in vals]

    top4 = df[df["_市場人気順"] <= 4]
    danger = None if top4.empty else top4.sort_values(
        "モデル評価シェア(%)", ascending=True
    ).iloc[0]

    pool = df[df["_市場人気順"] > 4]
    if pool.empty:
        pool = df
    hole = pool.sort_values(
        ["特注穴ポイント","モデル確率(%)"],
        ascending=[False,False]
    ).iloc[0] if not pool.empty else None
    if hole is not None and hole["特注穴ポイント"] < 25:
        hole = None
    return df, hole, danger


def build_bet_recommendations(result_df, budget, race_context=None):
    df, hole, danger = classify_special_horses(result_df)
    df = df.sort_values("モデル確率(%)", ascending=False).reset_index(drop=True)

    axis = df.iloc[0]
    main1 = df.iloc[1] if len(df) > 1 else None
    main2 = df.iloc[2] if len(df) > 2 else None

    danger_num = int(danger["馬番"]) if danger is not None else None
    hole_num = int(hole["馬番"]) if hole is not None else None

    mains = [r for r in [main1,main2]
             if r is not None and int(r["馬番"]) != danger_num]

    singles = [int(axis["馬番"])]
    if hole_num is not None and hole_num != int(axis["馬番"]):
        singles.append(hole_num)

    quinella=[]; wide=[]
    for r in mains:
        p=f"{int(axis['馬番'])}-{int(r['馬番'])}"
        quinella.append(p); wide.append(p)
    if hole_num is not None and hole_num != danger_num:
        p=f"{int(axis['馬番'])}-{hole_num}"
        if p not in quinella:
            quinella.append(p); wide.append(p)
        if mains:
            p=f"{int(mains[0]['馬番'])}-{hole_num}"
            if p not in wide:
                wide.append(p)

    nums=[int(axis["馬番"])]
    for r in mains[:2]:
        if int(r["馬番"]) not in nums: nums.append(int(r["馬番"]))
    if hole_num is not None and hole_num not in nums: nums.append(hole_num)

    import itertools
    trio=[]
    if len(nums)>=3:
        for c in itertools.combinations(sorted(nums),3):
            if danger_num is not None and danger_num in c: continue
            trio.append("-".join(map(str,c)))

    slots=len(singles)+len(quinella)+len(wide)+len(trio)
    unit=max(100,(int(budget)//max(slots,1)//100)*100)
    total=slots*unit

    def lines(items):
        return "\n".join(f"{x} {unit}円" for x in items) or "なし"

    hole_text = f"{hole_num}番 {hole['馬名']}" if hole is not None else "なし"
    danger_text = f"{int(danger['馬番'])}番 {danger['馬名']}" if danger is not None else "なし"

    race_context = race_context or {}
    race_date = str(race_context.get("race_date") or "")
    venue = str(race_context.get("venue") or "")
    race_num = race_context.get("race_num")
    race_name = str(race_context.get("race_name") or "")
    try:
        race_num_text = f"{int(race_num)}R" if race_num is not None else ""
    except Exception:
        race_num_text = str(race_num or "")
    race_line = " ".join(x for x in [venue, race_num_text] if x)

    copy = "\n".join([
        "【JRA AI 推奨買い目】",
        f"開催日：{race_date}",
        f"レース：{race_line}",
        f"レース名：{race_name}",
        "",
        f"◎ {int(axis['馬番'])}番 {axis['馬名']}",
        f"○ {int(main1['馬番'])}番 {main1['馬名']}" if main1 is not None else "○ なし",
        f"▲ {int(main2['馬番'])}番 {main2['馬名']}" if main2 is not None else "▲ なし",
        f"☆ {hole_text}",
        f"⚠ {danger_text}",
        "",
        "【単勝】", lines(singles),
        "", "【馬連】", lines(quinella),
        "", "【ワイド】", lines(wide),
        "", "【三連複】", lines(trio),
        "", f"【合計参考額】{total}円",
    ])
    return {
        "df":df, "axis":axis, "main1":main1, "main2":main2,
        "special_hole":hole, "danger_horse":danger,
        "single":lines(singles), "quinella":lines(quinella),
        "wide":lines(wide), "trifecta_box":lines(trio),
        "copy_text":copy, "total_amount":total,
    }


def create_bet_save_id(prediction_log_id):
    raw=f"{prediction_log_id}|{datetime.now().isoformat()}|BET"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


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
if _google_drive_configured():
    st.sidebar.success("Google Drive: 設定済み")
else:
    st.sidebar.info("Google Drive: 未設定")

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
                target_race_id,
                selected_date,
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

        if fetched_info.get("race_status") == "終了":
            st.error(
                "⛔ このレースは終了済みのため、新しいモデル予想は実行できません。"
                f"（判定根拠: {fetched_info.get('race_status_reason', '結果確認')}）"
            )
        else:
            st.info(
                "🟢 このレースは予想可能状態です。"
                "結果掲載後は自動的に予想不可へ切り替わります。"
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
            "🇯🇵 JRA公式リーディング取得診断"
        ):
            lp = st.session_state.get("latest_prediction")
            if lp:
                st.write(
                    f"参照年: **{lp.get('leading_year', '不明')}年 JRA公式**"
                )
                st.write(
                    f"騎手データ: **{lp.get('leading_jockey_count', 0)}名**"
                )
                st.write(
                    f"調教師データ: **{lp.get('leading_trainer_count', 0)}名**"
                )
                if lp.get("leading_error"):
                    st.caption(f"取得メッセージ: {lp['leading_error']}")
            else:
                st.info("モデル予想を実行すると最新年のJRA公式リーディングを取得します。")

        with st.expander(
            "🔎 データ取得診断"
        ):
            st.write(
                f"脚質・初出走判定: "
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

    run_disabled = (
        fetched_info is None
        or fetched_info.get("race_status") == "終了"
    )

    if fetched_info is None:
        st.info("まず出走表を取得してください。")
    elif fetched_info.get("race_status") == "終了":
        st.info("終了済みレースではモデル予想ボタンは無効です。")

    if st.button(
        "🚀 モデル予想を実行",
        disabled=run_disabled,
        use_container_width=True,
    ):

        horses = fetched_info["horses"]

        leading_year = fetched_info["race_date"].year
        leading_data, leading_error = fetch_jra_official_leading(leading_year)

        scores = []
        feature_rows = []

        for horse in horses:
            score, feature = calculate_model_score(
                horse=horse,
                fetched_info=fetched_info,
                track_type=track_type,
                distance=final_distance,
                front_bias=front_bias,
                inside_bias=inside_bias,
                outer_bias=outer_bias,
                green_belt=green_belt,
                g1_mode=g1_mode,
                leading_data=leading_data,
            )
            scores.append(score)
            feature_rows.append(feature)

        probabilities = scores_to_probabilities(scores)
        result_rows = []

        for i, horse in enumerate(horses):
            model_probability = probabilities[i]
            odds = horse.get("オッズ")
            value_index = calculate_value_index(
                model_probability,
                odds,
                fetched_info["odds_status"],
            )
            feature = feature_rows[i]

            result_rows.append({
                "枠番": int(horse.get("枠番", 0) or 0),
                "馬番": int(horse.get("馬番", 0) or 0),
                "馬名": str(horse.get("馬名", "")),
                "騎手": str(horse.get("騎手") or "不明"),
                "調教師": str(horse.get("調教師") or "不明"),
                "斤量": float(horse["斤量"]) if horse.get("斤量") is not None else np.nan,
                "脚質": str(horse.get("脚質") or "不明"),
                "脚質取得元": str(horse.get("脚質取得元") or "未取得"),
                "単勝オッズ": float(odds) if odds is not None else np.nan,
                "モデル確率(%)": round(model_probability * 100, 2),
                "Value Index": value_index if value_index is not None else np.nan,
                "騎手順位": feature["騎手順位"],
                "調教師順位": feature["調教師順位"],
                "騎手評価pt": round(max(feature["騎手評価"], 0) * 100, 2),
                "調教師評価pt": round(max(feature["調教師評価"], 0) * 100, 2),
                "継続騎乗pt": round(max(feature["継続騎乗評価"], 0) * 100, 2),
                "乗り替わりpt": round(max(feature["乗り替わり評価"], 0) * 100, 2),
                "騎手×競馬場pt": round(max(feature["騎手×競馬場"], 0) * 100, 2),
                "騎手×距離pt": round(max(feature["騎手×距離"], 0) * 100, 2),
                "騎手×コースpt": round(max(feature["騎手×コース"], 0) * 100, 2),
                "調教師×競馬場pt": round(max(feature["調教師×競馬場"], 0) * 100, 2),
                "調教師×コースpt": round(max(feature["調教師×コース"], 0) * 100, 2),
                "騎乗判定": feature["騎乗判定"],
            })

        result_df = pd.DataFrame(result_rows)
        result_df = result_df.sort_values(
            by="モデル確率(%)", ascending=False
        ).reset_index(drop=True)

        result_df = result_df.drop_duplicates(
            subset=["馬番"],
            keep="first",
        ).reset_index(drop=True)

        bet_info = build_bet_recommendations(
            result_df,
            budget,
            race_context={
                "race_date": fetched_info["race_date"].strftime("%Y/%m/%d"),
                "venue": fetched_info["venue"],
                "race_num": fetched_info["race_num"],
                "race_name": fetched_info["race_name"],
            },
        )
        result_df = bet_info["df"]

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
            "leading_year": leading_data.get("year"),
            "leading_jockey_count": len(leading_data.get("jockey", {})),
            "leading_trainer_count": len(leading_data.get("trainer", {})),
            "leading_error": leading_error,
            "bet_info": bet_info,
            "bet_saved": False,
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

        display_df = display_df.drop(
            columns=["_市場人気順"],
            errors="ignore",
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

        st.markdown("### 🎯 レース診断・特注穴馬 / 危険な人気馬")

        bet_info = latest["bet_info"]
        special_hole = bet_info["special_hole"]
        danger_horse = bet_info["danger_horse"]

        d1, d2 = st.columns(2)

        with d1:
            st.subheader("☆ 特注穴馬")
            if special_hole is None:
                st.info("ロジックポイント25pt以上の特注穴馬はありません。")
            else:
                st.write(
                    f"**{int(special_hole['馬番'])}番 {special_hole['馬名']}**"
                )
                st.write(
                    f"特注ポイント: **{special_hole['特注穴ポイント']:.1f}pt**"
                )
                st.caption(
                    f"根拠: {special_hole['特注穴根拠']}"
                )

        with d2:
            st.subheader("⚠ 危険な人気馬")
            if danger_horse is None:
                st.info("単勝人気上位4頭を判定できません。")
            else:
                st.write(
                    f"**{int(danger_horse['馬番'])}番 {danger_horse['馬名']}**"
                )
                st.write(
                    f"市場人気: **{int(danger_horse['_市場人気順'])}番人気相当**"
                )
                st.write(
                    f"モデル評価シェア: **{danger_horse['モデル評価シェア(%)']:.2f}%**"
                )
                st.caption(
                    "単勝人気上位4頭の中でモデル評価シェアが最低の馬"
                )

        st.markdown("### 💰 推奨買い目")
        st.caption(
            f"{latest['race_date']}｜{fetched_info['venue']} {fetched_info['race_num']}R｜{latest['race_name']}"
        )
        st.code(
            bet_info["copy_text"],
            language="text",
        )
        st.write(
            f"参考買い目総額: **{bet_info['total_amount']:,}円**"
        )
        st.warning(
            "⚠️ 買い目はモデル評価を馬券形式へ変換した候補です。"
            "的中・利益・期待値を保証するものではありません。"
        )

        st.markdown("### 💾 買い目の保存")
        st.info(
            "モデル予想を実行しただけでは買い目履歴には保存されません。"
            "このボタンを押した買い目だけが JRA_Bet_History.csv に蓄積されます。"
        )

        if st.button(
            "💾 この買い目を保存",
            use_container_width=True,
            key="save_bet_button",
        ):
            bet_df = load_bet_history_df()
            save_id = create_bet_save_id(latest["prediction_log_id"])

            already_saved = (
                not bet_df.empty
                and (
                    bet_df["予測ログID"].astype(str)
                    == str(latest["prediction_log_id"])
                ).any()
            )

            if already_saved:
                st.warning(
                    "この予測ログの買い目は既に保存されています。"
                )
            else:
                new_bet = {
                    "買い目保存ID": save_id,
                    "予測ログID": latest["prediction_log_id"],
                    "レースID": latest["race_id"],
                    "レース名": latest["race_name"],
                    "開催日": latest["race_date"],
                    "競馬場": fetched_info["venue"],
                    "レース番号": fetched_info["race_num"],
                    "モデルバージョン": VERSION,
                    "保存日時": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "◎軸": f"{int(bet_info['axis']['馬番'])}番 {bet_info['axis']['馬名']}",
                    "○本線": (
                        f"{int(bet_info['main1']['馬番'])}番 {bet_info['main1']['馬名']}"
                        if bet_info["main1"] is not None else ""
                    ),
                    "▲本線": (
                        f"{int(bet_info['main2']['馬番'])}番 {bet_info['main2']['馬名']}"
                        if bet_info["main2"] is not None else ""
                    ),
                    "☆特注穴馬": (
                        f"{int(special_hole['馬番'])}番 {special_hole['馬名']}"
                        if special_hole is not None else ""
                    ),
                    "特注穴馬ポイント": (
                        special_hole["特注穴ポイント"]
                        if special_hole is not None else 0
                    ),
                    "特注穴馬根拠": (
                        special_hole["特注穴根拠"]
                        if special_hole is not None else ""
                    ),
                    "⚠危険な人気馬": (
                        f"{int(danger_horse['馬番'])}番 {danger_horse['馬名']}"
                        if danger_horse is not None else ""
                    ),
                    "危険人気馬モデル評価シェア": (
                        danger_horse["モデル評価シェア(%)"]
                        if danger_horse is not None else 0
                    ),
                    "危険人気馬判定根拠": (
                        "単勝人気上位4頭の中でモデル評価シェア最低"
                        if danger_horse is not None else ""
                    ),
                    "単勝買い目": bet_info["single"],
                    "馬連買い目": bet_info["quinella"],
                    "ワイド買い目": bet_info["wide"],
                    "馬単買い目": "",
                    "三連複買い目": bet_info["trifecta_box"],
                    "三連単買い目": "",
                    "コピペ用買い目": bet_info["copy_text"],
                    "買い目総額": bet_info["total_amount"],
                    "買い目保存フラグ": "保存済み",
                    "結果": "未確定",
                    "回収額": 0,
                    "収支": 0,
                    "メモ": "",
                }

                updated_bets = pd.concat(
                    [bet_df, pd.DataFrame([new_bet])],
                    ignore_index=True,
                )

                # 買い目を保存したレースだけ予測履歴にも保存する。
                history_df = load_history_df()
                history_exists = (
                    not history_df.empty
                    and (
                        history_df["予測ログID"].astype(str)
                        == str(latest["prediction_log_id"])
                    ).any()
                )

                if not history_exists:
                    history_record = {
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
                        "相手馬": (
                            f"{bet_info['main1']['馬名'] if bet_info['main1'] is not None else ''}, "
                            f"{bet_info['main2']['馬名'] if bet_info['main2'] is not None else ''}"
                        ).strip(", "),
                        "軸馬オッズ": latest["top_odds"],
                        "バイアス履歴": latest["bias_text"],
                        "モデルバージョン": VERSION,
                        "確定フラグ": "未確定",
                        "回収額": 0,
                        "収支": 0,
                        "メモ": "買い目保存済み",
                        "投資額": bet_info["total_amount"],
                        "オッズ状態": latest["odds_status"],
                        "オッズ取得率": latest["odds_coverage"],
                    }
                    save_history_df(
                        pd.concat(
                            [history_df, pd.DataFrame([history_record])],
                            ignore_index=True,
                        )
                    )

                if save_bet_history_df(updated_bets):
                    latest["bet_saved"] = True
                    st.session_state["latest_prediction"] = latest
                    drive_status = st.session_state.get("drive_sync_status", {})
                    bet_drive = drive_status.get(BET_CSV_FILENAME, {})
                    if _google_drive_configured() and not bet_drive.get("ok", False):
                        st.error(
                            "❌ 買い目はローカルCSVには保存されましたが、Google Drive同期に失敗しました。"
                            "画面の警告に表示された原因を確認してください。"
                        )
                    elif _google_drive_configured():
                        st.success(
                            "✅ 買い目を保存し、JRA_Bet_History.csv をGoogle Driveへ同期しました。"
                        )
                    else:
                        st.success(
                            "✅ 買い目を JRA_Bet_History.csv に保存しました。"
                        )
                    st.rerun()



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
                        _sync_result_to_related_bet(
                            target_log_id,
                            "確定",
                            input_return,
                            input_return - investment,
                            input_memo,
                        )
                        st.success(
                            "✅ 確定成績を保存しました。予測履歴と関連する買い目履歴にも結果を反映しました。"
                        )
                        st.rerun()

    st.markdown("---")
    st.subheader("🎫 保存済み買い目履歴")

    bet_df = load_bet_history_df()

    if bet_df.empty:
        st.info("保存済みの買い目はまだありません。")
    else:
        bet_amount = pd.to_numeric(
            bet_df["買い目総額"], errors="coerce"
        ).fillna(0).sum()
        bet_return = pd.to_numeric(
            bet_df["回収額"], errors="coerce"
        ).fillna(0).sum()

        bc1, bc2, bc3 = st.columns(3)
        bc1.metric("保存買い目件数", f"{len(bet_df):,}")
        bc2.metric("買い目総額", f"{int(bet_amount):,}円")
        bc3.metric("買い目収支", f"{int(bet_return - bet_amount):+,}円")

        st.dataframe(
            bet_df,
            use_container_width=True,
            hide_index=True,
        )

        st.download_button(
            label="📥 買い目履歴CSVをダウンロード",
            data=_csv_bytes_for_download(bet_df),
            file_name=BET_CSV_FILENAME,
            mime="text/csv",
            use_container_width=True,
        )

        pending = bet_df[bet_df["結果"] != "確定"].copy()
        if not pending.empty:
            st.markdown("### 📝 保存済み買い目の結果入力")
            options = [
                f"{r['買い目保存ID']} | {r['開催日']} | {r['レース名']}"
                for _, r in pending.iterrows()
            ]
            selected = st.selectbox(
                "結果を入力する買い目",
                options,
                key="bet_result_select",
            )
            pos = options.index(selected)
            row = pending.iloc[pos]

            ret = st.number_input(
                "買い目の実回収額",
                min_value=0,
                value=0,
                step=100,
                key="bet_return_input",
            )
            memo = st.text_input(
                "買い目メモ",
                value="",
                key="bet_memo_input",
            )

            if st.button(
                "💾 買い目結果を確定",
                key="confirm_bet_result",
            ):
                idx = bet_df[
                    bet_df["買い目保存ID"].astype(str)
                    == str(row["買い目保存ID"])
                ].index
                if not idx.empty:
                    i = idx[0]
                    # Ver.2.44: 結果確定前に文字列列を再正規化し、
                    # pandas 2.x の LossySetitemError を防止。
                    bet_df = normalize_bet_columns(bet_df)
                    amount = float(pd.to_numeric(bet_df.loc[i, "買い目総額"], errors="coerce") or 0)
                    bet_df.loc[i, "結果"] = "確定"
                    bet_df.loc[i, "回収額"] = float(ret)
                    bet_df.loc[i, "収支"] = float(ret) - amount
                    bet_df.loc[i, "メモ"] = str(memo or "")

                    if save_bet_history_df(bet_df):
                        _sync_result_to_related_prediction(
                            row,
                            "確定",
                            ret,
                            float(ret) - amount,
                            memo,
                        )
                        st.success("✅ 買い目結果を確定しました。予測履歴にも結果を反映しました。")
                        st.rerun()


    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
    )

    st.download_button(
        label="📥 CSVをダウンロード",
        data=_csv_bytes_for_download(df),
        file_name=CSV_FILENAME,
        mime="text/csv",
        use_container_width=True,
    )
