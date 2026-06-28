# ADR 006 — 冬の光環境スクリーニング: 結論と最終構成

**Date**: 2026-06-28
**Status**: Accepted（Phase 2 の到達点。winter brightness = 主軸 / dark-spell = red-flag overlay）
**関連**: [004 v2スコア](004_phase2-scoring-v2.md) / [005 GHI推計](005_ghi-estimation-rejected.md) / [実験ノート](../experiments/2026-06-28_daily-volatility.md)

---

## 経緯（このフェーズで通った道）
1. **v2スコア(004)**: 平年値ベース。winter_floor(飽和) + 春/秋レート + excessTV + amplitude を絶対アンカー非補償(Chebyshev)で。道東が最上位に出た。
2. **GHI三角測量(005)**: 全天日射量は実測48点のみ。自前A–P推計は高地外挿で不採用。NASA POWER(衛星・独立)とNEDO METPV(温度+ML)を取得 → **盆地の"明るい冬"を日照/NEDO/NASAの3系統で独立確認**。NASAは~100km格子で盆地を過小評価(松本)・局所変動を平滑化、と判明。
3. **実日別 volatility**: JMA obsdl の実観測日別(1991-2020)で、平年値が消す「連続曇天」を測定。

## 確定した結論
- **winter brightness（冬の絶対的明るさ）が master 軸**。日照・NEDO・NASA・実日別の4系統で一致。沖縄/南島は曇り冬で脱落、日本海側は最悪。
- **「変化率(delta, 一階差分|Δ|)」仮説は地域選別の判別力がほぼ無い**（|Δ|は全地域フラット）。当初仮説は実日別で**棄却寄り**に更新。
- 効いたのは**低光状態の"持続"（連続曇天）**。ただし `winter_floor` と r=−0.88（約77%重複）＝独立な新次元ではなく主に明るさの補強。
- **dark_spell は スコアの重み項にしない → red-flag overlay**（CVaR80, Nov-Mar, 閾値2h。緑≤5日/赤≥10日）。重み付けは臨床アウトカム不在で恣意的＋二重計上になるため。
- **「道東最強はアーティファクト」は部分撤回**: ロバスト統計(CVaR80)で帯広・釧路は good core に残る(5/5)。根室のみ弱い(0/5, v2#2だが黄band)。
- **確証バイアスは回避できた**: consult合意の手法(事前登録・risk band・過適合検出)で、移住第一候補の松本は **good だが borderline(4/5)**。諏訪/飯田/甲府/静岡の方が頑健＝「松本最強」は作られなかった。

## 決定的に使える成果
- **避ける（RED 42地点）**: 日本海側・北海道西岸・冬曇り南島（寿都/留萌/深浦/稚内/秋田/輪島/金沢/新潟/与那国 等）。冬2-3h・最長2週間連続無日照。**最高確信度の除外。**
- **候補（GREEN 頑健core, 23地点）**: 諏訪/飯田/甲府/静岡/名古屋/帯広/釧路/前橋/熊谷/浜松/岐阜/宮古/仙台… ＋ 松本(border)。Pacific側+中部内陸+道東の連続気候帯。

## 最終構成（成果物）
- スコア: v2(平年値, floor中心・非補償)。UIにv1/v2切替+重みスライダー+Pareto★。
- overlay: 実日別の `dark_spell`/`winter_obs`/`interann_cv`/`risk_band` を data.json に付与、UIで band表示・map/scatter選択可（**スコア外のred-flag**）。
- データ源: JMA平年値 + JMA日別観測 + NASA POWER + NEDO METPV(盆地)。生日別/NEDO生CSVはライセンス/容量で gitignore、派生のみ commit。

## 提示方針（consult: 単一スカラーに依存しない）
①hard filter(医療アクセス/生活/雪/暑熱/仕事) → ②冬明るさ + red-flag → ③候補dossier(冬分布・連続暗カレンダー)。**気候スクリーニングであって治療/安全判断の代替ではない**（冬の低光が本人にどれだけ効くかは主治医と別途）。

## 今後（未着手）
- hard-filter 層（医療アクセス・生活コスト・積雪・暑熱・交通）の統合。
- 候補地 dossier（冬の日別分布図・連続暗カレンダー）。
- dark_spell の全天日射量版（clear-sky比）での検証 subset。
