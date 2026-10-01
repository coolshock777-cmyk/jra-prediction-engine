import streamlit as st
import pandas as pd
import numpy as np
import datetime
import os
import requests
from bs4 import BeautifulSoup
import re

# ---------------------------------------------------------
# 1. 簡易パスワード認証機能
# ---------------------------------------------------------
def check_password():
    if "APP_PASSWORD" not in st.secrets:
        st.error("Secrets に 'APP_PASSWORD' が設定されていません。")
        return False

    if "password_correct" not in st.session_state:
        st.session_state["password_correct"] = False

    if st.session_state["password_correct"]:
        return True

    st.title("🔒 ログイン")
    password_input = st.text_input("パスワードを入力してください", type="password")

    if st.button("ログイン"):
        if password_input == st.secrets["APP_PASSWORD"]:
            st.session_state["password_correct"] = True
            st.rerun()
        else:
            st.error("❌ パスワードが正しくありません")

    return False

if not check_password():
    st.stop()


# ---------------------------------------------------------
# 2. メニュー選択（サイドバー）
# ---------------------------------------------------------
st.sidebar.title("🏇 JRA Prediction Engine")
app_mode = st.sidebar.radio(
    "機能選択",
    ["🏇 リアルタイム予想", "📊 成績ダッシュボード・結果入力"]
)

CSV_FILE_PATH = "JRA_Prediction_History.csv"

# 全必須カラムの定義（旧フォーマット互換用）
REQUIRED_COLUMNS = [
    'レースID', '予想日時', '開催日', '競馬場', 'レース番号', 'コース種別', '馬場状態', 'G1モード',
    '本命(◎)', '本命AIシェア', '本命AI価値指数', '対抗(◯)', '単穴(▲)', '特注(☆)', '危険人気馬',
    'サイン馬番', 'プレゼンターメモ', 'ヘッドライン', '投資額', '回収額', '収支', 'ステータス', 'メモ', 'LINE/IPAT出力テキスト'
]

# ---------------------------------------------------------
# 3. 📊 成績ダッシュボード・結果更新画面
# ---------------------------------------------------------
if app_mode == "📊 成績ダッシュボード・結果入力":
    st.header("📊 予想成績ダッシュボード & 結果更新")
    
    if os.path.exists(CSV_FILE_PATH):
        try:
            df = pd.read_csv(CSV_FILE_PATH)
            if df.empty:
                st.info("まだ予想履歴データ（JRA_Prediction_History.csv）が空です。")
            else:
                # 不足カラムの自動補完・修復
                for col in REQUIRED_COLUMNS:
                    if col not in df.columns:
                        if col in ['回収額', '収支', '投資額', '本命AIシェア', '本命AI価値指数']:
                            df[col] = 0
                        elif col == 'ステータス':
                            df[col] = '未確定'
                        else:
                            df[col] = ''

                df['回収額'] = pd.to_numeric(df['回収額'], errors='coerce').fillna(0)
                df['収支'] = pd.to_numeric(df['収支'], errors='coerce').fillna(0)
                df['投資額'] = pd.to_numeric(df['投資額'], errors='coerce').fillna(0)
                df['本命AIシェア'] = pd.to_numeric(df['本命AIシェア'], errors='coerce').fillna(0)
                df['本命AI価値指数'] = pd.to_numeric(df['本命AI価値指数'], errors='coerce').fillna(0)

                confirmed_df = df[df['ステータス'] == '確定済']
                total_races = len(confirmed_df)
                total_invest = confirmed_df['投資額'].sum()
                total_return = confirmed_df['回収額'].sum()
                total_balance = confirmed_df['収支'].sum()
                recovery_rate = (total_return / total_invest * 100) if total_invest > 0 else 0
                hit_races = len(confirmed_df[confirmed_df['回収額'] > 0])
                hit_rate = (hit_races / total_races * 100) if total_races > 0 else 0

                col1, col2, col3, col4 = st.columns(4)
                col1.metric("確定レース数", f"{total_races} 件")
                col2.metric("通算回収率", f"{recovery_rate:.1f} %", delta=f"{total_balance:+,.0f}円")
                col3.metric("通算的中率", f"{hit_rate:.1f} %", f"{hit_races}/{total_races}レース")
                col4.metric("総収支", f"{total_balance:+,.0f} 円")

                st.markdown("---")
                st.subheader("📝 レース結果・払戻金の入力")
                
                unconfirmed_indices = df[df['ステータス'] != '確定済'].index
                if len(unconfirmed_indices) > 0:
                    selected_idx = st.selectbox(
                        "結果を入力するレースを選択してください",
                        options=unconfirmed_indices,
                        format_func=lambda i: f"[{df.loc[i, '開催日']}] {df.loc[i, '競馬場']}{df.loc[i, 'レース番号']} - 本命:{df.loc[i, '本命(◎)']}"
                    )
                    
                    col_p1, col_p2 = st.columns(2)
                    with col_p1:
                        return_amount = st.number_input("実際の払戻・回収金額 (円)", value=0, step=100)
                    with col_p2:
                        memo = st.text_input("反省・回顧メモ", "")

                    if st.button("💾 結果・成績を確定更新"):
                        df.loc[selected_idx, '回収額'] = return_amount
                        df.loc[selected_idx, '収支'] = return_amount - df.loc[selected_idx, '投資額']
                        df.loc[selected_idx, 'ステータス'] = '確定済'
                        if memo:
                            df.loc[selected_idx, 'メモ'] = memo
                        
                        df.to_csv(CSV_FILE_PATH, index=False, encoding='utf-8-sig')
                        st.success("✅ 的中・回収成績を更新しました！")
                        st.rerun()
                else:
                    st.info("全ての予想ログが確定済みです。")

                st.markdown("---")
                st.subheader("📋 全予想ログ一覧")
                st.dataframe(df.sort_index(ascending=False), use_container_width=True)

        except Exception as e:
            st.error(f"データ処理エラー: {e}")
    else:
        st.warning("⚠️ 予想履歴データが見つかりません。")

    st.stop()


