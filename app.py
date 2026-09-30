import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import re
import os
import csv
from datetime import datetime

# ==========================================
# ページ初期設定 (スマホ最適化)
# ==========================================
st.set_page_config(
    page_title="JRA AI競馬予想エンジン",
    page_icon="🏇",
    layout="wide",
    initial_sidebar_state="expanded"
)
import streamlit as st

# --- 簡易パスワード認証機能 ---
def check_password():
    """st.secrets に設定されたパスワードで認証を行う"""
    # secrets にパスワードが設定されていない場合の安全策
    if "APP_PASSWORD" not in st.secrets:
        st.error("Secrets に 'APP_PASSWORD' が設定されていません。")
        return False

    # セッション状態の初期化
    if "password_correct" not in st.session_state:
        st.session_state["password_correct"] = False

    # 認証済みであれば True を返す
    if st.session_state["password_correct"]:
        return True

    # ログインフォームの表示
    st.title("🔒 ログイン")
    password_input = st.text_input("パスワードを入力してください", type="password")

    if st.button("ログイン"):
        if password_input == st.secrets["APP_PASSWORD"]:
            st.session_state["password_correct"] = True
            st.rerun()  # 画面をリロードしてメイン処理へ
        else:
            st.error("❌ パスワードが正しくありません")

    return False

# 認証チェックの実行
if not check_password():
    st.stop()  # 認証未完了の場合はここで処理を一時停止

# --- ここから下に既存のメインロジック（16大分析やUI等）を記述 ---

st.title("🏇 JRA AI競馬予想エンジン 【16大分析＆自動資金配分】")

# ==========================================
# サイドバー設定 (操作フォーム)
# ==========================================
st.sidebar.header("⚙️ レース・条件設定")

開催日 = st.sidebar.date_input("開催日", datetime.now())
開催日_str = 開催日.strftime("%Y%m%d") # YYYYMMDD形式

競馬場 = st.sidebar.selectbox("競馬場", ["東京", "中山", "京都", "阪神", "小倉", "新潟", "福島", "中京", "札幌", "函館"])
レース番号 = st.sidebar.selectbox("レース番号", [f"{i}R" for i in range(1, 13)], index=10)
race_num_int = int(レース番号.replace("R", ""))

レース名 = st.sidebar.text_input("レース名", "メインレース")
コース距離 = st.sidebar.text_input("コース・距離", "芝2000m")
馬場状態 = st.sidebar.selectbox("馬場状態", ["良", "稍重", "重", "不良"])
G1レースか = st.sidebar.checkbox("G1レースか", value=True)
想定購入予算 = st.sidebar.number_input("想定購入予算 (円)", min_value=1000, max_value=1000000, value=10000, step=1000)

st.sidebar.subheader("🏁 トラックバイアス設定")
前残り傾向 = st.sidebar.checkbox("前残り傾向", value=True)
内枠有利 = st.sidebar.checkbox("内枠有利", value=True)
外差し好適 = st.sidebar.checkbox("外差し好適", value=False)
グリーンベルト発動 = st.sidebar.checkbox("グリーンベルト発動", value=False)

st.sidebar.subheader("🔮 G1サイン・オカルト設定")
ヘッドライン = st.sidebar.text_input("G1ヘッドライン", "")
ラッキーナンバー = st.sidebar.text_input("ラッキーナンバー (カンマ区切り)", "3, 7, 14")
プレゼンターサイン番 = st.sidebar.number_input("プレゼンター特注番", min_value=0, max_value=18, value=0)

Googleドライブに記録する = st.sidebar.checkbox("Googleドライブ(CSV)に自動記録", value=True)

# ==========================================
# WEBスクレイピング & リアルタイムデータ取得
# ==========================================

VENUE_CODES = {
    "札幌": "01", "函館": "02", "福島": "03", "新潟": "04", "東京": "05",
    "中山": "06", "中京": "07", "京都": "08", "阪神": "09", "小倉": "10"
}