# ---------------------------------------------------------
# 4. ⚡ 高速・高精度ピンポイントスクレイピング & スコアリング
# ---------------------------------------------------------
PLACE_MAP = {
    "札幌": "01", "函館": "02", "福島": "03", "新潟": "04", "東京": "05",
    "中山": "06", "中京": "07", "京都": "08", "阪神": "09", "小倉": "10"
}

TOP_JOCKEYS = ["ルメール", "川田", "武豊", "横山武", "戸崎", "坂井", "松山", "モレイラ", "レーン", "デムーロ"]

@st.cache_data(ttl=60)
def fetch_and_predict_ml(date_obj, place_name, race_num_str, course_type, track_cond, is_g1_mode, bias_params, lucky_nums_str, presenter_note_str, g1_headline_str):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    year = date_obj.strftime("%Y")
    date_str = date_obj.strftime("%Y%m%d")
    place_code = PLACE_MAP.get(place_name, "05")
    race_r = race_num_str.replace("R", "").zfill(2)
    
    horses_list = []
    
    # 正規表現を用いた race_id ピンポイント抽出
    target_race_id = None
    list_url = f"https://race.netkeiba.com/top/race_list.html?kaisai_date={date_str}"
    try:
        res_list = requests.get(list_url, headers=headers, timeout=2.0)
        res_list.encoding = 'euc-jp'
        if res_list.status_code == 200:
            soup_list = BeautifulSoup(res_list.text, "html.parser")
            for a_tag in soup_list.find_all("a", href=True):
                href = a_tag["href"]
                if "shutuba.html" in href:
                    match = re.search(r'race_id=(\d+)', href)
                    if match:
                        candidate_id = match.group(1)
                        if candidate_id.startswith(f"{year}{place_code}") and candidate_id.endswith(race_r):
                            target_race_id = candidate_id
                            break
    except Exception:
        pass

    # 軽量化フォールバック試行（最小18パターン）
    candidate_ids = [target_race_id] if target_race_id else [
        f"{year}{place_code}{kai:02d}{nichi:02d}{race_r}" for kai in range(1, 4) for nichi in range(1, 7)
    ]

    for race_id in candidate_ids:
        if not race_id:
            continue
        url = f"https://race.netkeiba.com/race/shutuba.html?race_id={race_id}"
        try:
            res = requests.get(url, headers=headers, timeout=1.5)
            res.encoding = 'euc-jp'
            if res.status_code == 200 and "Shutuba_Table" in res.text:
                soup = BeautifulSoup(res.text, "html.parser")
                table = soup.find("table", class_="Shutuba_Table")
                if table:
                    rows = table.find_all("tr", class_="HorseList")
                    for row in rows:
                        try:
                            umaban_elem = row.find("td", class_="Umaban")
                            umaban = int(umaban_elem.text.strip()) if umaban_elem and umaban_elem.text.strip().isdigit() else 0
                            
                            horse_elem = row.find("span", class_="HorseName")
                            horse_name = horse_elem.text.strip() if horse_elem else ""
                            
                            jockey_elem = row.find("td", class_="Jockey")
                            jockey_name = jockey_elem.text.strip().replace("\n", "") if jockey_elem else "未定"
                            
                            odds_elem = row.find("td", class_="Popular")
                            odds_val = None
                            if odds_elem:
                                odds_txt = odds_elem.text.strip()
                                try:
                                    odds_val = float(odds_txt)
                                except ValueError:
                                    odds_val = None

                            if umaban > 0 and horse_name and odds_val is not None and odds_val > 0:
                                horses_list.append({
                                    "umaban": umaban,
                                    "name": horse_name,
                                    "jockey": jockey_name,
                                    "odds": odds_val
                                })
                        except Exception:
                            continue
                    if len(horses_list) > 0:
                        break
        except Exception:
            continue

    if not horses_list:
        return {
            "status": "not_ready",
            "message": f"{date_obj} {place_name}{race_num_str} の出馬表データがまだ確定前か未取得です。確定後に再度お試しください。"
        }

    # ---------------------------------------------------------
    # 多特徴量スコアリング ＆ モデル評価シェア算出
    # ---------------------------------------------------------
    lucky_list = [int(x.strip()) for x in lucky_nums_str.split(",") if x.strip().isdigit()]
    presenter_nums = [int(n) for n in re.findall(r'\d+', presenter_note_str)]
    headline_nums = [int(n) for n in re.findall(r'\d+', g1_headline_str)]

    raw_scores = []

    for h in horses_list:
        feat_odds = 1.0 / (np.log(h["odds"] + 1.0) + 0.1)
        feat_jockey = 1.15 if any(j in h["jockey"] for j in TOP_JOCKEYS) else 1.0
        
        feat_bias = 1.0
        if track_cond in ["重", "不良"]:
            if h["umaban"] in [1, 2, 3]:
                feat_bias *= 0.90
            elif h["umaban"] >= 10:
                feat_bias *= 1.10

        if "ダ" in course_type and "1200m" in course_type and h["umaban"] <= 4:
            feat_bias *= 1.10

        if bias_params["mae"] and h["umaban"] in [1, 2, 3, 4]:
            feat_bias *= 1.08
        if bias_params["uchi"] and h["umaban"] <= 4:
            feat_bias *= 1.05
        if bias_params["sashi"] and h["umaban"] >= 8:
            feat_bias *= 1.05
            
        if bias_params["green"] and h["umaban"] in [2, 3, 4, 5]:
            feat_bias *= 1.06

        sign_matched = False
        if is_g1_mode:
            if h["umaban"] in lucky_list:
                feat_bias *= (1.0 + bias_params["sign_weight"] * 0.08)
                sign_matched = True
            if h["umaban"] in presenter_nums:
                feat_bias *= 1.06
                sign_matched = True
            if h["umaban"] in headline_nums:
                feat_bias *= 1.05
                sign_matched = True

        total_rating = feat_odds * feat_jockey * feat_bias
        raw_scores.append(total_rating)

    exp_scores = np.exp(np.array(raw_scores) - np.max(raw_scores))
    ai_shares = exp_scores / np.sum(exp_scores)

    analyzed_horses = []
    for i, h in enumerate(horses_list):
        share_pct = ai_shares[i] * 100
        val_index = round((ai_shares[i] * h["odds"]), 2)
        
        h_copy = h.copy()
        h_copy["ai_share"] = round(share_pct, 1)
        h_copy["val_index"] = val_index
        h_copy["sign_matched"] = is_g1_mode and (h["umaban"] in lucky_list or h["umaban"] in presenter_nums or h["umaban"] in headline_nums)
        analyzed_horses.append(h_copy)

    analyzed_horses.sort(key=lambda x: x["ai_share"], reverse=True)
    return {"status": "success", "data": analyzed_horses}


# 確定成績を保護し全ログ項目を安全更新するロジック
def save_prediction_to_csv(log_data):
    try:
        race_id_key = log_data["レースID"]
        
        if os.path.exists(CSV_FILE_PATH):
            df = pd.read_csv(CSV_FILE_PATH)
            
            for col in REQUIRED_COLUMNS:
                if col not in df.columns:
                    df[col] = 0 if col in ['回収額', '収支', '投資額', '本命AIシェア', '本命AI価値指数'] else ('未確定' if col == 'ステータス' else '')

            if "レースID" in df.columns and race_id_key in df["レースID"].values:
                idx = df[df["レースID"] == race_id_key].index[0]
                
                update_columns = [
                    "予想日時", "コース種別", "馬場状態", "G1モード", 
                    "本命(◎)", "本命AIシェア", "本命AI価値指数", 
                    "対抗(◯)", "単穴(▲)", "特注(☆)", "危険人気馬",
                    "サイン馬番", "プレゼンターメモ", "ヘッドライン", "LINE/IPAT出力テキスト"
                ]
                for k in update_columns:
                    if k in log_data:
                        df.loc[idx, k] = log_data[k]
                        
                if df.loc[idx, "ステータス"] != "確定済":
                    df.loc[idx, "投資額"] = log_data["投資額"]
                    df.loc[idx, "回収額"] = log_data["回収額"]
                    df.loc[idx, "収支"] = log_data["収支"]
                    df.loc[idx, "ステータス"] = log_data["ステータス"]

                df.to_csv(CSV_FILE_PATH, index=False, encoding='utf-8-sig')
                return "updated"
            else:
                df_new = pd.DataFrame([log_data])
                df_new.to_csv(CSV_FILE_PATH, mode='a', header=False, index=False, encoding='utf-8-sig')
                return "created"
        else:
            df_new = pd.DataFrame([log_data])
            df_new.to_csv(CSV_FILE_PATH, index=False, encoding='utf-8-sig')
            return "created"
    except Exception as e:
        st.error(f"CSV保存エラー: {e}")
        return "error"