@st.cache_data(ttl=60) # 60秒キャッシュでリアルタイムオッズ更新対応
def fetch_real_race_data(date_str, venue_name, race_num):
    """netkeiba等からリアルタイム出走表・オッズ・騎手・斤量をスクレイピング取得"""
    venue_code = VENUE_CODES.get(venue_name, "05")
    race_id = f"{date_str[:4]}{venue_code}0101{race_num:02d}" # リモートレースID生成
    url = f"https://race.netkeiba.com/race/shutuba.html?race_id={race_id}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    horses = []
    try:
        res = requests.get(url, headers=headers, timeout=10)
        res.encoding = "euc-jp"
        soup = BeautifulSoup(res.text, "html.parser")
        
        table = soup.find("table", class_="Shutuba_Table")
        if table:
            rows = table.find_all("tr", class_="HorseList")
            for row in rows:
                try:
                    # 枠番・馬番
                    gate_td = row.find("td", class_=re.compile("Waku"))
                    gate = int(gate_td.text.strip()) if gate_td and gate_td.text.strip().isdigit() else 1
                    
                    num_td = row.find("td", class_=re.compile("Umaban"))
                    num = int(num_td.text.strip()) if num_td and num_td.text.strip().isdigit() else 1
                    
                    # 馬名
                    name_element = row.find("span", class_="HorseName")
                    name = name_element.text.strip() if name_element else f"競走馬{num}"
                    
                    # 斤量
                    handicap_td = row.find("td", class_="Txt_C")
                    handicap = float(handicap_td.text.strip()) if handicap_td and handicap_td.text.strip().replace(".", "").isdigit() else 57.0
                    
                    # 騎手
                    jockey_element = row.find("td", class_="Jockey")
                    jockey = jockey_element.text.strip() if jockey_element else "未定"
                    
                    # オッズ
                    odds_element = row.find("span", id=re.compile(f"odds-{num}"))
                    odds_str = odds_element.text.strip() if odds_element else ""
                    try:
                        odds = float(odds_str)
                    except ValueError:
                        odds = 9.9 # デフォルト値
                    
                    # 脚質・適性（簡易自動推定）
                    style = "先行" if gate <= 4 else ("差し" if gate <= 12 else "追込")
                    if num in [1, 2]: style = "逃げ"

                    horses.append({
                        "num": num,
                        "gate": gate,
                        "name": name,
                        "style": style,
                        "jockey": jockey,
                        "jockey_rank": 3 if "川田" in jockey or "ルメール" in jockey or "戸崎" in jockey else 10,
                        "jockey_change": False,
                        "handicap": handicap,
                        "prev_handicap": handicap,
                        "weight": 480,
                        "prev_weight": 480,
                        "odds": odds,
                        "course_aptitude": 1.5,
                        "heavy_track_aptitude": 1.0 if 馬場状態 in ["重", "不良"] else 0.0,
                        "pace_match": 1.0,
                        "blood_score": 1.5,
                        "rotation_score": 1.0,
                        "recent_perf_score": 2.0,
                        "weight_diff": 0,
                        "prev_disadvantage": False,
                        "is_dangerous_favorite": True if (odds <= 3.0 and "追込" in style) else False,
                        "last_3f_rank": 2,
                        "jockey_trainer_tag_win_rate": 0.20
                    })
                except Exception:
                    continue
    except Exception as e:
        st.sidebar.error(f"WEB取得注意: {e}")

    # WEBからの取得が不完全な場合のエラー回避用フォールバック
    if not horses:
        st.warning("⚠️ リアルタイムデータの自動取得を試みましたが、レース前またはID未発効のため基本枠組データで計算します。")
        for i in range(1, 13):
            horses.append({
                "num": i, "gate": (i % 8) + 1, "name": f"サンプル馬{i}号", "style": "先行" if i <= 4 else "差し",
                "jockey": "騎手", "jockey_rank": 5, "jockey_change": False,
                "handicap": 57.0, "prev_handicap": 57.0, "weight": 480, "prev_weight": 480,
                "odds": round(2.5 + i * 3.1, 1), "course_aptitude": 1.0, "heavy_track_aptitude": 0.5,
                "pace_match": 1.0, "blood_score": 1.0, "rotation_score": 1.0, "recent_perf_score": 1.5,
                "weight_diff": 0, "prev_disadvantage": False, "is_dangerous_favorite": False,
                "last_3f_rank": 3, "jockey_trainer_tag_win_rate": 0.15
            })

    return horses

def calc_handicap_score(handicap, prev_handicap, weight, prev_weight):
    score = 0.0
    reasons = []
    if prev_handicap and prev_handicap > 0:
        diff = handicap - prev_handicap
        if diff <= -2.0: score += 2.0; reasons.append(f"斤量大幅減({diff:+.1f}kg:+2.0)")
        elif diff <= -1.0: score += 1.0; reasons.append(f"斤量減({diff:+.1f}kg:+1.0)")
        elif diff >= 2.0: score -= 2.0; reasons.append(f"斤量大幅増({diff:+.1f}kg:-2.0⚠️)")
        elif diff >= 1.0: score -= 1.0; reasons.append(f"斤量増({diff:+.1f}kg:-1.0)")
    return round(score, 1), reasons