# ---------------------------------------------------------
# 5. 🏇 リアルタイム予想メイン画面
# ---------------------------------------------------------
st.title("🏇 JRA AI予想エンジン")
st.caption("多特徴量ルールベース予測エンジン / モデル評価シェア ＆ AI価値指数算出 / 資金配分 / 的中検証機能")

st.subheader("📅 レース情報 & 投資設定")

col_r1, col_r2, col_r3, col_r4 = st.columns(4)
with col_r1:
    race_date = st.date_input("開催日", datetime.date.today())
with col_r2:
    place = st.selectbox("競馬場", ["東京", "中山", "阪神", "京都", "中京", "小倉", "新潟", "福島", "札幌", "函館"])
with col_r3:
    race_num = st.selectbox("レース番号", [f"{i}R" for i in range(1, 13)], index=10)
with col_r4:
    budget = st.number_input("💰 投資予算 (円)", value=10000, min_value=100, step=1000)

col_c1, col_c2 = st.columns(2)
with col_c1:
    course_type = st.selectbox("コース種別・距離", ["芝1200m", "芝1600m", "芝2000m", "芝2400m", "ダ1200m", "ダ1800m", "その他"])
with col_c2:
    track_condition = st.selectbox("馬場状態", ["良", "稍重", "重", "不良"])

is_g1 = st.checkbox("🏆 G1レースモード（サイン・ヘッドライン参照発動）", value=False)

sign_weight = 1.5
lucky_number = "3, 7, 14"
g1_headline = ""
presenter_note = ""
if is_g1:
    st.subheader("🔮 G1オカルトサイン分析設定")
    col_g1, col_g2 = st.columns(2)
    with col_g1:
        g1_headline = st.text_input("G1ヘッドライン (サイン分析連動)", "時代を創る絶対王者が伝統の盾を掴む")
        lucky_number = st.text_input("ラッキーナンバー・特注馬番", "3, 7, 14")
    with col_g2:
        presenter_note = st.text_input("プレゼンター・イベント特注サイン (例: 3, 8)", "3, 8")
        sign_weight = st.slider("サインスコア重み付け補正", 0.0, 3.0, 1.5)