def calc_frame_position_score(horse, all_horses):
    score = 0.0
    reasons = []
    my_gate = horse.get("gate", 0)
    my_style = horse.get("style", "")
    if my_style in ["逃げ", "先行"]:
        outer_front = [h for h in all_horses if h.get("gate", 0) > my_gate and h.get("style") in ["逃げ", "先行"]]
        if len(outer_front) >= 2: score -= 1.5; reasons.append("外同型包囲リスク(-1.5⚠️)")
    return score, reasons

# ==========================================
# メイン解析処理 ＆ アプリ画面描写
# ==========================================

if st.button("🚀 予想を実行する（リアルタイムデータ自動同期）", type="primary"):
    with st.spinner("WEBから最新の出走表・リアルタイムオッズを取得して16大分析を実行中..."):
        horses = fetch_real_race_data(開催日_str, 競馬場, race_num_int)
        lucky_nums = [int(n.strip()) for n in ラッキーナンバー.split(",") if n.strip().isdigit()]
        analyzed_results = []

        for h in horses:
            score = 0.0
            details = []

            score += h["course_aptitude"] + h["recent_perf_score"] + h["pace_match"] + h["blood_score"] + h["rotation_score"]

            if 前残り傾向 and h["style"] in ["逃げ", "先行"]: score += 1.5; details.append("前残り(+1.5)")
            if 内枠有利 and h["gate"] <= 2: score += 1.0; details.append("内枠有利(+1.0)")
            if 外差し好適 and h["style"] in ["差し", "追込"] and h["gate"] >= 6: score += 1.0; details.append("外差し好適(+1.0)")
            if グリーンベルト発動 and h["gate"] <= 2: score += 1.0; details.append("グリーンベルト(+1.0)")

            if 馬場状態 in ["重", "不良"]:
                score += h["heavy_track_aptitude"]
                if h["heavy_track_aptitude"] > 0: details.append(f"道悪適性(+{h['heavy_track_aptitude']})")

            if h["jockey_rank"] <= 5: score += 2.5; details.append(f"トップ騎手({h['jockey']}:+2.5)")
            elif h["jockey_rank"] <= 15: score += 1.5; details.append(f"上位騎手({h['jockey']}:+1.5)")

            if h["odds"] >= 10.0 and (h["course_aptitude"] + h["recent_perf_score"]) >= 3.0:
                score += 1.5; details.append(f"オッズ妙味(+1.5/単{h['odds']}倍)")

            if h.get("is_dangerous_favorite"): score -= 2.0; details.append("⚠️危険な人気馬(-2.0)")

            f_score, f_reasons = calc_frame_position_score(h, horses)
            score += f_score; details.extend(f_reasons)

            h_score, h_reasons = calc_handicap_score(h["handicap"], h["prev_handicap"], h["weight"], h["prev_weight"])
            score += h_score; details.extend(h_reasons)

            is_sign = False
            sign_reasons = []
            if G1レースか:
                if h["num"] in lucky_nums: is_sign = True; sign_reasons.append(f"サインNo.({h['num']})")
                if プレゼンターサイン番 > 0 and h["num"] == プレゼンターサイン番: is_sign = True; sign_reasons.append(f"プレゼンター特注({プレゼンターサイン番})")

            analyzed_results.append({
                "num": h["num"], "gate": h["gate"], "name": h["name"], "odds": h["odds"], "jockey": h["jockey"], "handicap": h["handicap"],
                "total_score": round(score, 1), "is_sign": is_sign, "sign_reasons": sign_reasons, "analysis_details": details
            })

        analyzed_results.sort(key=lambda x: x["total_score"], reverse=True)

        marks = ["◎", "○", "▲", "△", "☆"]
        for i, h in enumerate(analyzed_results): h["mark"] = marks[i] if i < len(marks) else "  "

        hole_horses = [h for h in analyzed_results if h["odds"] >= 10.0 and h["total_score"] >= 4.0]
        top_horse = analyzed_results[0]

        honmei, taikou, tanana = analyzed_results[0], analyzed_results[1], analyzed_results[2]
        sign_horses = [h for h in analyzed_results if h["is_sign"]]
        dangerous_favorite = [h for h in analyzed_results if "⚠️危険な人気馬(-2.0)" in h["analysis_details"]]

        score_gap = round(honmei["total_score"] - taikou["total_score"], 1)
        confidence_pct = int(min(98, max(40, round(50.0 + (score_gap * 10.0)))))

        if confidence_pct >= 85: confidence_label = f"Sクラス 🔥 (自信度: {confidence_pct}%)"
        elif confidence_pct >= 70: confidence_label = f"Aクラス 🎯 (自信度: {confidence_pct}%)"
        elif confidence_pct >= 55: confidence_label = f"Bクラス ⚖️ (自信度: {confidence_pct}%)"
        else: confidence_label = f"Cクラス ⚠️ (自信度: {confidence_pct}%)"

        # 買い目組み立て
        valid_opponents = [h for h in analyzed_results[1:] if "⚠️危険な人気馬(-2.0)" not in h["analysis_details"]]
        opponent_nums = [h['num'] for h in valid_opponents]

        budget_umaren = int(想定購入予算 * 0.60)
        budget_sanren = int(想定購入予算 * 0.40)

        umaren_1_amount = int(round(budget_umaren * 0.60, -2))
        umaren_2_amount = int(round(budget_umaren * 0.40, -2))
        umaren_bets = [f"馬連 {honmei['num']}-{taikou['num']} ({umaren_1_amount}円)", f"馬連 {honmei['num']}-{tanana['num']} ({umaren_2_amount}円)"]

        sanren_pts = max(1, len(opponent_nums) * (len(opponent_nums) - 1) // 2)
        per_sanren = int(max(100, round(budget_sanren / sanren_pts, -2)))
        sanrenpuku_text = f"三連複 軸:{honmei['num']}番 - 相手:{', '.join([str(n) for n in opponent_nums[:5]])} (各{per_sanren}円)"

        copy_text = f"""【AI競馬予想＆資金配分（予算:{想定購入予算:,}円）】
🗓️ {開催日_str} {競馬場}{レース番号} {レース名} (馬場:{馬場状態})
🎯 予想自信度: 【{confidence_label}】
--------------------------------
【予想印】
◎ {honmei['num']}番 {honmei['name']} ({honmei['jockey']}/単{honmei['odds']}倍)
◯ {taikou['num']}番 {taikou['name']}
▲ {tanana['num']}番 {tanana['name']}
--------------------------------
【推奨買い目 ＆ 資金配分】
■ 本線: {', '.join(umaren_bets)}
■ 押さえ: {sanrenpuku_text}
--------------------------------
#JRA競馬予想 #AI予想 #リアルタイム分析"""

        # 画面出力
        st.subheader(f"📊 予想結果: {開催日_str} {競馬場}{レース番号} {レース名}")
        st.info(f"🎯 **予想自信度**: {confidence_label}")

        df_display = []
        for h in analyzed_results:
            df_display.append({
                "印": h["mark"],
                "馬番": h["num"],
                "枠番": h["gate"],
                "馬名": h["name"],
                "騎手/斤量": f"{h['jockey']} ({h['handicap']}kg)",
                "最新オッズ": f"{h['odds']}倍",
                "総合スコア": h["total_score"],
                "分析根拠": " / ".join(h["analysis_details"])
            })

        st.table(pd.DataFrame(df_display))

        st.subheader("📋 LINE / IPAT用 1タップコピペテキスト")
        st.code(copy_text, language="text")

        # CSV保存
        if Googleドライブに記録する:
            try:
                csv_file_path = "JRA_Prediction_History.csv"
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                row = [now_str, 開催日_str, 競馬場, レース番号, レース名, f"{honmei['num']}番 {honmei['name']}", f"{taikou['num']}番 {taikou['name']}", f"{tanana['num']}番 {tanana['name']}", copy_text.replace("\n", " ")]
                file_exists = os.path.exists(csv_file_path)
                with open(csv_file_path, mode="a", encoding="utf-8-sig", newline="") as f:
                    writer = csv.writer(f)
                    if not file_exists: writer.writerow(["日時","開催日","競馬場","レース","レース名","本命","対抗","単穴","出力"])
                    writer.writerow(row)
                st.success("✅ CSV履歴ファイルへ記録しました！")
            except Exception as e:
                st.warning(f"⚠️ CSV保存注意: {e}")