st.subheader("⚙️ トラックバイアス・傾向補正")
col_b1, col_b2, col_b3, col_b4 = st.columns(4)
with col_b1:
    bias_mae = st.checkbox("前残り傾向 (×1.08)", value=True)
with col_b2:
    bias_uchi = st.checkbox("内枠有利 (×1.05)", value=True)
with col_b3:
    bias_sashi = st.checkbox("外差し有利 (×1.05)")
with col_b4:
    bias_green = st.checkbox("グリーンベルト (×1.06)", value=True)

bias_params = {
    "mae": bias_mae,
    "uchi": bias_uchi,
    "sashi": bias_sashi,
    "green": bias_green,
    "sign_weight": sign_weight
}

if st.button("🚀 多特徴量予測エンジン実行・予想計算スタート", type="primary"):
    with st.spinner(f"🌐 出馬表取得 & 多特徴量スコアリング評価中 ({race_date} {place}{race_num})..."):
        res = fetch_and_predict_ml(
            race_date, place, race_num, course_type, track_condition, is_g1, bias_params, lucky_number, presenter_note, g1_headline
        )

    if res.get("status") == "success":
        horses = res["data"]
        st.success("✅ 実走データ取得・モデル評価シェア ＆ AI価値指数の計算が完了しました！")

        honmei = horses[0] if len(horses) > 0 else {"umaban": "-", "name": "-", "ai_share": 0, "val_index": 0}
        taikou = horses[1] if len(horses) > 1 else {"umaban": "-", "name": "-", "ai_share": 0, "val_index": 0}
        tanana = horses[2] if len(horses) > 2 else {"umaban": "-", "name": "-", "ai_share": 0, "val_index": 0}
        tokuchu = horses[3] if len(horses) > 3 else {"umaban": "-", "name": "-", "ai_share": 0, "val_index": 0}

        popular_top4 = sorted(horses, key=lambda x: x["odds"])[:4]
        kiken_horse = sorted(popular_top4, key=lambda x: x["ai_share"])[0] if len(popular_top4) > 0 else horses[-1]

        is_w_match = honmei.get("sign_matched", False)

        st.markdown("---")
        col_res1, col_res2 = st.columns(2)

        with col_res1:
            st.markdown("### 🎯 予想印 ＆ モデル評価シェア")
            st.markdown(f"""
            * **◎ 本命:** **{honmei['umaban']}番 {honmei['name']}** (モデル評価シェア: **{honmei['ai_share']}%** / AI価値指数: **{honmei['val_index']}**) 
            * **◯ 対抗:** **{taikou['umaban']}番 {taikou['name']}** (モデル評価シェア: {taikou['ai_share']}% / AI価値指数: {taikou['val_index']})
            * **▲ 単穴:** **{tanana['umaban']}番 {tanana['name']}** (モデル評価シェア: {tanana['ai_share']}% / AI価値指数: {tanana['val_index']})
            * **☆ 特注穴馬:** **{tokuchu['umaban']}番 {tokuchu['name']}** (モデル評価シェア: {tokuchu['ai_share']}% / AI価値指数: {tokuchu['val_index']})
            * **⚠️ 危険な人気馬:** **{kiken_horse['umaban']}番 {kiken_horse['name']}** (単勝{kiken_horse['odds']}倍 / 人気上位4頭の中でモデル評価シェア最低)
            """)
            st.caption("※ AI価値指数: モデル内の相対評価シェアと単勝オッズを乗じた独自参考指標（1.00超はモデル評価が市場オッズより相対的に高く妙味がある傾向を示す数値）。")

        with col_res2:
            st.markdown("### 🔥 G1サイン & 勝負馬判定")
            if is_g1:
                w_str = f"🔥 **【W勝負馬成立！】**: **{honmei['umaban']}番 {honmei['name']}**" if is_w_match else "該当なし（本命馬とサイン不一致）"
                st.markdown(f"""
                * {w_str}
                * 🔮 **【サイン指定馬番】**: {lucky_number}
                * 📝 **【イベントサインメモ】**: {presenter_note}
                * 📰 **ヘッドライン分析**: `{g1_headline}`
                """)
            else:
                st.info("G1モード OFF")

        alloc_1 = int(budget * 0.50)
        alloc_2 = int(budget * 0.30)
        alloc_3 = int(budget * 0.20)

        st.subheader(f"💰 推奨買い目・資金自動配分 (総予算: {budget:,}円)")
        st.markdown(f"""
        | 賭け式 | 組合せ | 資金配分(%) | 推奨購入額 |
        | :--- | :--- | :--- | :--- |
        | **馬連** | **{honmei['umaban']} - {taikou['umaban']}** | **50%** | **{alloc_1:,}円** |
        | **馬連** | **{honmei['umaban']} - {tanana['umaban']}** | **30%** | **{alloc_2:,}円** |
        | **ワイド** | **{honmei['umaban']} - {tokuchu['umaban']}** | **20%** | **{alloc_3:,}円** |
        """)

        g1_str = f"【G1ヘッドライン: {g1_headline}】\n" if is_g1 else ""
        copy_text = f"""【AI競馬予想＆買い目配信】
📅 開催日: {race_date} {place}{race_num} ({course_type} 馬場:{track_condition})
💰 投資予算: {budget:,}円
{g1_str}--------------------------------
【予想印】
◎ {honmei['umaban']}番 {honmei['name']} (評価:{honmei['ai_share']}% Val指数:{honmei['val_index']})
◯ {taikou['umaban']}番 {taikou['name']}
▲ {tanana['umaban']}番 {tanana['name']}
☆ {tokuchu['umaban']}番 {tokuchu['name']}
⚠️ 危険な人気馬: {kiken_horse['umaban']}番 {kiken_horse['name']}
--------------------------------
【推奨買い目】
馬連: {honmei['umaban']}-{taikou['umaban']} ({alloc_1:,}円)
馬連: {honmei['umaban']}-{tanana['umaban']} ({alloc_2:,}円)
ワイド: {honmei['umaban']}-{tokuchu['umaban']} ({alloc_3:,}円)
--------------------------------
#JRA競馬予想 #AI予想"""

        st.subheader("📋 1タップコピペ用テキスト")
        st.code(copy_text, language="text")

        race_id_key = f"{race_date.strftime('%Y%m%d')}_{place}_{race_num}"
        log_entry = {
            "レースID": race_id_key,
            "予想日時": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "開催日": str(race_date),
            "競馬場": place,
            "レース番号": race_num,
            "コース種別": course_type,
            "馬場状態": track_condition,
            "G1モード": "ON" if is_g1 else "OFF",
            "本命(◎)": f"{honmei['umaban']}番 {honmei['name']}",
            "本命AIシェア": honmei['ai_share'],
            "本命AI価値指数": honmei['val_index'],
            "対抗(◯)": f"{taikou['umaban']}番 {taikou['name']}",
            "単穴(▲)": f"{tanana['umaban']}番 {tanana['name']}",
            "特注(☆)": f"{tokuchu['umaban']}番 {tokuchu['name']}",
            "危険人気馬": f"{kiken_horse['umaban']}番 {kiken_horse['name']}",
            "サイン馬番": lucky_number if is_g1 else "",
            "プレゼンターメモ": presenter_note if is_g1 else "",
            "ヘッドライン": g1_headline if is_g1 else "",
            "投資額": budget,
            "回収額": 0,
            "収支": -budget,
            "ステータス": "未確定",
            "LINE/IPAT出力テキスト": copy_text
        }
        
        save_status = save_prediction_to_csv(log_entry)
        if save_status == "updated":
            st.info("ℹ️️ 既存レースの予想情報を最新に更新しました（確定済みの成績・投資額データは保護されます）。")
        elif save_status == "created":
            st.caption("✅ 新規予想ログを保存しました。「成績ダッシュボード」からレース終了後の払戻金を入力して回収率を更新できます。")

    elif res.get("status") == "not_ready":
        st.warning(f"⚠️ {res.get('message')}")
    else:
        st.error(f"データ取得・解析エラー: {res.get('message')}")
